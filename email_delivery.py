"""Shared Resend delivery; never log payloads, recipients or credentials."""
import os
import httpx


def send_email(payload, idempotency_key):
    key = os.getenv('RESEND_API_KEY')
    if not key:
        raise RuntimeError('Email configuration is incomplete')
    response = httpx.post('https://api.resend.com/emails',
        headers={'Authorization': 'Bearer ' + key, 'Idempotency-Key': idempotency_key},
        json=payload, timeout=15)
    response.raise_for_status()
    return response
