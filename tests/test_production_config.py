import unittest
from unittest.mock import patch
from flask import Flask
from production_config import configure


class ProductionConfigTests(unittest.TestCase):
    def test_production_cookies_health_and_errors(self):
        environment = {name: 'test' for name in ('DATABASE_URL', 'OPENAI_API_KEY', 'RESEND_API_KEY', 'MAIL_FROM',
                       'R2_ACCOUNT_ID', 'R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY', 'R2_BUCKET_NAME')}
        environment.update(APP_ENV='production', PUBLIC_BASE_URL='https://example.com')
        app = Flask(__name__)
        app.config['SECRET_KEY'] = 'x' * 40
        with patch.dict('os.environ', environment, clear=True):
            configure(app)
        self.assertTrue(app.config['SESSION_COOKIE_SECURE'])
        self.assertTrue(app.config['SESSION_COOKIE_HTTPONLY'])
        self.assertFalse(app.debug)
        @app.route('/failure')
        def failure():
            raise RuntimeError('private-details')

        response = app.test_client().get('/healthz')
        self.assertEqual(response.json, {'status': 'ok'})
        self.assertIn('Strict-Transport-Security', response.headers)
        response = app.test_client().get('/failure')
        self.assertEqual(response.status_code, 500)
        self.assertNotIn('private-details', response.get_data(as_text=True))

    def test_missing_production_settings_rejected(self):
        app = Flask(__name__)
        with patch.dict('os.environ', {'APP_ENV': 'production'}, clear=True):
            with self.assertRaises(RuntimeError):
                configure(app)

    def test_development_http_supported(self):
        app = Flask(__name__)
        with patch.dict('os.environ', {'APP_ENV': 'development'}, clear=True):
            configure(app)
        self.assertFalse(app.config['SESSION_COOKIE_SECURE'])
