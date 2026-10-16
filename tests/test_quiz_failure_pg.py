"""Opt-in regression for deferred stream cleanup while the usage row is locked."""
import json
import os
import unittest

import test_usage_integration_pg as fixtures


# Run in a separate interpreter: no surrounding Flask context may hide request teardown.
PROBE = r'''
import gc,json,logging,os,sys
from types import SimpleNamespace
from unittest.mock import patch
from sqlalchemy import event,text
import app as module

app,db=module.app,module.db
ledger=app.extensions['usage_ledger']
with app.app_context():
    assert db.engine.dialect.name=='postgresql'
    assert db.session.execute(text('SELECT current_schema()')).scalar()==os.environ['EXPECTED_SCHEMA']
    db.session.remove()
    @event.listens_for(db.engine,'checkout')
    def bounded_wait(connection,record,proxy):
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION lock_timeout = '1000ms'")
        connection.commit()
    control=db.session.get(ledger.m['Control'],1)
    control.stopped,control.pdf_ready=False,True
    user=module.User(email='quiz-fixture@example.com',password_hash='fixture')
    db.session.add(user);db.session.flush()
    course=module.Course(name='Quiz fixture',user_id=user.id)
    db.session.add(course);db.session.flush()
    lecture=module.Lecture(course_id=course.id,week=1,text='HTTP connects browser and server.')
    db.session.add(lecture);db.session.flush()
    user_id,lecture_id=user.id,lecture.id
    db.session.commit()

class Source:
    closed=False
    def __iter__(self):
        yield SimpleNamespace(type='response.output_text.delta',delta='{invalid\n'*10)
        yield SimpleNamespace(type='response.completed',response=SimpleNamespace(
            usage=SimpleNamespace(input_tokens=24,output_tokens=16)))
    def close(self):
        self.closed=True

source=Source()
pending=[]
records=[]
class Capture(logging.Handler):
    def emit(self,record): records.append(record)
handler=Capture()
app.logger.addHandler(handler)
app.config.update(TESTING=False)
client=app.test_client()
with client.session_transaction() as state:
    state.update(_user_id=str(user_id),auth_version=0)
assert client.get(f'/lecture/{lecture_id}/upload').status_code==200
with client.session_transaction() as state: csrf=state['security_csrf']
original=module.ai_service.stream
def capture(*args,**kwargs):
    result=original(*args,**kwargs)
    pending.append(result)
    return result

gc.disable()
try:
    with patch('app.client.responses.create',return_value=source),patch.object(
        module.ai_service,'stream',side_effect=capture):
        response=client.post(f'/lecture/{lecture_id}/quiz/generate',buffered=True,
            headers={'X-CSRF-Token':csrf,'Idempotency-Key':'quiz-format-pg-fixture'})
    closed_before_lock=source.closed
    cleanup_errors=[]
    original_hook=sys.unraisablehook
    def record_unraisable(args):
        cleanup_errors.append(getattr(getattr(args.exc_value,'orig',None),'sqlstate',
                                      type(args.exc_value).__name__))
    sys.unraisablehook=record_unraisable
    try:
        with app.app_context():
            with ledger.transaction():
                # Simulate a traceback's later collection during the next usage request.
                records.clear();pending.clear();gc.collect()
    finally:
        sys.unraisablehook=original_hook
    old=os.getenv('EXPECT_OLD_BUG')=='1'
    assert response.status_code==(500 if old else 502)
    assert closed_before_lock is (not old)
    assert ('55P03' in cleanup_errors) if old else not cleanup_errors
    if not old:
        usage=client.get('/api/ai-usage')
        assert usage.status_code==200 and usage.json['features']['quiz']['available']
    print(json.dumps({'http_status':response.status_code,
        'closed_before_usage_lock':closed_before_lock,
        'cleanup_lock_timeout':'55P03' in cleanup_errors,
        'cleanup_errors':cleanup_errors}))
finally:
    app.logger.removeHandler(handler)
    gc.enable()
    with app.app_context():
        records.clear();pending.clear();gc.collect();db.session.remove()
'''


@unittest.skipUnless(os.getenv('USAGE_TEST_DATABASE_URL'), 'Dedicated test database not supplied')
class QuizFailurePostgreSQLTests(unittest.TestCase):
    setUp = fixtures.PostgreSQLIntegrationTests.setUp
    cleanup_schema = fixtures.PostgreSQLIntegrationTests.cleanup_schema
    migrate = fixtures.PostgreSQLIntegrationTests.migrate
    run_python = fixtures.PostgreSQLIntegrationTests.run_python

    def test_format_failure_closes_stream_before_later_usage_lock(self):
        self.migrate()
        self.env.update(EXPECTED_SCHEMA=self.schema, EXPECT_OLD_BUG='0')
        result = json.loads(self.run_python(PROBE).decode())
        self.assertEqual(result['http_status'], 502)
        self.assertTrue(result['closed_before_usage_lock'])
        self.assertFalse(result['cleanup_lock_timeout'])
