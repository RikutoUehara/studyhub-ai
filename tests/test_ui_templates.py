"""Render the shared UI through real routes and preserve form/JS integration contracts."""
from collections import Counter
import hashlib
from html.parser import HTMLParser
import time
import unittest

import test_pdf_storage as fixtures
from test_pdf_storage import app, db, g
from app import QuizAttempt


class PageParser(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.forms = []
        self.form = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs))
        if tag == 'form':
            self.form = {'attrs': attrs, 'fields': {}}
            self.forms.append(self.form)
        elif self.form is not None and tag in {'input', 'textarea', 'select'}:
            self.form['fields'][attrs.get('name')] = attrs

    def handle_endtag(self, tag):
        if tag == 'form':
            self.form = None

    def by_id(self, identity):
        return next(attrs for _, attrs in self.tags if attrs.get('id') == identity)


class UITemplateTests(unittest.TestCase):
    setUp = fixtures.PdfTests.setUp
    tearDown = fixtures.PdfTests.tearDown
    client_for = fixtures.PdfTests.client_for

    def page(self, path, client=None, expected_status=200):
        g.pop('_login_user', None)
        response = (client or self.client).get(path)
        self.assertEqual(response.status_code, expected_status, path)
        return PageParser(response.get_data(as_text=True))

    def assert_accessible_shell(self, page):
        self.assertIn(('html', {'lang': 'ja'}), page.tags)
        self.assertTrue(any(tag == 'meta' and attrs.get('name') == 'viewport'
                            for tag, attrs in page.tags))
        self.assertEqual(sum(tag == 'main' for tag, _ in page.tags), 1)
        self.assertEqual(sum(tag == 'h1' for tag, _ in page.tags), 1)
        ids = [attrs['id'] for _, attrs in page.tags if attrs.get('id')]
        self.assertEqual([identity for identity, count in Counter(ids).items() if count > 1], [])
        labels = {attrs['for'] for tag, attrs in page.tags if tag == 'label' and attrs.get('for')}
        for tag, attrs in page.tags:
            if tag in {'input', 'textarea', 'select'} and attrs.get('type') != 'hidden':
                self.assertTrue(attrs.get('aria-label') or attrs.get('aria-labelledby') or attrs.get('id') in labels,
                                f'Missing accessible label: {tag} {attrs}')
            for reference in ('aria-labelledby', 'aria-describedby', 'aria-controls'):
                for identity in attrs.get(reference, '').split():
                    self.assertIn(identity, ids, f'Broken {reference} reference')
        self.assertIn('main-content', ids)
        self.assertTrue(any(tag == 'a' and attrs.get('href') == '#main-content' for tag, attrs in page.tags))

    def test_public_pages_and_auth_forms_have_consistent_accessible_shell(self):
        client = app.test_client()
        for path in ('/', '/login', '/register', '/forgot-password', '/resend-verification', '/terms', '/privacy'):
            with self.subTest(path=path):
                page = self.page(path, client)
                self.assert_accessible_shell(page)
                if path in {'/login', '/register', '/forgot-password', '/resend-verification'}:
                    form = next(form for form in page.forms if 'email' in form['fields'])
                    self.assertEqual(form['attrs']['method'].upper(), 'POST')
                    self.assertTrue(form['fields']['csrf_token']['value'])
                    self.assertEqual(form['fields']['email']['type'], 'email')
                    if path in {'/login', '/register'}:
                        self.assertEqual(form['fields']['password']['type'], 'password')

    def test_verification_and_password_reset_form_modes_render(self):
        for purpose, path, expected_fields in (
            ('verify', '/verify-email/', {'csrf_token'}),
            ('reset', '/reset-password/', {'csrf_token', 'password', 'password_confirm'}),
        ):
            token = 'ui-template-' + purpose
            table = db.metadata.tables['email_token']
            db.session.execute(table.insert().values(
                digest=hashlib.sha256(token.encode()).hexdigest(), user_id=self.user.id,
                purpose=purpose, created_at=time.time(), expires_at=time.time() + 600, used=False,
            ))
            db.session.commit()
            page = self.page(path + token, app.test_client())
            self.assert_accessible_shell(page)
            self.assertTrue(any(set(form['fields']) == expected_fields for form in page.forms))
        self.assert_accessible_shell(self.page('/reset-password/invalid', app.test_client(), expected_status=400))

    def test_dashboard_and_lecture_pages_keep_course_form_contracts(self):
        for path in ('/', '/course', f'/lecture/{self.course.id}'):
            with self.subTest(path=path):
                page = self.page(path)
                self.assert_accessible_shell(page)
                logout = next(form for form in page.forms if form['attrs'].get('action') == '/logout')
                self.assertEqual(logout['attrs']['method'].upper(), 'POST')
                self.assertTrue(logout['fields']['csrf_token']['value'])
        page = self.page('/course')
        add = next(form for form in page.forms if 'course_name' in form['fields'])
        self.assertTrue(add['fields']['csrf_token']['value'])
        delete = next(form for form in page.forms if form['attrs'].get('action') == f'/course/{self.course.id}/delete')
        self.assertTrue(delete['fields']['csrf_token']['value'])

    def test_workspace_empty_and_populated_states_keep_ui_integration_points(self):
        for populated in (False, True):
            with self.subTest(populated=populated):
                if populated:
                    key = f'users/{self.user.id}/lectures/{self.id}/fixture.pdf'
                    self.store.put(key, fixtures.sample_pdf())
                    self.lecture.pdf_filename = key
                    self.lecture.summary = '## 要約\n\n保存した分析結果'
                    db.session.add(QuizAttempt(lecture_id=self.id, score=7, total_questions=10))
                    db.session.commit()
                page = self.page(f'/lecture/{self.id}/upload')
                self.assert_accessible_shell(page)
                upload = next(form for form in page.forms if form['attrs'].get('id') == 'upload-form')
                self.assertEqual(upload['attrs']['action'], f'/lecture/{self.id}/upload')
                self.assertEqual(upload['attrs']['enctype'], 'multipart/form-data')
                self.assertEqual(upload['fields']['pdf']['type'], 'file')
                self.assertTrue(upload['fields']['csrf_token']['value'])
                for action, field in ((f'/lecture/{self.id}/reanalyze', 'instruction'),
                                      (f'/lecture/{self.id}/chat', 'message')):
                    self.assertTrue(any(form['attrs'].get('action') == action and field in form['fields']
                                        for form in page.forms))
                for identity in ('analysis-tab', 'pdf-tab', 'quiz-tab', 'analysis-content', 'pdf-content',
                                 'quiz-content', 'summary-stream', 'quiz-question', 'quiz-result',
                                 'quiz-feedback', 'quiz-score-chart', 'generate-quiz-button'):
                    page.by_id(identity)
                frames = [attrs for tag, attrs in page.tags if tag == 'iframe']
                self.assertEqual(len(frames), int(populated))
                if populated:
                    self.assertEqual(frames[0]['src'], f'/lecture/{self.id}/pdf')
                    self.assertTrue(frames[0].get('title'))
