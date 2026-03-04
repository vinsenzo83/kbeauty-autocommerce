from __future__ import annotations

import hashlib
import hmac
import base64


def verify_shopify_hmac(raw_body: bytes, secret: str, header_value: str) -> bool:
    """
    Verify Shopify webhook HMAC-SHA256 signature.

    Shopify sends the header as a base64-encoded HMAC-SHA256 digest of the
    raw request body, keyed with the webhook secret.

    Returns True if the signature is valid, False otherwise.
    """
    if not header_value:
        return False

    expected = hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).digest()

    try:
        received = base64.b64decode(header_value)
    except Exception:
        return False

    return hmac.compare_digest(expected, received)
