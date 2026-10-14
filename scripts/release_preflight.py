"""Read-only release checks. Never print credentials, identities or object keys."""
import importlib.util
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url


def main():
    load_dotenv(ROOT / '.env', override=False)
    result = {}
    engine = None
    keys = []
    try:
        url = make_url(os.environ['DATABASE_URL'])
        if not url.drivername.startswith('postgresql'):
            raise ValueError('PostgreSQL required')
        test = make_url(os.environ['USAGE_TEST_DATABASE_URL'])
        target = lambda u: ((u.host or '').replace('-pooler.', '.'), u.database)
        if target(url) == target(test):
            raise ValueError('Production and test targets coincide')
        result['production_test_distinct'] = True
        engine = create_engine(url.set(drivername='postgresql+psycopg'),
                               connect_args={'connect_timeout': 10})
        with engine.connect() as connection:
            with connection.begin():
                connection.execute(text('SET TRANSACTION READ ONLY'))
                connection.execute(text("SET LOCAL statement_timeout = '15s'"))
                spec = importlib.util.spec_from_file_location('frozen_baseline', ROOT / 'migrations/versions/0001_baseline.py')
                revision = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(revision)
                inspector = inspect(connection)
                missing = [name for name in revision.metadata.tables if not inspector.has_table(name)]
                result['missing_baseline_tables'] = missing
                if not missing:
                    with patch.object(revision.op, 'get_bind', return_value=connection):
                        revision.upgrade()  # All tables exist: validation only, under READ ONLY.
                    result['baseline_schema'] = 'PASS'
                else:
                    result['baseline_schema'] = 'NEEDS_REVIEW'
                result['record_counts'] = {
                    name: connection.execute(text('SELECT count(*) FROM "' + name + '"')).scalar()
                    for name in revision.metadata.tables if name not in missing
                }
                result['usage_tables_present'] = sorted(set(inspector.get_table_names()) & {
                    'usage_control', 'usage_month', 'usage_day', 'usage_request',
                    'abuse_bucket', 'pdf_quota', 'pdf_object'})
                if inspector.has_table('alembic_version'):
                    result['alembic_versions'] = list(connection.execute(text('SELECT version_num FROM alembic_version')).scalars())
                else:
                    result['alembic_versions'] = []
                if inspector.has_table('lecture'):
                    keys = list(connection.execute(text('SELECT DISTINCT pdf_filename FROM lecture WHERE pdf_filename IS NOT NULL')).scalars())
    except Exception as error:
        result['database_error_type'] = type(error).__name__
    finally:
        if engine:
            engine.dispose()
    try:
        from pdf_storage import R2Storage
        storage = R2Storage()
        storage.client.head_bucket(Bucket=storage.bucket)
        result['r2_bucket_access'] = 'PASS'
        total = 0
        failures = 0
        for key in keys:
            try:
                total += storage.client.head_object(Bucket=storage.bucket, Key=key)['ContentLength']
            except Exception:
                failures += 1
        result['referenced_pdf_metadata'] = {'objects': len(keys), 'bytes': total, 'failed': failures}
        result['r2_public_access_configuration'] = 'NOT_VERIFIED_BY_S3_HEAD'
    except Exception as error:
        result['r2_error_type'] = type(error).__name__
    result['render_management_access'] = 'NOT_CONNECTED'
    result['admin_email_configured_locally'] = bool(os.getenv('AI_ADMIN_EMAIL'))
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return int('database_error_type' in result or 'r2_error_type' in result
               or result.get('baseline_schema') != 'PASS'
               or result.get('referenced_pdf_metadata', {}).get('failed', 0) > 0)


if __name__ == '__main__':
    raise SystemExit(main())
