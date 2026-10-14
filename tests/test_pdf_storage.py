from io import BytesIO
import time
import unittest
from unittest.mock import patch

from test_email_auth import app, db, User
from app import Course, Lecture, pdf_service
from botocore.exceptions import BotoCoreError
from flask import g
from pypdf import PdfWriter
from sqlalchemy.exc import SQLAlchemyError


def sample_pdf():
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
    stream = DecodedStreamObject()
    stream.set_data(b'BT /F1 12 Tf 20 200 Td (Study PDF) Tj ET')
    page[NameObject('/Contents')] = stream
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


class MemoryStorage:
    def __init__(self):
        self.objects = {}

    def put(self, key, data):
        self.objects[key] = data

    def get(self, key):
        return BytesIO(self.objects[key])

    def delete(self, key):
        self.objects.pop(key, None)


class PdfTests(unittest.TestCase):
    def setUp(self):
        self.context = app.app_context()
        self.context.push()
        db.create_all()
        control = db.session.get(app.extensions['usage_ledger'].m['Control'], 1)
        control.stopped, control.pdf_ready = False, True
        db.session.commit()
        self.store = MemoryStorage()
        app.extensions['pdf_storage'] = self.store
        self.user = User(email='owner@example.com', password_hash='unused')
        self.other = User(email='other@example.com', password_hash='unused')
        db.session.add_all([self.user, self.other])
        db.session.flush()
        self.course = Course(name='Course', user_id=self.user.id)
        db.session.add(self.course)
        db.session.flush()
        self.lecture = Lecture(course_id=self.course.id, week=1, text='Study notes for testing')
        db.session.add(self.lecture)
        db.session.commit()
        self.id = self.lecture.id
        self.client = self.client_for(self.user.id)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        app.extensions.pop('pdf_storage', None)
        self.context.pop()

    def client_for(self, user_id):
        client = app.test_client()
        with client.session_transaction() as state:
            state.update(_user_id=str(user_id), auth_version=0, pdf_csrf='csrf', course_delete_token='delete')
        return client

    def upload(self, data=None, filename='same.pdf', csrf='csrf'):
        g.pop('_login_user', None)
        return self.client.post(f'/lecture/{self.id}/upload', data={'pdf': (BytesIO(data if data is not None else sample_pdf()), filename), 'csrf_token': csrf})

    def test_upload_extract_reload_restart_and_isolation(self):
        response = self.upload()
        self.assertEqual(response.status_code, 200)
        self.assertIn('Study PDF', response.json['text'])
        key = db.session.get(Lecture, self.id).pdf_filename
        self.assertTrue(key.startswith(f'users/{self.user.id}/lectures/{self.id}/'))
        url = response.json['pdf_url']
        self.assertEqual(self.client.get(url).data, sample_pdf())
        self.assertEqual(self.client.get(f'/lecture/{self.id}/upload').status_code, 200)
        # A new client/session retrieves solely from the persisted key and storage.
        owner_id, other_id = self.user.id, self.other.id
        db.session.remove()
        g.pop('_login_user', None)
        self.assertEqual(self.client_for(owner_id).get(url).data, sample_pdf())
        g.pop('_login_user', None)
        self.assertEqual(self.client_for(other_id).get(url).status_code, 403)
        g.pop('_login_user', None)
        self.assertEqual(app.test_client().get(url).status_code, 302)
        self.assertEqual(app.test_client().get('/uploads/same.pdf').status_code, 404)

    def test_replace_same_name_and_delete(self):
        self.upload()
        first = self.lecture.pdf_filename
        self.lecture.summary = 'old'
        db.session.commit()
        self.upload()
        self.assertNotEqual(first, self.lecture.pdf_filename)
        self.assertNotIn(first, self.store.objects)
        self.assertIsNone(self.lecture.summary)
        response = self.client.post(f'/course/{self.course.id}/delete', data={'csrf_token': 'delete'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.store.objects)
        self.assertIsNone(db.session.get(Lecture, self.id))

    def test_validation(self):
        self.assertEqual(self.upload(csrf='wrong').status_code, 400)
        self.assertEqual(self.upload(filename='file.txt').status_code, 400)
        self.assertEqual(self.upload(data=b'%PDF-broken').status_code, 400)
        self.assertEqual(self.upload(data=b'not pdf').status_code, 400)
        with patch.dict(app.config, PDF_MAX_BYTES=10):
            self.assertEqual(self.upload().status_code, 413)
        with patch.dict(app.config, MAX_CONTENT_LENGTH=10):
            self.assertEqual(self.upload().status_code, 413)
        self.assertFalse(self.store.objects)
        self.assertIsNone(self.lecture.pdf_filename)

    def test_encrypted_and_page_limit(self):
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.encrypt('secret')
        output = BytesIO()
        writer.write(output)
        self.assertEqual(self.upload(data=output.getvalue()).status_code, 400)
        with patch.dict(app.config, PDF_MAX_PAGES=0):
            self.assertEqual(self.upload().status_code, 400)

    def test_put_failure_retains_old_pdf(self):
        self.upload()
        old = self.lecture.pdf_filename
        with patch.object(self.store, 'put', side_effect=BotoCoreError()):
            self.assertEqual(self.upload().status_code, 503)
        self.assertEqual(self.lecture.pdf_filename, old)

    def test_db_failure_cleanup_journal(self):
        original = db.session.commit
        calls = 0
        def commit():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise SQLAlchemyError('simulated')
            return original()
        with patch.object(db.session, 'commit', side_effect=commit):
            self.assertEqual(self.upload().status_code, 503)
        self.assertIsNone(self.lecture.pdf_filename)
        self.assertEqual(len(self.store.objects), 1)
        job = pdf_service['Deletion'].query.one()
        job.not_before = time.time() - 1
        db.session.commit()
        pdf_service['cleanup']()
        self.assertFalse(self.store.objects)

    def test_delete_failure_is_retryable(self):
        self.upload()
        with patch.object(self.store, 'delete', side_effect=BotoCoreError()):
            self.client.post(f'/course/{self.course.id}/delete', data={'csrf_token': 'delete'})
        self.assertEqual(pdf_service['Deletion'].query.count(), 1)
        self.assertEqual(len(self.store.objects), 1)
        pdf_service['cleanup']()
        self.assertEqual(pdf_service['Deletion'].query.count(), 0)
        self.assertFalse(self.store.objects)


if __name__ == '__main__':
    unittest.main()
