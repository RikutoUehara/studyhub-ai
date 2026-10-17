import json
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_pdf_storage as fixtures
from test_pdf_storage import app, db, g
from app import ChatMessage, Lecture
from usage_ledger import JST


class AIEndpointTests(unittest.TestCase):
    setUp = fixtures.PdfTests.setUp
    tearDown = fixtures.PdfTests.tearDown
    client_for = fixtures.PdfTests.client_for

    routes = [
        ('summary-stream', 'summary', {}), ('reanalyze', 'summary', {'instruction': 'review'}),
        ('chat', 'chat', {'message': 'Question'}), ('quiz/generate', 'quiz', {}),
        ('quiz/keyword/check', 'grading', {'user_answer': 'a', 'correct_answer': 'a'}),
        ('quiz/written/check', 'grading', {'user_answer': 'a', 'model_answer': 'a', 'rubric': '[]'}),
    ]

    def post(self, route, data, buffered=True):
        self.client.get(f'/lecture/{self.id}/upload')
        with self.client.session_transaction() as state:
            token = state['security_csrf']
        g.pop('_login_user', None)
        return self.client.post(f'/lecture/{self.id}/{route}', data=data,
                                headers={'X-CSRF-Token': token}, buffered=buffered)

    def test_all_routes_success_save_and_shared_pools(self):
        usage = SimpleNamespace(input_tokens=20, output_tokens=10)
        quiz = '\n'.join(json.dumps({'type': 'true_false', 'answer': True, 'question': 'q', 'explanation': 'e'}) for _ in range(10))
        def events(**kwargs):
            text = quiz if '10' in str(kwargs['input']) and 'JSON' in str(kwargs['input']) else 'saved AI answer'
            return iter([SimpleNamespace(type='response.output_text.delta', delta=text),
                         SimpleNamespace(type='response.completed', response=SimpleNamespace(usage=usage))])
        completion = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content='{"is_correct":true,"score":1,"total":1,"feedback":"ok"}'))],
            usage=SimpleNamespace(prompt_tokens=20, completion_tokens=10))
        with patch('app.client.responses.create', side_effect=events) as response_call, patch(
            'app.client.chat.completions.create', side_effect=lambda **kwargs: completion if kwargs.get('response_format') else
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='true'))], usage=completion.usage)
        ) as completion_call:
            for route, _, data in self.routes:
                self.assertEqual(self.post(route, data).status_code, 200, route)
            self.assertEqual(response_call.call_count, 4)
            self.assertEqual(completion_call.call_count, 2)
            self.assertEqual(completion_call.call_args.kwargs['max_completion_tokens'], 1000)
        models = app.extensions['usage_ledger'].m
        counts = {item.feature: 0 for item in db.session.query(models['Request'])}
        for item in db.session.query(models['Request']):
            self.assertEqual(item.status, 'completed')
            counts[item.feature] += 1
        self.assertEqual(counts, {'summary': 2, 'chat': 1, 'quiz': 1, 'grading': 2})
        self.assertTrue(db.session.get(Lecture, self.id).summary)
        self.assertEqual(ChatMessage.query.filter_by(lecture_id=self.id).count(), 2)

    def test_all_routes_refuse_daily_budget_and_emergency_before_api(self):
        models = app.extensions['usage_ledger'].m
        date = datetime.now(JST)
        day = date.strftime('%Y-%m-%d')
        for feature, count in [('summary', 10), ('chat', 60), ('quiz', 10), ('grading', 60)]:
            db.session.add(models['Day'](key=f'{self.user.id}:{day}:{feature}', count=count))
        db.session.commit()
        with patch('app.client.responses.create') as responses, patch('app.client.chat.completions.create') as completions:
            for route, _, data in self.routes:
                self.assertEqual(self.post(route, data).status_code, 429)
            db.session.query(models['Day']).delete()
            db.session.add(models['Month'](month=date.strftime('%Y-%m'), spent=2400, reserved=0, uncertain=0))
            db.session.commit()
            for route, _, data in self.routes:
                self.assertEqual(self.post(route, data).status_code, 503)
            app.extensions['usage_ledger'].stop(True)
            for route, _, data in self.routes:
                self.assertEqual(self.post(route, data).status_code, 503)
            responses.assert_not_called()
            completions.assert_not_called()
        self.assertEqual(ChatMessage.query.count(), 0)

    def test_invalid_inputs_do_not_reserve_or_call_provider(self):
        with patch('app.client.responses.create') as responses, patch('app.client.chat.completions.create') as completions:
            self.assertEqual(self.post('chat', {'message': ' '}).status_code, 400)
            self.assertEqual(self.post('quiz/keyword/check', {}).status_code, 400)
            self.assertEqual(self.post('quiz/written/check', {'rubric': '{bad'}).status_code, 400)
            self.lecture.text = ''
            db.session.commit()
            for route in ('summary-stream', 'reanalyze', 'quiz/generate', 'chat'):
                self.assertEqual(self.post(route, {'message': 'question'}).status_code, 400)
            responses.assert_not_called()
            completions.assert_not_called()
        self.assertEqual(db.session.query(app.extensions['usage_ledger'].m['Request']).count(), 0)
