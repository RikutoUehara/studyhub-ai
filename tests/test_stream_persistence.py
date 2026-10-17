"""Exercise streamed responses across the same app-context boundary as real HTTP."""
import json
import re
import unittest
from unittest.mock import patch

from openai.types import CompletionUsage
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice
from openai.types.responses import Response, ResponseCompletedEvent, ResponseTextDeltaEvent, ResponseUsage

from test_email_auth import app, db, User
from app import ChatMessage, Course, Lecture, QuizAttempt


def sdk_events(text, completed=True):
    """Use multiple nonempty deltas so persistence happens after response iteration starts."""
    midpoint = max(1, len(text) // 2)
    for index, delta in enumerate((text[:midpoint], text[midpoint:]), start=1):
        yield ResponseTextDeltaEvent.model_construct(
            type='response.output_text.delta', delta=delta, item_id='fixture',
            output_index=0, content_index=0, sequence_number=index, logprobs=[],
        )
    if completed:
        yield ResponseCompletedEvent.model_construct(
            type='response.completed', sequence_number=3,
            response=Response.model_construct(usage=ResponseUsage.model_construct(
                input_tokens=24, output_tokens=16, total_tokens=40,
            )),
        )


class StreamPersistenceTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True)
        # Deliberately exit this context before creating or consuming any response.
        with app.app_context():
            db.create_all()
            models = app.extensions['usage_ledger'].m
            control = db.session.get(models['Control'], 1)
            control.stopped, control.pdf_ready = False, True
            user = User(email='stream@example.com', password_hash='unused')
            db.session.add(user)
            db.session.flush()
            course = Course(name='Stream fixture', user_id=user.id)
            db.session.add(course)
            db.session.flush()
            lecture = Lecture(course_id=course.id, week=1, text='HTTP is a web communication protocol.')
            db.session.add(lecture)
            db.session.flush()
            self.user_id, self.lecture_id = user.id, lecture.id
            db.session.commit()
        self.client = self.authenticated_client()
        response = self.client.get(self.workspace_url)
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as state:
            self.csrf = state['security_csrf']

    def tearDown(self):
        with app.app_context():
            db.session.remove()
            db.drop_all()

    @property
    def workspace_url(self):
        return f'/lecture/{self.lecture_id}/upload'

    def authenticated_client(self):
        client = app.test_client()
        with client.session_transaction() as state:
            state.update(_user_id=str(self.user_id), auth_version=0)
        return client

    def stream(self, route, text, data=None):
        with patch('app.client.responses.create', side_effect=lambda **kwargs: sdk_events(text)) as provider:
            response = self.client.post(
                f'/lecture/{self.lecture_id}/{route}', data=data or {}, buffered=True,
                headers={'X-CSRF-Token': self.csrf, 'Idempotency-Key': 'stream-regression-key'},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(as_text=True), text)
        self.assertEqual(provider.call_count, 1)
        return response

    def assert_accounted_once(self, feature):
        # A fresh context cannot reuse ORM objects that were active during the POST.
        with app.app_context():
            models = app.extensions['usage_ledger'].m
            request = db.session.query(models['Request']).one()
            self.assertEqual(request.feature, feature)
            self.assertEqual(request.status, 'completed')
            self.assertFalse(request.active)
            self.assertEqual((request.input_tokens, request.output_tokens), (24, 16))
            month = db.session.query(models['Month']).one()
            self.assertEqual(month.reserved, 0)
            self.assertEqual(month.uncertain, 0)
            self.assertEqual(month.spent, request.cost)
            self.assertEqual(db.session.query(models['Day']).one().count, 1)

    def test_summary_survives_request_teardown_and_reload(self):
        text = '## 要約\n\nこの要約は保存の回帰テスト用です。'
        self.stream('summary-stream', text)
        with app.app_context():
            self.assertEqual(db.session.get(Lecture, self.lecture_id).summary, text)
        reloaded = self.authenticated_client().get(self.workspace_url)
        self.assertEqual(reloaded.status_code, 200)
        self.assertIn('この要約は保存の回帰テスト用です。', reloaded.get_data(as_text=True))
        self.assert_accounted_once('summary')

    def test_reanalysis_replaces_saved_summary_after_teardown(self):
        with app.app_context():
            db.session.get(Lecture, self.lecture_id).summary = '以前に保存した要約です。'
            db.session.commit()
        text = '## 要約\n\n再分析で新しく保存した内容です。'
        self.stream('reanalyze', text, {'instruction': '要点を整理してください。'})
        with app.app_context():
            self.assertEqual(db.session.get(Lecture, self.lecture_id).summary, text)
        reloaded = self.authenticated_client().get(self.workspace_url).get_data(as_text=True)
        self.assertIn('再分析で新しく保存した内容です。', reloaded)
        self.assertNotIn('以前に保存した要約です。', reloaded)
        self.assert_accounted_once('summary')

    def test_chat_saves_both_messages_in_order_and_survives_reload(self):
        question = '授業資料の要点を教えてください。'
        answer = '回答を保存しました。通信規約について確認しましょう。'
        self.stream('chat', answer, {'message': question})
        with app.app_context():
            rows = ChatMessage.query.filter_by(lecture_id=self.lecture_id).order_by(ChatMessage.id).all()
            self.assertEqual([(row.role, row.content) for row in rows], [('user', question), ('assistant', answer)])
        reloaded = self.authenticated_client().get(self.workspace_url)
        self.assertEqual(reloaded.status_code, 200)
        self.assertIn(question, reloaded.get_data(as_text=True))
        self.assertIn(answer, reloaded.get_data(as_text=True))
        self.assert_accounted_once('chat')

    def test_quiz_grading_result_and_chart_survive_separate_requests(self):
        questions = [
            {'type': 'multiple_choice', 'question': 'Which protocol?',
             'choices': ['HTTP', 'PDF', 'CSS', 'SQL'], 'answer': 'HTTP', 'explanation': 'Web protocol.'}
            for _ in range(6)
        ] + [
            {'type': 'true_false', 'question': 'HTTP is a web protocol.' if index % 2 == 0 else 'HTTP is an image format.',
             'answer': index % 2 == 0, 'explanation': 'Fixture explanation.'}
            for index in range(4)
        ]
        output = '\n'.join(json.dumps(question) for question in questions) + '\n'
        with patch('app.client.responses.create', side_effect=lambda **kwargs: sdk_events(output)) as provider:
            response = self.client.post(
                f'/lecture/{self.lecture_id}/quiz/generate', buffered=True,
                headers={'X-CSRF-Token': self.csrf, 'Idempotency-Key': 'quiz-regression-key'},
            )
        self.assertEqual(response.status_code, 200)
        generated = [json.loads(line) for line in response.get_data(as_text=True).splitlines()]
        self.assertEqual(len(generated), 10)
        self.assertEqual({question['type'] for question in generated}, {'multiple_choice', 'true_false'})
        self.assertEqual(provider.call_count, 1)
        self.assertNotIn('keyword', provider.call_args.kwargs['input'])

        def completion(content):
            return ChatCompletion.model_construct(
                choices=[Choice.model_construct(message=ChatCompletionMessage.model_construct(
                    role='assistant', content=content,
                ))],
                usage=CompletionUsage(prompt_tokens=24, completion_tokens=16, total_tokens=40),
            )

        written_result = {'is_correct': True, 'score': 1, 'total': 1, 'feedback': 'Correct.'}
        with patch('app.client.chat.completions.create', side_effect=[
            completion('true'), completion(json.dumps(written_result)),
        ]) as grader:
            keyword = self.client.post(
                f'/lecture/{self.lecture_id}/quiz/keyword/check',
                data={'user_answer': 'http', 'correct_answer': 'HTTP'},
                headers={'X-CSRF-Token': self.csrf, 'Idempotency-Key': 'keyword-regression-key'},
            )
            written = self.client.post(
                f'/lecture/{self.lecture_id}/quiz/written/check',
                data={'user_answer': 'HTTP', 'model_answer': 'HTTP', 'rubric': '["HTTP"]'},
                headers={'X-CSRF-Token': self.csrf, 'Idempotency-Key': 'written-regression-key'},
            )
        self.assertEqual(keyword.status_code, 200)
        self.assertTrue(keyword.json['is_correct'])
        self.assertEqual(written.status_code, 200)
        self.assertEqual(written.json, written_result)
        self.assertEqual(grader.call_count, 2)

        result = self.client.post(
            f'/lecture/{self.lecture_id}/quiz/result', json={'score': 8, 'total_questions': 10},
            headers={'X-CSRF-Token': self.csrf},
        )
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json['success'])
        with app.app_context():
            attempt = QuizAttempt.query.one()
            self.assertEqual((attempt.id, attempt.lecture_id, attempt.score, attempt.total_questions),
                             (result.json['attempt_id'], self.lecture_id, 8, 10))
            models = app.extensions['usage_ledger'].m
            requests = db.session.query(models['Request']).all()
            self.assertEqual(sorted(row.feature for row in requests), ['grading', 'grading', 'quiz'])
            self.assertTrue(all(row.status == 'completed' and not row.active for row in requests))
            month = db.session.query(models['Month']).one()
            self.assertEqual((month.reserved, month.uncertain), (0, 0))
        reloaded = self.authenticated_client().get(self.workspace_url)
        self.assertEqual(reloaded.status_code, 200)
        chart = re.search(r'const\s+quizScoreData\s*=\s*(\[.*?\])\s*;',
                          reloaded.get_data(as_text=True), re.DOTALL)
        self.assertIsNotNone(chart)
        self.assertEqual(json.loads(chart.group(1)), [{'label': result.json['label'], 'score': 80}])

    def test_disconnect_preserves_previous_summary_and_accounts_without_retry(self):
        previous = 'Keep the previously saved summary.'
        with app.app_context():
            db.session.get(Lecture, self.lecture_id).summary = previous
            db.session.commit()
        closed = []

        def incomplete(**kwargs):
            try:
                yield from sdk_events('This response was interrupted.', completed=False)
            finally:
                closed.append(True)

        with patch('app.client.responses.create', side_effect=incomplete) as provider:
            response = self.client.post(
                f'/lecture/{self.lecture_id}/summary-stream', buffered=False,
                headers={'X-CSRF-Token': self.csrf, 'Idempotency-Key': 'disconnect-regression-key'},
            )
            self.assertEqual(response.status_code, 200)
            self.assertTrue(next(iter(response.response)))
            response.close()
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(closed, [True])
        with app.app_context():
            self.assertEqual(db.session.get(Lecture, self.lecture_id).summary, previous)
            models = app.extensions['usage_ledger'].m
            request = db.session.query(models['Request']).one()
            self.assertEqual(request.status, 'uncertain')
            month = db.session.query(models['Month']).one()
            self.assertEqual(month.reserved, 0)
            self.assertGreater(month.uncertain, 0)
            self.assertEqual(db.session.query(models['Day']).one().count, 1)
