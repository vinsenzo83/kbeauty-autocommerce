"""
tests/conftest.py
─────────────────
Shared pytest fixtures and configuration for the kbeauty-autocommerce test suite.

Key behaviours
--------------
* When DATABASE_URL_TEST is set in the environment (CI), the app's
  get_db() dependency is *not* overridden here — each test file that needs a
  real DB session sets up its own aiosqlite or Postgres session.
* Tests that only use mocks/stubs work without any DB at all.
* pytest-asyncio is set to "auto" mode in pytest.ini, so every async test
  function is automatically treated as a coroutine test.
"""

from __future__ import annotations

import os

import pytest

pytest_plugins = ["anyio"]


# ─────────────────────────────────────────────────────────────────────────────
# Auto-mark tests that live in certain files as `integration` or `slow`
# so `make test-fast` can skip them without manual decoration.
# ─────────────────────────────────────────────────────────────────────────────

_INTEGRATION_MODULES = {
    "test_webhook_idempotency",   # uses ASGI + real DB session (aiosqlite/pg)
    "test_order_state_machine",   # exercises full order pipeline
    "test_sprint2_supplier",      # Playwright + DB
    "test_sprint3_tracking",      # Playwright + DB
}

_SLOW_MODULES = {
    "test_sprint2_supplier",      # Playwright browser launch
    "test_sprint3_tracking",      # Playwright browser launch
    "test_sprint4_products",      # Playwright + image downloader
}


def pytest_collection_modifyitems(items: list) -> None:  # type: ignore[type-arg]
    """Auto-apply integration / slow markers based on module name."""
    for item in items:
        module_name = item.module.__name__.split(".")[-1]
        if module_name in _INTEGRATION_MODULES:
            item.add_marker(pytest.mark.integration)
        if module_name in _SLOW_MODULES:
            item.add_marker(pytest.mark.slow)
