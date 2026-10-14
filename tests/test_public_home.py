from html.parser import HTMLParser
import unittest

import test_pdf_storage as fixtures
from test_pdf_storage import app, g


class VerificationParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_head = False
        self.tags = []

    def handle_starttag(self, tag, attrs):
        if tag == 'head':
            self.in_head = True
        values = dict(attrs)
        if tag == 'meta' and values.get('name') == 'google-site-verification':
            self.tags.append((self.in_head, values.get('content')))

    def handle_endtag(self, tag):
        if tag == 'head':
            self.in_head = False


class PublicHomeTests(unittest.TestCase):
    setUp = fixtures.PdfTests.setUp
    tearDown = fixtures.PdfTests.tearDown
    client_for = fixtures.PdfTests.client_for

    def test_anonymous_home_has_verification_without_private_panel(self):
        g.pop('_login_user', None)
        client = app.test_client()
        response = client.get('/')
        self.assertEqual(response.status_code, 200)
        parser = VerificationParser()
        html = response.get_data(as_text=True)
        parser.feed(html)
        self.assertEqual(parser.tags, [(True, 'pR0LN6meuqwCcDz47ntCnNfO2r_h2zi88t_JOrbiJdA')])
        self.assertIn('href="/login"', html)
        self.assertNotIn('name="csrf_token"', html)
        self.assertNotIn('ai-usage.js', html)
        self.assertEqual(client.head('/').status_code, 200)
        for path in ('/course', '/api/ai-usage', f'/lecture/{self.id}/pdf'):
            g.pop('_login_user', None)
            self.assertEqual(client.get(path).status_code, 302)

    def test_authenticated_home_keeps_existing_controls(self):
        g.pop('_login_user', None)
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('ai-usage.js', html)
        self.assertIn('action="/logout"', html)
        self.assertIn('href="/course"', html)
