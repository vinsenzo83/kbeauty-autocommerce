from __future__ import annotations

"""
app/suppliers/jolse.py
───────────────────────
Sprint 7 – Jolse supplier client (jolse.com).

Operates in playwright mode (browser automation skeleton).
Tests MUST mock this class; real browser is never launched during pytest.
"""

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from app.suppliers.base import SupplierClient, SupplierError

if TYPE_CHECKING:
    from app.models.order import Order

logger = structlog.get_logger(__name__)

_BASE_URL     = "https://www.jolse.com"
_LOGIN_URL    = f"{_BASE_URL}/member/login"
_CHECKOUT_URL = f"{_BASE_URL}/order/checkout"


class JolseClient(SupplierClient):
    """
    Supplier client for jolse.com.

    Parameters
    ----------
    headless : bool
    """

    name = "jolse"

    def __init__(self, headless: bool = True) -> None:
        self.headless  = headless
        self._email    = os.getenv("JOLSE_EMAIL", "")
        self._password = os.getenv("JOLSE_PASSWORD", "")
        self._storage  = Path(os.getenv("STORAGE_PATH", "./storage"))

    # ── Public interface ──────────────────────────────────────────────────────

    async def create_order(self, order: "Order") -> str:
        return await self._playwright_create_order(order)

    async def get_tracking(
        self, supplier_order_id: str
    ) -> tuple[str | None, str | None]:
        return await self._playwright_get_tracking(supplier_order_id)

    # ── Playwright stubs ──────────────────────────────────────────────────────

    async def _playwright_create_order(self, order: "Order") -> str:
        try:
            from playwright.async_api import async_playwright  # type: ignore[import]
        except ImportError as exc:
            raise SupplierError(
                "playwright is not installed.", retryable=False
            ) from exc

        log = logger.bind(order_id=str(order.id), supplier=self.name)
        log.info("jolse.placement.start")

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self.headless)
            ctx     = await browser.new_context()
            page    = await ctx.new_page()
            try:
                await self._login(page, log)
                await self._add_to_cart(page, order, log)
                await self._fill_checkout(page, order, log)
                supplier_order_id = await self._submit_order(page, log)
                log.info("jolse.placement.success", supplier_order_id=supplier_order_id)
                return supplier_order_id
            except SupplierError:
                raise
            except Exception as exc:
                reason = f"Jolse placement error: {exc}"
                log.error("jolse.placement.error", error=reason)
                await self._save_artifacts(page, str(order.id), reason)
                raise SupplierError(reason, retryable=True) from exc
            finally:
                await browser.close()

    async def _playwright_get_tracking(
        self, supplier_order_id: str
    ) -> tuple[str | None, str | None]:
        # Stub: real implementation mirrors StyleKorean My-Orders scraping
        logger.info("jolse.get_tracking.stub", supplier_order_id=supplier_order_id)
        return (None, None)

    # ── Page interaction helpers ──────────────────────────────────────────────

    async def _login(self, page: Any, log: Any) -> None:
        log.debug("jolse.login")
        await page.goto(_LOGIN_URL, wait_until="networkidle")
        await page.fill("input[name='email']",    self._email)
        await page.fill("input[name='password']", self._password)
        await page.click("button[type='submit']")
        await page.wait_for_url(lambda url: "/member/login" not in url, timeout=15_000)

    async def _add_to_cart(self, page: Any, order: "Order", log: Any) -> None:
        for item in order.line_items_json or []:
            url = item.get("product_url", f"{_BASE_URL}/product/placeholder")
            log.debug("jolse.add_to_cart", url=url)
            await page.goto(url, wait_until="networkidle")
            await page.click("button.add-to-cart")
            await page.wait_for_timeout(600)

    async def _fill_checkout(self, page: Any, order: "Order", log: Any) -> None:
        await page.goto(_CHECKOUT_URL, wait_until="networkidle")
        addr = order.shipping_address_json or {}
        for sel, key in [
            ("input[name='first_name']", "first_name"),
            ("input[name='last_name']",  "last_name"),
            ("input[name='address1']",   "address1"),
            ("input[name='city']",       "city"),
            ("input[name='zip']",        "zip"),
            ("input[name='phone']",      "phone"),
        ]:
            if addr.get(key):
                await page.fill(sel, addr[key])

    async def _submit_order(self, page: Any, log: Any) -> str:
        await page.click("button.place-order")
        await page.wait_for_selector(".order-confirmation", timeout=20_000)
        raw = await page.inner_text(".order-confirmation .order-number")
        order_id = re.sub(r"\s+", "", raw).strip("#")
        if not order_id:
            raise SupplierError("Could not parse Jolse order ID.", retryable=False)
        return order_id

    async def _save_artifacts(
        self, page: Any, ref_id: str, reason: str
    ) -> None:
        artifact_dir = self._storage / "bot_failures" / "jolse" / ref_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        try:
            await page.screenshot(
                path=str(artifact_dir / "screenshot.png"), full_page=True
            )
        except Exception:
            pass
        (artifact_dir / "reason.txt").write_text(
            f"{datetime.now(timezone.utc).isoformat()} | {reason}\n"
        )
