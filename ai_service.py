"""All model calls pass through reservations and conservative token/cost accounting."""
import hashlib
import json
import math
import uuid
import time

from flask import current_app, request
from flask_login import current_user
import tiktoken

from usage_ledger import LimitReached
from usage_policy import LIMITS


def input_count(value):
    serialized = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    if len(serialized.encode('utf-8')) > 1024 * 1024:
        raise LimitReached('入力が長すぎます。資料や質問を短くしてください。', 413)
    count = len(tiktoken.get_encoding('o200k_base').encode(serialized, disallowed_special=()))
    # Local estimation includes serialized roles/content and a margin for API formatting.
    return math.ceil(count * 1.15) + 256


def bounded_chat(messages):
    """Keep instructions/latest question, bounded source excerpt and recent history."""
    limit = LIMITS['chat'][1]
    encoder = tiktoken.get_encoding('o200k_base')
    system = dict(messages[0])
    system['content'] = encoder.decode(encoder.encode(system['content'][:100000], disallowed_special=())[:6500])
    latest = messages[-1]
    result = [system, latest]
    if input_count(result) > limit:
        raise LimitReached('質問が長すぎます。短くして再度お試しください。', 413)
    for old in reversed(messages[1:-1]):
        candidate = [system, old, *result[1:]]
        if input_count(candidate) > limit:
            break
        result = candidate
    return result


class AIService:
    def __init__(self, db, ledger, client):
        self.db, self.ledger, self.client = db, ledger, client

    def prepare(self, feature, kwargs):
        model = kwargs['model']
        value = kwargs.get('input', kwargs.get('messages'))
        if feature == 'chat':
            value = bounded_chat(value)
            kwargs['input'] = value
        count = input_count(value)
        supplied = request.headers.get('Idempotency-Key')
        if supplied and not 8 <= len(supplied) <= 128:
            raise LimitReached('リクエスト識別子が不正です。', 400)
        identity = f'{request.endpoint}:{supplied or uuid.uuid4()}'
        user_id = current_user.id
        # Release the ORM read transaction before starting network communication.
        self.db.session.commit()
        request_id = self.ledger.reserve(user_id, feature, model, count, identity)
        try:
            self.ledger.started(request_id)
        except Exception:
            self.ledger.settle(request_id, unsent=True)
            raise
        return request_id

    def stream(self, feature, **kwargs):
        request_id = self.prepare(feature, kwargs)
        kwargs['max_output_tokens'] = LIMITS[feature][2]
        kwargs['store'] = False

        def events():
            usage = None
            stream = None
            try:
                stream = self.client.responses.create(**kwargs)
                heartbeat_at = time.monotonic()
                for event in stream:
                    if time.monotonic() - heartbeat_at > 30:
                        self.ledger.heartbeat(request_id)
                        heartbeat_at = time.monotonic()
                    if event.type in ('response.completed', 'response.incomplete', 'response.failed'):
                        usage = getattr(getattr(event, 'response', None), 'usage', None)
                        if event.type != 'response.completed':
                            raise RuntimeError('Model response did not complete')
                    yield event
                if usage is None:
                    raise RuntimeError('Missing usage')
            except GeneratorExit:
                raise
            except Exception:
                # Do not log upstream exceptions: request bodies may be embedded in them.
                current_app.logger.warning('AI stream interrupted; usage accounted conservatively')
                raise LimitReached('AI処理を完了できませんでした。時間をおいてお試しください。', 503) from None
            finally:
                try:
                    if stream is not None and hasattr(stream, 'close'):
                        stream.close()
                finally:
                    if usage is not None:
                        self.ledger.settle(request_id, usage.input_tokens, usage.output_tokens)
                    else:
                        self.ledger.settle(request_id)
        return events()

    def completion(self, feature, **kwargs):
        request_id = self.prepare(feature, kwargs)
        kwargs['max_completion_tokens'] = LIMITS[feature][2]
        kwargs['store'] = False
        try:
            response = self.client.chat.completions.create(**kwargs)
            usage = response.usage
            self.ledger.settle(request_id, usage.prompt_tokens, usage.completion_tokens)
            return response
        except Exception:
            self.ledger.settle(request_id)
            raise LimitReached('採点を完了できませんでした。時間をおいてお試しください。', 503) from None


def prime_stream(iterator):
    """Reserve/validate before headers; signal later failures without replaying the API call."""
    try:
        first = next(iterator)
    except StopIteration:
        first = ''

    def chunks():
        try:
            yield first
            yield from iterator
        except LimitReached as error:
            yield '\n[AI_ERROR] ' + error.message
        finally:
            iterator.close()
    return chunks()
