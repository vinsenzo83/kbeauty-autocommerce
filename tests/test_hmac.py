from __future__ import annotations

import base64
import hashlib
import hmac

import pytest

from app.utils.hmac_verify import verify_shopify_hmac

SECRET = "test-webhook-secret"


def _sign(body: bytes, secret: str = SECRET) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


class TestVerifyShopifyHmac:
    def test_valid_signature_returns_true(self) -> None:
        body = b'{"id":1,"email":"test@example.com"}'
        signature = _sign(body)
        assert verify_shopify_hmac(body, SECRET, signature) is True

    def test_wrong_secret_returns_false(self) -> None:
        body = b'{"id":1}'
        signature = _sign(body, secret="wrong-secret")
        assert verify_shopify_hmac(body, SECRET, signature) is False

    def test_tampered_body_returns_false(self) -> None:
        body = b'{"id":1}'
        signature = _sign(body)
        tampered = b'{"id":2}'
        assert verify_shopify_hmac(tampered, SECRET, signature) is False

    def test_empty_header_returns_false(self) -> None:
        body = b'{"id":1}'
        assert verify_shopify_hmac(body, SECRET, "") is False

    def test_invalid_base64_returns_false(self) -> None:
        body = b'{"id":1}'
        assert verify_shopify_hmac(body, SECRET, "not-valid-base64!!!") is False

    def test_empty_body_valid_signature(self) -> None:
        body = b""
        signature = _sign(body)
        assert verify_shopify_hmac(body, SECRET, signature) is True

    def test_unicode_secret(self) -> None:
        secret = "한국어시크릿"
        body = b'{"id":999}'
        signature = _sign(body, secret=secret)
        assert verify_shopify_hmac(body, secret, signature) is True
