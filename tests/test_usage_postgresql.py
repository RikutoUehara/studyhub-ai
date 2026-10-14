"""Opt-in: isolated temporary schema in an explicitly supplied test database."""
from concurrent.futures import ThreadPoolExecutor
import os
import unittest
import uuid
from pathlib import Path
from urllib.parse import urlsplit
from dotenv import load_dotenv, dotenv_values

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text, select, update
from datetime import datetime
from usage_ledger import JST
from abuse_control import AbuseControl
from usage_models import define_usage_models
from usage_ledger import UsageLedger, LimitReached

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env', override=False)


@unittest.skipUnless(os.getenv('USAGE_TEST_DATABASE_URL'), 'Dedicated PostgreSQL test connection not supplied')
class PostgreSQLConcurrencyTests(unittest.TestCase):
    def test_atomic_reservations_across_connections(self):
        configured = dotenv_values(ROOT / '.env')
        def target(value):
            parsed = urlsplit(value or '')
            return ((parsed.hostname or '').replace('-pooler.', '.'), parsed.path)
        if target(os.environ['USAGE_TEST_DATABASE_URL']) == target(configured.get('DATABASE_URL')):
            self.fail('Dedicated test connection must not target the application database')
        app = Flask(__name__)
        app.config['SQLALCHEMY_DATABASE_URI'] = os.environ['USAGE_TEST_DATABASE_URL'].replace(
            'postgresql://', 'postgresql+psycopg://', 1)
        db = SQLAlchemy(app)
        models = define_usage_models(db)
        schema = 'usage_test_' + uuid.uuid4().hex
        with app.app_context():
            if db.engine.dialect.name != 'postgresql':
                self.fail('USAGE_TEST_DATABASE_URL must be PostgreSQL')
            with db.engine.begin() as connection:
                connection.execute(text('CREATE SCHEMA ' + schema))
            engine = db.engine.execution_options(schema_translate_map={None: schema})
            db.metadata.create_all(engine)
            with engine.begin() as connection:
                connection.execute(models['Control'].__table__.insert().values(id=1, stopped=False, pdf_ready=True))
            ledger = UsageLedger(type('DB', (), {'engine': engine})(), models)
            try:
                def reserve(user):
                    try:
                        ledger.reserve(user, 'summary', 'gpt-5.4-mini', 100, str(uuid.uuid4()))
                        return True
                    except LimitReached:
                        return False
                with ThreadPoolExecutor(max_workers=8) as workers:
                    outcomes = list(workers.map(reserve, [1] * 8))
                self.assertEqual(sum(outcomes), 1)
                with ThreadPoolExecutor(max_workers=8) as workers:
                    outcomes = list(workers.map(reserve, range(2, 10)))
                self.assertEqual(sum(outcomes), 2)
                with engine.connect() as connection:
                    identities = connection.execute(select(models['Request'].id)).scalars().all()
                for identity in identities:
                    ledger.settle(identity, 0, 0)
                month = datetime.now(JST).strftime('%Y-%m')
                with engine.begin() as connection:
                    connection.execute(update(models['Month']).where(models['Month'].month == month).values(spent=2390))
                with ThreadPoolExecutor(max_workers=8) as workers:
                    outcomes = list(workers.map(reserve, range(20, 28)))
                self.assertEqual(sum(outcomes), 1)
                with engine.begin() as connection:
                    connection.execute(models['PdfQuota'].__table__.insert().values(user_id=50, bytes=95 * 1024 * 1024))
                abuse = AbuseControl(ledger)
                def upload(_):
                    try:
                        abuse.pdf_reserve(50, str(uuid.uuid4()), 4 * 1024 * 1024)
                        return True
                    except LimitReached:
                        return False
                with ThreadPoolExecutor(max_workers=8) as workers:
                    outcomes = list(workers.map(upload, range(8)))
                self.assertEqual(sum(outcomes), 1)
            finally:
                db.session.remove()
                with db.engine.begin() as connection:
                    connection.execute(text('DROP SCHEMA ' + schema + ' CASCADE'))
                db.engine.dispose()
