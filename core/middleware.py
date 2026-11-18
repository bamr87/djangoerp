"""
Request tracing headers (hub specs/BACKEND.md UPS-BE-04).

``RequestIdMiddleware`` sits first in ``MIDDLEWARE`` so that every response carries ``X-Request-Id`` and
``X-App-Version``: pages, API errors, static files WhiteNoise serves and SecurityMiddleware's HTTPS redirects alike.
"""

import re
import uuid

from django.conf import settings

# What an upstream proxy or a client correlating its own logs sends: UUIDs, hex and base64-style trace ids, at most
# 200 characters. Anything else (spaces, quotes, control characters, a megabyte of text) is replaced, never echoed.
WELL_FORMED_REQUEST_ID = re.compile(r'[A-Za-z0-9._:+/=-]{1,200}')


class RequestIdMiddleware:
    """
    Echo a well-formed inbound ``X-Request-Id`` or assign a fresh ``uuid4().hex``, and stamp ``X-App-Version``.

    The id is also set as ``request.request_id`` for code that wants to log it.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        inbound = request.headers.get('X-Request-Id', '')
        request.request_id = inbound if WELL_FORMED_REQUEST_ID.fullmatch(inbound) else uuid.uuid4().hex
        response = self.get_response(request)
        response['X-Request-Id'] = request.request_id
        response['X-App-Version'] = settings.APP_VERSION
        return response
