import unittest
import json
from types import SimpleNamespace
from unittest.mock import patch

import test_pdf_storage as pdf_tests
from test_pdf_storage import app, db, g
from web_security import safe_markdown


class SecurityTests(unittest.TestCase):
    setUp = pdf_tests.PdfTests.setUp
    tearDown = pdf_tests.PdfTests.tearDown
    client_for = pdf_tests.PdfTests.client_for

    routes = [
        ('/summary-stream', {}), ('/reanalyze', {'instruction': 'test'}),
        ('/chat', {'message': 'test'}), ('/quiz/generate', {}),
        ('/quiz/keyword/check', {'user_answer': 'a', 'correct_answer': 'a'}),
        ('/quiz/written/check', {'user_answer': 'a', 'model_answer': 'a', 'rubric': '[]'}),
        ('/quiz/result', None),
    ]

    def request(self, client, suffix, data, token=True, lecture_id=None):
        with client.session_transaction() as state:
            state['security_csrf'] = 'test-csrf'
        g.pop('_login_user', None)
        options = {'headers': {'X-CSRF-Token': 'test-csrf'} if token else {}}
        if data is None:
            options['json'] = {'score': 1, 'total_questions': 1}
        else:
            options['data'] = data
        return client.post(f'/lecture/{lecture_id or self.id}' + suffix, buffered=True, **options)

    def test_anonymous_other_owner_missing_and_csrf(self):
        usage = SimpleNamespace(input_tokens=5, output_tokens=5)
        events = [SimpleNamespace(type='response.completed', response=SimpleNamespace(usage=usage))]
        quiz = '\n'.join(json.dumps({'type': 'true_false', 'question': 'q', 'answer': True,
                                    'explanation': 'e'}) for _ in range(10))
        def stream_events(**kwargs):
            if '1行につき1問' in str(kwargs.get('input')):
                return iter([SimpleNamespace(type='response.output_text.delta', delta=quiz), *events])
            return iter(events)
        def completion(**kwargs):
            content = '{"is_correct":true,"score":1,"total":1,"feedback":"ok"}' if kwargs.get('response_format') else 'true'
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                usage=SimpleNamespace(prompt_tokens=5, completion_tokens=5)
            )
        with patch('app.client.responses.create', side_effect=stream_events) as responses, patch(
            'app.client.chat.completions.create', side_effect=completion
        ) as completions:
            other = self.client_for(self.other.id)
            for suffix, data in self.routes:
                with self.subTest(route=suffix):
                    self.assertEqual(self.request(app.test_client(), suffix, data).status_code, 302)
                    self.assertEqual(self.request(other, suffix, data).status_code, 403)
                    self.assertEqual(self.request(self.client, suffix, data, token=False).status_code, 400)
                    self.assertEqual(self.request(self.client, suffix, data, lecture_id=99999).status_code, 404)
            responses.assert_not_called()
            completions.assert_not_called()
            for suffix, data in self.routes:
                with self.subTest(owner_route=suffix):
                    self.assertEqual(self.request(self.client, suffix, data).status_code, 200)

    def test_get_cannot_generate_or_logout_and_course_csrf(self):
        self.assertEqual(self.client.get(f'/lecture/{self.id}/summary-stream').status_code, 405)
        self.assertEqual(self.client.get('/logout').status_code, 405)
        self.assertEqual(self.client.post('/course', data={'course_name': 'test'}).status_code, 400)
        self.client.get('/course')
        with self.client.session_transaction() as state:
            token = state['security_csrf']
        self.assertEqual(self.client.post('/course', data={'course_name': 'test', 'csrf_token': token}).status_code, 302)
        self.assertEqual(self.client.post('/logout').status_code, 400)
        self.assertEqual(self.client.post('/logout', data={'csrf_token': token}).status_code, 302)

    def test_markdown_sanitized_on_reload(self):
        payload = '<script>alert(1)</script><img src="x" onerror="alert(1)"><a href="javascript:alert(1)">bad</a>'
        clean = safe_markdown('## Title\n\n' + payload)
        self.assertIn('<h2>Title</h2>', clean)
        for dangerous in ('<script', 'onerror', 'javascript:'):
            self.assertNotIn(dangerous, clean)
        self.lecture.summary = payload
        db.session.commit()
        html = self.client.get(f'/lecture/{self.id}/upload').get_data(as_text=True)
        self.assertNotIn(payload, html)


if __name__ == '__main__':
    unittest.main()
