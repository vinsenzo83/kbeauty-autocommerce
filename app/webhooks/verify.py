"""
app/webhooks/verify.py
───────────────────────
Sprint 11 – Production-grade webhook signature verification.

Shopify HMAC-SHA256 Verification
─────────────────────────────────
Algorithm:
    expected = base64( hmac_sha256(SHOPIFY_WEBHOOK_SECRET, raw_request_body) )
    safe_compare(expected, header["X-Shopify-Hmac-Sha256"])

References:
    https://shopify.dev/docs/apps/webhooks/configuration/https#step-5-verify-the-webhook

Security notes:
  * Always use hmac.compare_digest() — prevents timing-oracle attacks.
  * Raw body must be compared (before any JSON parsing / re-serialisation).
  * An empty or missing header is treated as verification failure (returns False).
"""
from __future__ import annotations

import base64
import hashlib
import hmac


def verify_shopify_webhook(secret: str, body: bytes, header_hmac: str) -> bool:
    """Verify a Shopify webhook HMAC-SHA256 signature.

    Args:
        secret:       SHOPIFY_WEBHOOK_SECRET value (plain string).
        body:         Raw HTTP request body bytes (before JSON parsing).
        header_hmac:  Value of the ``X-Shopify-Hmac-Sha256`` request header.

    Returns:
        ``True``  – signature is valid.
        ``False`` – signature is missing, malformed, or does not match.

    Example::

        ok = verify_shopify_webhook(
            secret="my-secret",
            body=b'{"id":1}',
            header_hmac="DKpqO/tkBBBPQXCmv7TElC4yLpHb/UKM3MGALFQ31vM=",
        )
    """
    if not header_hmac or not secret or not body:
        return False

    # Compute expected digest
    digest = hmac.new(
        key=secret.encode("utf-8"),
        msg=body,
        digestmod=hashlib.sha256,
    ).digest()
    expected = base64.b64encode(digest).decode("utf-8")

    # Constant-time comparison — immune to timing-oracle attacks
    return hmac.compare_digest(expected, header_hmac)
