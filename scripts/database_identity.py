"""Compare DB endpoint identities without outputting connection credentials."""
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit, unquote

from dotenv import load_dotenv


def fingerprint(raw):
    url = urlsplit(raw)
    if not url.scheme.startswith('postgres') or not url.hostname or not url.path.strip('/'):
        raise ValueError('PostgreSQL endpoint required')
    identity = [(url.hostname or '').lower().replace('-pooler.', '.'),
                url.port or 5432, unquote(url.path.lstrip('/'))]
    return hashlib.sha256(json.dumps(identity, separators=(',', ':')).encode()).hexdigest()


def main():
    load_dotenv(Path(__file__).resolve().parents[1] / '.env', override=False)
    names = ['DATABASE_URL', 'USAGE_TEST_DATABASE_URL',
             'NEON_PRODUCTION_COMPARE_URL', 'RENDER_DATABASE_COMPARE_URL']
    values = {name: fingerprint(os.environ[name]) for name in names if os.getenv(name)}
    print(json.dumps({'endpoint_fingerprints': values,
                     'matches_local_DATABASE_URL': {
                         name: value == values.get('DATABASE_URL')
                         for name, value in values.items() if name != 'DATABASE_URL'}}, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'error_type': type(error).__name__}))
        raise SystemExit(1)
