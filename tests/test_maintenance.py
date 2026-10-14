import json
import unittest
from unittest.mock import patch
import httpx
import test_pdf_storage as fixtures
from test_pdf_storage import app, db
from app import pdf_service


class MaintenanceTests(unittest.TestCase):
    setUp = fixtures.PdfTests.setUp
    tearDown = fixtures.PdfTests.tearDown
    client_for = fixtures.PdfTests.client_for

    def warning(self):
        model = app.extensions['usage_ledger'].m['Month']
        row = model(month='2026-10', spent=1800, reserved=0, uncertain=0, warned=True)
        db.session.add(row)
        db.session.commit()
        return row

    def test_recovery_failure_does_not_stop_cleanup_and_notification(self):
        self.warning()
        ledger = app.extensions['usage_ledger']
        with patch.dict('os.environ', {'AI_ADMIN_EMAIL': 'admin@example.com'}), patch.object(
            ledger, 'reap', side_effect=RuntimeError('sensitive text must not be logged')
        ), patch.dict(pdf_service, {'cleanup': lambda **kw: {'completed': 1, 'failed': 0}}), patch(
            'email_delivery.httpx.post'
        ) as send:
            result = app.test_cli_runner().invoke(args=['usage-maintenance'])
            self.assertNotEqual(result.exit_code, 0)
            self.assertNotIn('sensitive text', result.output)
            self.assertIn('"task": "pdf_cleanup", "status": "success"', result.output)
            self.assertEqual(send.call_count, 1)

    def test_partial_pdf_failure_is_reported_and_notification_continues(self):
        self.warning()
        with patch.dict('os.environ', {'AI_ADMIN_EMAIL': 'admin@example.com'}), patch.dict(
            pdf_service, {'cleanup': lambda **kw: {'completed': 1, 'failed': 1}}
        ), patch('email_delivery.httpx.post') as send:
            result = app.test_cli_runner().invoke(args=['usage-maintenance'])
            self.assertNotEqual(result.exit_code, 0)
            self.assertIn('"task": "pdf_cleanup", "status": "failed"', result.output)
            self.assertEqual(send.call_count, 1)

    def test_unknown_email_never_replays_after_idempotency_retention(self):
        self.warning()
        with patch.dict('os.environ', {'AI_ADMIN_EMAIL': 'admin@example.com'}), patch(
            'email_delivery.httpx.post', side_effect=httpx.ReadTimeout('private details')
        ) as send:
            result = app.test_cli_runner().invoke(args=['usage-maintenance'])
            self.assertNotEqual(result.exit_code, 0)
            self.assertNotIn('private details', result.output)
            self.assertEqual(send.call_count, 1)
        models = app.extensions['usage_ledger'].m
        db.session.remove()
        row = db.session.get(models['Month'], '2026-10')
        future = row.notification_started + 24 * 3600
        with patch.dict('os.environ', {'AI_ADMIN_EMAIL': 'admin@example.com'}), patch(
            'usage_install.time.time', return_value=future
        ), patch('email_delivery.httpx.post') as send:
            result = app.test_cli_runner().invoke(args=['usage-maintenance'])
            self.assertNotEqual(result.exit_code, 0)
            send.assert_not_called()
        db.session.expire_all()
        self.assertEqual(db.session.get(models['Month'], '2026-10').notification_state, 'unknown')

    def test_repeated_cleanup_does_not_delete_referenced_pdf(self):
        self.lecture.pdf_filename = 'still-referenced.pdf'
        db.session.add(pdf_service['Deletion'](key='still-referenced.pdf', not_before=0))
        db.session.commit()
        with patch.object(self.store, 'delete') as delete:
            pdf_service['cleanup'](detailed=True)
            pdf_service['cleanup'](detailed=True)
            delete.assert_not_called()

    def test_retry_uses_same_payload_even_if_usage_amount_changes(self):
        self.warning()
        with patch.dict('os.environ', {'AI_ADMIN_EMAIL': 'admin@example.com'}), patch(
            'email_delivery.httpx.post', side_effect=httpx.ReadTimeout('offline')
        ) as first:
            app.test_cli_runner().invoke(args=['usage-notify'])
            payload = first.call_args.kwargs['json']
        model = app.extensions['usage_ledger'].m['Month']
        db.session.remove()
        row = db.session.get(model, '2026-10')
        row.spent = 1900
        future = row.notification_started + 61
        db.session.commit()
        with patch.dict('os.environ', {'AI_ADMIN_EMAIL': 'admin@example.com'}), patch(
            'usage_install.time.time', return_value=future
        ), patch('email_delivery.httpx.post') as second:
            result = app.test_cli_runner().invoke(args=['usage-notify'])
            self.assertEqual(result.exit_code, 0)
            self.assertEqual(second.call_args.kwargs['json'], payload)

    def test_missing_notification_destination_does_not_stop_other_tasks(self):
        with patch.dict('os.environ', {'AI_ADMIN_EMAIL': ''}), patch.dict(
            pdf_service, {'cleanup': lambda **kw: {'completed': 0, 'failed': 0}}
        ):
            result = app.test_cli_runner().invoke(args=['usage-maintenance'])
            self.assertNotEqual(result.exit_code, 0)
            self.assertIn('"task": "ai_recovery", "status": "success"', result.output)
            self.assertIn('"task": "pdf_cleanup", "status": "success"', result.output)
