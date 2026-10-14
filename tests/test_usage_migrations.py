import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class MigrationTests(unittest.TestCase):
    def test_fresh_database_upgrade_starts_stopped(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, 'migration.db').as_posix()
            environment = os.environ.copy()
            environment.update(APP_ENV='development', DATABASE_URL='sqlite:///' + path,
                               SECRET_KEY='test-secret', OPENAI_API_KEY='test-key')
            result = subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'],
                cwd=root, env=environment, capture_output=True, encoding='utf-8', errors='replace', timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            import sqlite3
            connection = sqlite3.connect(path)
            try:
                self.assertEqual(connection.execute('SELECT stopped,pdf_ready FROM usage_control').fetchone(), (1, 0))
                self.assertEqual(connection.execute('SELECT version_num FROM alembic_version').fetchone()[0], '0002_usage_controls')
            finally:
                connection.close()

    def test_frozen_revisions_ignore_future_application_metadata(self):
        import importlib.util
        from unittest.mock import patch
        from sqlalchemy import create_engine, inspect, text, Column, String
        from app import db
        root = Path(__file__).resolve().parents[1]
        engine = create_engine('sqlite:///:memory:')
        future = Column('future_revision_probe', String(10))
        db.metadata.tables['user'].append_column(future)
        try:
            with engine.begin() as connection:
                for name in ('0001_baseline', '0002_usage_controls'):
                    spec = importlib.util.spec_from_file_location(name, root / 'migrations/versions' / (name + '.py'))
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    with patch.object(module.op, 'get_bind', return_value=connection):
                        module.upgrade()
                self.assertNotIn('future_revision_probe', {c['name'] for c in inspect(connection).get_columns('user')})
                self.assertEqual(connection.execute(text('SELECT stopped,pdf_ready FROM usage_control')).one(), (1, 0))
                self.assertEqual(connection.execute(text('SELECT count(*) FROM usage_month')).scalar(), 0)
                self.assertEqual({i['name'] for i in inspect(connection).get_indexes('usage_request')}, {'ix_usage_request_user_id'})
        finally:
            db.metadata.tables['user']._columns.remove(future)
            engine.dispose()

    def test_existing_database_preserved_and_mismatch_rejected(self):
        import importlib.util
        from unittest.mock import patch
        from sqlalchemy import create_engine, text
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('baseline_test', root / 'migrations/versions/0001_baseline.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        engine = create_engine('sqlite:///:memory:')
        try:
            with engine.begin() as connection:
                module.metadata.create_all(connection)
                connection.execute(text("INSERT INTO user(id,email,password_hash) VALUES (1,'fixture@example.com','fixture')"))
                with patch.object(module.op, 'get_bind', return_value=connection):
                    module.upgrade()
                    module.upgrade()
                    self.assertEqual(connection.execute(text('SELECT count(*) FROM user')).scalar(), 1)
                    connection.execute(text('ALTER TABLE user ADD COLUMN unexpected TEXT'))
                    with self.assertRaisesRegex(RuntimeError, 'Existing schema differs'):
                        module.upgrade()
                    self.assertEqual(connection.execute(text('SELECT count(*) FROM user')).scalar(), 1)
        finally:
            engine.dispose()
