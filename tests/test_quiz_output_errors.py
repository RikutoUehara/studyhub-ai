"""Malformed provider output must not escape the quiz request or leave cleanup deferred."""
import gc
import json
import unittest
from unittest.mock import patch
from uuid import uuid4
from types import SimpleNamespace

import test_stream_persistence as fixtures
from app import app, db, QuizAttempt


QUESTION = {'type': 'true_false', 'question': 'Name the protocol.',
            'answer': True, 'explanation': 'HTTP connects web clients and servers.'}


class TrackedStream:
    def __init__(self, output, completed=True):
        self.events = iter(fixtures.sdk_events(output, completed=completed))
        self.closed = False
        self.close_count = 0

    def __iter__(self):
        return self

    def __next__(self):
        return next(self.events)

    def close(self):
        self.closed = True
        self.close_count += 1
        self.events.close()


class QuizOutputErrorTests(unittest.TestCase):
    setUp = fixtures.StreamPersistenceTests.setUp
    tearDown = fixtures.StreamPersistenceTests.tearDown
    authenticated_client = fixtures.StreamPersistenceTests.authenticated_client
    workspace_url = fixtures.StreamPersistenceTests.workspace_url

    def post_output(self, output, completed=True):
        source = TrackedStream(output, completed=completed)
        with patch('app.client.responses.create', return_value=source) as provider:
            response = self.client.post(
                f'/lecture/{self.lecture_id}/quiz/generate', buffered=True,
                headers={'X-CSRF-Token': self.csrf, 'Idempotency-Key': str(uuid4())},
            )
        self.assertEqual(provider.call_count, 1)
        self.assertTrue(source.closed)
        self.assertEqual(source.close_count, 1)
        return response

    def assert_rejected(self, output):
        response = self.post_output(output)
        self.assertEqual(response.status_code, 502)
        self.assertFalse(response.json['success'])
        self.assertIn('生成', response.json['error'])
        self.assertNotIn('private-model-content', response.get_data(as_text=True))
        with app.app_context():
            models = app.extensions['usage_ledger'].m
            self.assertTrue(all(row.status == 'completed' and not row.active
                                for row in db.session.query(models['Request']).all()))
            month = db.session.query(models['Month']).one()
            self.assertEqual((month.reserved, month.uncertain), (0, 0))
            self.assertEqual(QuizAttempt.query.count(), 0)
        usage = self.client.get('/api/ai-usage')
        self.assertEqual(usage.status_code, 200)
        self.assertTrue(usage.json['features']['quiz']['available'])

    def test_invalid_json_and_non_object_output_are_recoverable(self):
        for line in ('{private-model-content', 'null', '[]', '"private-model-content"', '42'):
            with self.subTest(line=line):
                self.assert_rejected('\n'.join([line] * 10))

    def test_invalid_question_fields_are_recoverable(self):
        cases = [
            {**QUESTION, 'question': ''}, {**QUESTION, 'question': []},
            {key: value for key, value in QUESTION.items() if key != 'explanation'},
            {**QUESTION, 'type': 'unknown'},
            {**QUESTION, 'type': 'keyword', 'answer': 'HTTP'}, {**QUESTION, 'answer': ['HTTP']},
            {**QUESTION, 'type': 'true_false', 'answer': 'true'},
            {**QUESTION, 'type': 'multiple_choice', 'choices': ['A'] * 4, 'answer': 'A'},
            {**QUESTION, 'type': 'multiple_choice', 'choices': ['A', 'B', 'C', 'D'], 'answer': 'Z'},
        ]
        for question in cases:
            with self.subTest(question=question):
                self.assert_rejected('\n'.join([json.dumps(question)] * 10))

    def test_bad_last_question_never_sends_a_partial_quiz(self):
        output = '\n'.join([json.dumps(QUESTION)] * 9 + ['{private-model-content'])
        self.assert_rejected(output)

    def test_wrong_question_count_is_rejected(self):
        for count in (0, 9, 11):
            with self.subTest(count=count):
                self.assert_rejected('\n'.join([json.dumps(QUESTION)] * count))

    def test_success_after_format_error_does_not_wait_or_retry_provider(self):
        self.assert_rejected('\n'.join(['{bad'] * 10))
        response = self.post_output('\n'.join([json.dumps(QUESTION)] * 10))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.get_data(as_text=True).splitlines()), 10)

    def test_valid_quiz_without_final_newline_keeps_ndjson_contract(self):
        response = self.post_output('\n'.join([json.dumps(QUESTION)] * 10))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'application/x-ndjson')
        self.assertEqual([json.loads(line) for line in response.get_data(as_text=True).splitlines()],
                         [QUESTION] * 10)

    def test_missing_terminal_usage_is_closed_and_reported_without_replay(self):
        response = self.post_output('\n'.join([json.dumps(QUESTION)] * 10), completed=False)
        self.assertEqual(response.status_code, 503)
        with app.app_context():
            models = app.extensions['usage_ledger'].m
            request = db.session.query(models['Request']).one()
            self.assertEqual(request.status, 'uncertain')
            self.assertEqual(db.session.query(models['Month']).one().reserved, 0)

    def test_cleanup_is_not_deferred_until_another_db_transaction(self):
        self.assert_rejected('\n'.join(['{bad'] * 10))
        ledger = app.extensions['usage_ledger']
        with app.app_context(), patch.object(ledger, 'settle') as settle:
            with ledger.transaction():
                gc.collect()
            settle.assert_not_called()

    def test_malformed_keyword_grading_is_not_counted_as_a_wrong_answer(self):
        self.check_bad_grading('quiz/keyword/check',
            {'user_answer': 'http', 'correct_answer': 'HTTP'},
            [None, '', 'probably true', '{"is_correct":true}'])

    def test_malformed_written_grading_is_recoverable(self):
        self.check_bad_grading('quiz/written/check',
            {'user_answer': 'HTTP', 'model_answer': 'HTTP', 'rubric': '["HTTP"]'},
            ['{bad', 'null', '[]', '{}',
             '{"is_correct":true,"score":2,"total":1,"feedback":"bad"}',
             '{"is_correct":true,"score":0,"total":0,"feedback":"bad"}',
             '{"is_correct":true,"score":NaN,"total":1,"feedback":"bad"}'])

    def check_bad_grading(self, route, data, contents):
        for content in contents:
            with self.subTest(content=content):
                completion = SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                    usage=SimpleNamespace(prompt_tokens=24, completion_tokens=16))
                with patch('app.client.chat.completions.create', return_value=completion) as provider:
                    response = self.client.post(f'/lecture/{self.lecture_id}/{route}', data=data,
                        headers={'X-CSRF-Token': self.csrf, 'Idempotency-Key': str(uuid4())})
                self.assertEqual(provider.call_count, 1)
                self.assertEqual(response.status_code, 502)
                self.assertIn('もう一度回答', response.json['error'])
                with app.app_context():
                    self.assertEqual(QuizAttempt.query.count(), 0)
                self.assertTrue(self.client.get('/api/ai-usage').json['features']['grading']['available'])
