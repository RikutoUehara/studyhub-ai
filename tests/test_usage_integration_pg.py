"""Dedicated DB only; R2/OpenAI are never called by these integration tests."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid

from dotenv import dotenv_values, load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env', override=False)


@unittest.skipUnless(os.getenv('USAGE_TEST_DATABASE_URL'), 'Dedicated test database not supplied')
class PostgreSQLIntegrationTests(unittest.TestCase):
    def setUp(self):
        values = dotenv_values(ROOT / '.env')
        raw = os.environ['USAGE_TEST_DATABASE_URL'].replace('postgresql://', 'postgresql+psycopg://', 1)
        test = make_url(raw)
        production = make_url(values['DATABASE_URL'])
        def target(url):
            return ((url.host or '').replace('-pooler.', '.'), url.database)
        self.assertNotEqual(target(test), target(production))
        self.schema = 'usage_test_' + uuid.uuid4().hex
        self.admin = create_engine(raw)
        with self.admin.begin() as connection:
            connection.execute(text('CREATE SCHEMA ' + self.schema))
        self.addCleanup(self.cleanup_schema)
        # Neon transaction pooling rejects search_path startup options; use the same
        # branch's direct endpoint for isolated-schema migration/process tests.
        test = test.set(host=(test.host or '').replace('-pooler.', '.'))
        self.url = test.update_query_dict({'options': '-csearch_path=' + self.schema}).render_as_string(hide_password=False)
        self.env = os.environ.copy()
        self.env.update(DATABASE_URL=self.url, APP_ENV='development', SECRET_KEY='test-secret',
                        OPENAI_API_KEY='test-key', RESEND_API_KEY='test-key')
        self.engine = create_engine(self.url)
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(text('SELECT current_schema()')).scalar(), self.schema)

    def cleanup_schema(self):
        if hasattr(self, 'engine'):
            self.engine.dispose()
        with self.admin.begin() as connection:
            connection.execute(text('DROP SCHEMA ' + self.schema + ' CASCADE'))
        self.admin.dispose()

    def run_python(self, source, expected=0):
        result = subprocess.run([sys.executable, '-c', source], cwd=ROOT, env=self.env,
                                capture_output=True, timeout=60)
        # Avoid displaying any database exception that could include credentials.
        self.assertEqual(result.returncode, expected, 'Isolated test subprocess failed')
        return result.stdout

    def migrate(self):
        result = subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], cwd=ROOT,
                                env=self.env, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, 'Isolated migration failed')

    def test_existing_data_migration_reexecution_and_mock_pdf_initialization(self):
        self.run_python('''
from app import app,db,User,Course,Lecture
with app.app_context():
    legacy={'user','course','lecture','chat_message','quiz_attempt','email_auth','email_token','pdf_deletion'}
    for table in db.metadata.sorted_tables:
        if table.name in legacy: table.create(db.engine)
    user=User(id=1,email='fixture@example.com',password_hash='fixture')
    db.session.add(user);db.session.flush()
    db.session.add(Course(id=1,user_id=1,name='Preserved course'));db.session.flush()
    db.session.add(Lecture(id=76,course_id=1,week=1,pdf_filename='users/1/lectures/76/fixture.pdf',text='Preserved text'))
    db.session.commit()
''')
        self.migrate()
        self.migrate()
        with self.engine.connect() as connection:
            row = connection.execute(text('SELECT text,pdf_filename FROM lecture WHERE id=76')).one()
            self.assertEqual(row[0], 'Preserved text')
            self.assertEqual(row[1], 'users/1/lectures/76/fixture.pdf')
            self.assertEqual(connection.execute(text('SELECT count(*) FROM "user"')).scalar(), 1)
        self.run_python('''
from unittest.mock import patch,MagicMock
from app import app
fake=MagicMock();fake.client.head_object.return_value={'ContentLength':4194304}
with patch('pdf_storage.R2Storage',return_value=fake):
    runner=app.test_cli_runner()
    first=runner.invoke(args=['usage-init-pdfs']); assert first.exit_code==0
    second=runner.invoke(args=['usage-init-pdfs']); assert second.exit_code!=0
    assert fake.client.head_object.call_count==1
''')
        self.migrate()
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(text('SELECT bytes FROM pdf_quota WHERE user_id=1')).scalar(), 4194304)
            self.assertEqual(connection.execute(text('SELECT count(*) FROM pdf_object')).scalar(), 1)
            self.assertEqual(connection.execute(text('SELECT pdf_ready FROM usage_control')).scalar(), True)

    def test_multiple_processes_exit_recovery_and_late_double_settlement(self):
        self.migrate()
        with self.engine.begin() as connection:
            connection.execute(text('UPDATE usage_control SET stopped=false,pdf_ready=true'))
        worker = '''
from app import app
from usage_ledger import LimitReached
import uuid,os
with app.app_context():
    ledger=app.extensions['usage_ledger']
    try:
        identity=ledger.reserve(1,'chat','gpt-5.4-nano',100,str(uuid.uuid4()))
        ledger.started(identity)
        os._exit(0)
    except LimitReached: os._exit(3)
'''
        def execute(_):
            return subprocess.run([sys.executable, '-c', worker], cwd=ROOT, env=self.env,
                                  capture_output=True, timeout=60).returncode
        with ThreadPoolExecutor(max_workers=4) as workers:
            results = list(workers.map(execute, range(4)))
        self.assertEqual(results.count(0), 1)
        self.assertEqual(results.count(3), 3)
        with self.engine.begin() as connection:
            connection.execute(text('UPDATE usage_request SET expires=0'))
        self.run_python('''
from app import app,db
with app.app_context():
    ledger=app.extensions['usage_ledger'];ledger.reap()
    row=db.session.query(ledger.m['Request']).one()
    assert row.status=='uncertain' and not row.active
    identity=row.id
    ledger.settle(identity,100,10)
    ledger.settle(identity,100,10)
    db.session.expire_all()
    row=db.session.get(ledger.m['Request'],identity)
    month=db.session.get(ledger.m['Month'],row.month)
    assert month.uncertain==0 and month.reserved==0 and month.spent==row.cost
    ledger.reserve(1,'chat','gpt-5.4-nano',100,'after-restart')
''')

    def test_multiple_processes_claim_one_budget_notification(self):
        self.migrate()
        with self.engine.begin() as connection:
            connection.execute(text("INSERT INTO usage_month(month,spent,reserved,uncertain,warned,notified,notification_started,notification_state,notification_lease_until) VALUES ('2026-10',1800,0,0,true,false,0,'pending',0)"))
        worker = '''
from app import app
from unittest.mock import patch
import os,time
os.environ['AI_ADMIN_EMAIL']='admin@example.com'
def send(*args,**kwargs):
    print('SENT',flush=True)
    time.sleep(1)
with patch('usage_install.send_email',side_effect=send):
    result=app.test_cli_runner().invoke(args=['usage-notify'])
    if 'SENT' in result.output: print('SENT',flush=True)
    raise SystemExit(result.exit_code)
'''
        def execute(_):
            result = subprocess.run([sys.executable, '-c', worker], cwd=ROOT, env=self.env,
                                    capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, 'Isolated notification process failed')
            return result.stdout.count(b'SENT')
        with ThreadPoolExecutor(max_workers=4) as workers:
            results = list(workers.map(execute, range(4)))
        self.assertEqual(sum(results), 1)
