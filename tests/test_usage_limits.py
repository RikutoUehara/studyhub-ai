from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from unittest.mock import Mock
import unittest
import uuid

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event

from usage_models import define_usage_models
from usage_ledger import UsageLedger, LimitReached, JST
from abuse_control import AbuseControl
from ai_service import AIService, input_count, bounded_chat


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(SQLALCHEMY_DATABASE_URI='sqlite://', SECRET_KEY='test-secret')
        self.db = SQLAlchemy(self.app)
        self.m = define_usage_models(self.db)
        self.context = self.app.app_context()
        self.context.push()
        self.db.create_all()
        self.db.session.add(self.m['Control'](id=1, stopped=False, pdf_ready=True))
        self.db.session.commit()
        self.ledger = UsageLedger(self.db, self.m)
        self.abuse = AbuseControl(self.ledger)

    def tearDown(self):
        self.db.session.remove()
        self.db.drop_all()
        self.db.engine.dispose()
        self.context.pop()

    def reserve(self, user=1, feature='summary', key=None):
        return self.ledger.reserve(user, feature, 'gpt-5.4-mini', 100, key or str(uuid.uuid4()))

    def test_user_independence_daily_limit_and_input(self):
        for _ in range(10):
            identity = self.reserve()
            self.ledger.started(identity)
            self.ledger.settle(identity, 100, 10)
        with self.assertRaises(LimitReached):
            self.reserve()
        self.reserve(user=2)
        with self.assertRaises(LimitReached):
            self.ledger.reserve(3, 'summary', 'gpt-5.4-mini', 30001, 'too-long')

    def test_concurrency_and_duplicate(self):
        self.reserve(key='unique')
        with self.assertRaises(LimitReached):
            self.reserve(key='unique')
        with self.assertRaises(LimitReached):
            self.reserve()
        self.reserve(user=2)
        self.reserve(user=3)
        with self.assertRaises(LimitReached):
            self.reserve(user=4)

    def test_budget_and_stop_refuse_before_provider(self):
        with patch('usage_ledger.settings', return_value={
            'prices': {'gpt-5.4-mini': (Decimal('.75'), Decimal('4.5'))},
            'fx': Decimal(200), 'stop': Decimal(1), 'warning': Decimal('.5')
        }):
            with self.assertRaises(LimitReached):
                self.reserve()
        self.ledger.stop(True)
        with self.assertRaises(LimitReached):
            self.reserve()
        self.assertEqual(self.db.session.query(self.m['Request']).count(), 0)

    def test_success_idempotent_settlement_unknown_and_late_completion(self):
        identity = self.reserve()
        self.ledger.started(identity)
        self.ledger.settle(identity)
        self.ledger.settle(identity)
        self.db.session.expire_all()
        row = self.db.session.get(self.m['Request'], identity)
        month = self.db.session.get(self.m['Month'], row.month)
        self.assertEqual(month.uncertain, row.reservation)
        self.assertEqual(month.reserved, 0)
        self.ledger.settle(identity, 100, 10)
        self.ledger.settle(identity, 100, 10)
        self.db.session.expire_all()
        self.assertEqual(month.uncertain, 0)
        self.assertEqual(month.spent, row.cost)
        self.assertFalse(row.active)

    def test_expiry_unsent_and_started(self):
        unsent = self.reserve(user=1)
        started = self.reserve(user=2)
        self.ledger.started(started)
        with patch('usage_ledger.time.time', return_value=10**12):
            self.ledger.reap()
        self.db.session.expire_all()
        self.assertEqual(self.db.session.get(self.m['Request'], unsent).status, 'cancelled')
        unknown = self.db.session.get(self.m['Request'], started)
        self.assertEqual(unknown.status, 'uncertain')
        self.assertFalse(unknown.active)
        self.assertGreater(self.db.session.get(self.m['Month'], unknown.month).uncertain, 0)

    def test_jst_month_change_old_month_settled(self):
        class Before(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 10, 31, 23, 59, tzinfo=JST)
        class After(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 11, 1, 0, 1, tzinfo=JST)
        with patch('usage_ledger.datetime', Before):
            identity = self.reserve()
            self.ledger.started(identity)
        with patch('usage_ledger.datetime', After):
            self.ledger.settle(identity, 100, 10)
            next_id = self.reserve()
        self.assertEqual(self.db.session.get(self.m['Request'], identity).month, '2026-10')
        self.assertEqual(self.db.session.get(self.m['Request'], next_id).month, '2026-11')

    def test_pdf_daily_and_total_capacity(self):
        for index in range(20):
            self.abuse.pdf_reserve(1, 'key-' + str(index), 1)
        with self.assertRaises(LimitReached):
            self.abuse.pdf_reserve(1, 'over-count', 1)
        self.abuse.pdf_reserve(2, 'full', 100 * 1024 * 1024)
        with self.assertRaises(LimitReached):
            self.abuse.pdf_reserve(2, 'over-capacity', 1)
        self.abuse.pdf_deleted('full')
        self.abuse.pdf_deleted('full')
        self.abuse.pdf_reserve(2, 'new', 1)
        self.db.session.expire_all()
        self.assertEqual(self.db.session.get(self.m['PdfQuota'], 2).bytes, 1)

    def test_mail_gate_shared_and_identifiers_hashed(self):
        self.abuse.email_send(1, 'verify')
        with self.assertRaises(LimitReached):
            self.abuse.email_send(1, 'verify')
        self.abuse.email_send(2, 'verify')
        subject = 'student@example.com'
        self.abuse.consume([('test', subject, 60, 1)])
        keys = [item.key for item in self.db.session.query(self.m['Bucket']).all()]
        self.assertTrue(all(subject not in key for key in keys))

    def test_bounded_chat_keeps_latest_and_input_includes_everything(self):
        messages = [{'role': 'system', 'content': 'source ' * 20000}]
        messages += [{'role': 'user', 'content': 'old ' * 1000} for _ in range(60)]
        messages.append({'role': 'user', 'content': 'latest question'})
        result = bounded_chat(messages)
        self.assertLessEqual(input_count(result), 12000)
        self.assertEqual(result[-1], messages[-1])
        self.assertLess(len(result), len(messages))

    def test_stream_timeout_and_disconnect_keep_reserved_cost(self):
        service = AIService(self.db, self.ledger, SimpleNamespace())
        for user, mode in ((1, 'failure'), (2, 'disconnect')):
            identity = self.reserve(user=user)
            self.ledger.started(identity)
            def events():
                yield SimpleNamespace(type='response.output_text.delta', delta='hello')
                raise TimeoutError('not logged')
            provider = SimpleNamespace(responses=SimpleNamespace(create=lambda **kw: events()))
            service.client = provider
            with patch.object(service, 'prepare', return_value=identity):
                stream = service.stream('summary', model='gpt-5.4-mini', input='hello')
                next(stream)
                if mode == 'failure':
                    with self.assertRaises(LimitReached):
                        next(stream)
                else:
                    stream.close()
            self.db.session.expire_all()
            self.assertEqual(self.db.session.get(self.m['Request'], identity).status, 'uncertain')

    def test_provider_success_caps_and_stop_never_calls_provider(self):
        usage = SimpleNamespace(input_tokens=20, output_tokens=10)
        create = Mock(return_value=iter([
            SimpleNamespace(type='response.output_text.delta', delta='hello'),
            SimpleNamespace(type='response.completed', response=SimpleNamespace(usage=usage)),
        ]))
        provider = SimpleNamespace(responses=SimpleNamespace(create=create))
        service = AIService(self.db, self.ledger, provider)
        with self.app.test_request_context('/ai', headers={'Idempotency-Key': 'abcdefgh1234'}), patch(
            'ai_service.current_user', SimpleNamespace(id=1)
        ):
            list(service.stream('summary', model='gpt-5.4-mini', input='hello', stream=True))
        self.assertEqual(create.call_args.kwargs['max_output_tokens'], 4000)
        self.assertFalse(create.call_args.kwargs['store'])
        self.assertEqual(self.db.session.query(self.m['Request']).one().status, 'completed')
        self.ledger.stop(True)
        create.reset_mock()
        with self.app.test_request_context('/ai'), patch('ai_service.current_user', SimpleNamespace(id=2)):
            with self.assertRaises(LimitReached):
                service.stream('summary', model='gpt-5.4-mini', input='hello', stream=True)
        create.assert_not_called()
