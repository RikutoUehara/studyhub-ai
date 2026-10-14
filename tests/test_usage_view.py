from unittest.mock import patch
import unittest
from datetime import datetime
from decimal import Decimal
import test_pdf_storage as fixtures
from test_pdf_storage import app, db, g
from usage_ledger import JST


class UsageViewTests(unittest.TestCase):
    setUp = fixtures.PdfTests.setUp
    tearDown = fixtures.PdfTests.tearDown
    client_for = fixtures.PdfTests.client_for

    def get(self, client=None, query=''):
        g.pop('_login_user', None)
        return (client or self.client).get('/api/ai-usage' + query)

    def test_independence_reservations_and_no_user_override(self):
        ledger = app.extensions['usage_ledger']
        ledger.reserve(self.user.id, 'chat', 'gpt-5.4-nano', 100, 'usage-view-request')
        own = self.get().json
        self.assertEqual(own['features']['chat']['used'], 1)
        self.assertEqual(own['features']['chat']['remaining'], 59)
        self.assertEqual(own['features']['chat']['reason'], 'busy')
        self.assertEqual(self.get(self.client_for(self.other.id)).json['features']['chat']['used'], 0)
        self.assertEqual(self.get(query='?user_id=1').status_code, 400)
        self.assertEqual(self.get(app.test_client()).status_code, 302)
        self.assertEqual(self.get().headers['Cache-Control'], 'private, no-store')

    def test_daily_budget_stop_and_emergency_distinct(self):
        ledger = app.extensions['usage_ledger']
        now = datetime.now(JST)
        db.session.add(ledger.m['Day'](key=f'{self.user.id}:{now:%Y-%m-%d}:summary', count=10))
        db.session.commit()
        self.assertEqual(self.get().json['features']['summary']['reason'], 'daily_limit')
        db.session.add(ledger.m['Month'](month=now.strftime('%Y-%m'), spent=2400, reserved=0, uncertain=0))
        db.session.commit()
        self.assertEqual(self.get().json['features']['chat']['reason'], 'budget_stop')
        ledger.stop(True)
        self.assertEqual(self.get().json['features']['chat']['reason'], 'emergency_stop')
        self.assertEqual(self.get().json['features']['chat']['remaining'], 60)

    def test_reset_and_shared_panel_placement(self):
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 10, 31, 23, 59, tzinfo=JST)
        with patch('usage_view.datetime', Clock):
            self.assertEqual(self.get().json['resets_at'], '2026-11-01T00:00:00+09:00')
        for path in ('/', f'/lecture/{self.id}/upload'):
            html = self.client.get(path).get_data(as_text=True)
            self.assertIn('data-ai-usage-url="/api/ai-usage"', html)
            self.assertIn('午前0時', html)

    def test_pdf_save_and_read_work_during_ai_stop(self):
        ledger = app.extensions['usage_ledger']
        ledger.stop(True)
        self.client.get(f'/lecture/{self.id}/upload')
        with self.client.session_transaction() as state:
            csrf = state['pdf_csrf']
        from io import BytesIO
        response = self.client.post(f'/lecture/{self.id}/upload', data={
            'csrf_token': csrf, 'pdf': (BytesIO(fixtures.sample_pdf()), 'study.pdf')})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(response.json['pdf_url']).status_code, 200)

    def test_combined_maintenance_and_notification_are_idempotent(self):
        models = app.extensions['usage_ledger'].m
        db.session.add(models['Month'](month='2026-10', spent=1800, reserved=0, uncertain=0, warned=True))
        db.session.commit()
        with patch.dict('os.environ', {'AI_ADMIN_EMAIL': 'admin@example.com'}), patch(
            'usage_install.httpx.post'
        ) as send:
            send.return_value.raise_for_status.return_value = None
            runner = app.test_cli_runner()
            self.assertEqual(runner.invoke(args=['usage-maintenance']).exit_code, 0)
            self.assertEqual(runner.invoke(args=['usage-maintenance']).exit_code, 0)
            self.assertEqual(send.call_count, 1)
