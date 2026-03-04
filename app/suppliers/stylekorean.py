from __future__ import annotations

"""
StyleKorean supplier client
===========================

Two operating modes, selected at construction time:

  mode="api"        – (future) REST API integration. Currently raises NotImplementedError.
  mode="playwright" – Browser automation skeleton using Playwright.
                      No anti-bot / CAPTCHA-bypass logic is included.

Environment variables consumed (mode=playwright):
  STYLEKOREAN_EMAIL     – account login e-mail
  STYLEKOREAN_PASSWORD  – account password
  STORAGE_PATH          – root path for artifact storage (default: ./storage)

Failure artifacts are saved to:
  {STORAGE_PATH}/bot_failures/{order_id}/screenshot.png
  {STORAGE_PATH}/bot_failures/{order_id}/page.html

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

# ── Public site constants (adjust selectors as StyleKorean updates their UI) ──
_BASE_URL        = "https://www.stylekorean.com"
_LOGIN_URL       = f"{_BASE_URL}/member/login"
_CART_URL        = f"{_BASE_URL}/cart"
_CHECKOUT_URL    = f"{_BASE_URL}/order/checkout"

# CSS selectors – kept in one place for easy maintenance
_SEL = {
    "login_email":       "input[name='email']",
    "login_password":    "input[name='password']",
    "login_submit":      "button[type='submit']",
    "add_to_cart":       "button.add-to-cart, button[data-action='add-to-cart']",
    "cart_checkout_btn": "a.checkout-btn, button.checkout-btn",
    # Shipping address fields
    "ship_first_name":   "input[name='shipping_first_name']",
    "ship_last_name":    "input[name='shipping_last_name']",
    "ship_address1":     "input[name='shipping_address1']",
    "ship_city":         "input[name='shipping_city']",
    "ship_country":      "select[name='shipping_country']",
    "ship_zip":          "input[name='shipping_zip']",
    "ship_phone":        "input[name='shipping_phone']",
    # Order placement
    "place_order_btn":   "button.place-order, button[data-action='place-order']",
    # Confirmation page – order number
    "order_number":      ".order-confirmation .order-number, [data-order-id]",
}


class StyleKoreanClient(SupplierClient):
    """
    Supplier client for stylekorean.com.

    Parameters
    ----------
    mode : "api" | "playwright"
        "api"        – stub, raises NotImplementedError (future REST integration).
        "playwright" – Playwright browser automation skeleton.
    headless : bool
        Passed to Playwright's browser launch. Ignored in "api" mode.
    """

    name = "stylekorean"

    def __init__(self, mode: str = "playwright", headless: bool = True) -> None:
        if mode not in ("api", "playwright"):
            raise ValueError(f"Unknown mode: {mode!r}. Choose 'api' or 'playwright'.")
        self.mode     = mode
        self.headless = headless
        self._email    = os.getenv("STYLEKOREAN_EMAIL", "")
        self._password = os.getenv("STYLEKOREAN_PASSWORD", "")
        self._storage  = Path(os.getenv("STORAGE_PATH", "./storage"))

    # ── Public interface ──────────────────────────────────────────────────────

    async def create_order(self, order: "Order") -> str:
        if self.mode == "api":
            raise NotImplementedError(
                "StyleKorean REST API integration is not yet implemented. "
                "Use mode='playwright' for the browser-based skeleton."
            )
        return await self._playwright_create_order(order)

    async def get_tracking(self, supplier_order_id: str) -> tuple[str | None, str | None]:
        """Stub – tracking lookup not yet implemented."""
        logger.debug(
            "stylekorean.get_tracking.stub",
            supplier_order_id=supplier_order_id,
        )
        return (None, None)

    # ── Playwright skeleton ───────────────────────────────────────────────────

    async def _playwright_create_order(self, order: "Order") -> str:
        """
        Browser-automation skeleton for placing an order on StyleKorean.

        Steps
        -----
        1. Launch Chromium (headless by default).
        2. Log in with STYLEKOREAN_EMAIL / STYLEKOREAN_PASSWORD.
        3. For each line item: navigate to product URL and add to cart.
        4. Navigate to checkout and fill shipping address.
        5. Click place-order and parse confirmation order ID.
        6. On any exception: save screenshot + HTML artifacts and raise SupplierError.

        Notes
        -----
        - Selectors are centralised in _SEL for easy adjustment.
        - No anti-bot / CAPTCHA-bypass logic is present or should be added.
        - Import playwright lazily so the package is optional at import time
          (avoids import errors in environments without playwright installed).
        """
        # Lazy import — not available in test environment
        try:
            from playwright.async_api import async_playwright  # type: ignore[import]
        except ImportError as exc:
            raise SupplierError(
                "playwright is not installed. "
                "Run: pip install playwright && playwright install chromium",
                retryable=False,
            ) from exc

        log = logger.bind(order_id=str(order.id), supplier=self.name)
        log.info("stylekorean.placement.start")

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self.headless)
            context = await browser.new_context()
            page    = await context.new_page()

            try:
                # ── Step 1: Login ─────────────────────────────────────────────
                await self._login(page, log)

                # ── Step 2: Add items to cart ─────────────────────────────────
                await self._add_items_to_cart(page, order, log)

                # ── Step 3: Checkout + fill address ───────────────────────────
                await self._fill_checkout(page, order, log)

                # ── Step 4: Place order ───────────────────────────────────────
                supplier_order_id = await self._submit_order(page, log)

                log.info(
                    "stylekorean.placement.success",
                    supplier_order_id=supplier_order_id,
                )
                return supplier_order_id

            except SupplierError:
                raise

            except Exception as exc:
                reason = f"Playwright error: {exc}"
                log.error("stylekorean.placement.error", error=reason)
                await self._save_failure_artifacts(page, order, reason)
                raise SupplierError(reason, retryable=True) from exc

            finally:
                await browser.close()

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _login(self, page: Any, log: Any) -> None:
        log.debug("stylekorean.login")
        await page.goto(_LOGIN_URL, wait_until="networkidle")
        await page.fill(_SEL["login_email"],    self._email)
        await page.fill(_SEL["login_password"], self._password)
        await page.click(_SEL["login_submit"])
        await page.wait_for_url(lambda url: "/member/login" not in url, timeout=15_000)
        log.debug("stylekorean.login.ok")

    async def _add_items_to_cart(self, page: Any, order: "Order", log: Any) -> None:
        line_items: list[dict] = order.line_items_json or []
        if not line_items:
            raise SupplierError("No line items on order – cannot place.", retryable=False)

        for item in line_items:
            product_url = item.get("product_url") or self._resolve_product_url(item)
            log.debug("stylekorean.add_to_cart", product_url=product_url)
            await page.goto(product_url, wait_until="networkidle")
            await page.click(_SEL["add_to_cart"])
            # Brief settle wait; use page.wait_for_selector for real robustness
            await page.wait_for_timeout(800)

    def _resolve_product_url(self, item: dict) -> str:
        """
        Resolve a product URL from a line item dict.

        In production this would look up a SKU → StyleKorean URL mapping table.
        For MVP we use a placeholder URL so the skeleton can run end-to-end
        in a staging environment with a known test product.
        """
        sku = item.get("sku") or item.get("variant_id") or "UNKNOWN"
        logger.warning(
            "stylekorean.product_url_fallback",
            sku=sku,
            note="Add SKU→URL mapping in supplier config for production use.",
        )
        return f"{_BASE_URL}/product/placeholder?sku={sku}"

    async def _fill_checkout(self, page: Any, order: "Order", log: Any) -> None:
        log.debug("stylekorean.checkout.navigate")
        await page.goto(_CHECKOUT_URL, wait_until="networkidle")

        addr: dict = order.shipping_address_json or {}

        async def _fill(sel: str, value: str | None) -> None:
            if value:
                await page.fill(sel, value)

        await _fill(_SEL["ship_first_name"], addr.get("first_name"))
        await _fill(_SEL["ship_last_name"],  addr.get("last_name"))
        await _fill(_SEL["ship_address1"],   addr.get("address1"))
        await _fill(_SEL["ship_city"],       addr.get("city"))
        await _fill(_SEL["ship_zip"],        addr.get("zip") or addr.get("postal_code"))
        await _fill(_SEL["ship_phone"],      addr.get("phone"))

        country = addr.get("country_code") or addr.get("country")
        if country:
            await page.select_option(_SEL["ship_country"], country)

        log.debug("stylekorean.checkout.address_filled")

    async def _submit_order(self, page: Any, log: Any) -> str:
        log.debug("stylekorean.submit_order")
        await page.click(_SEL["place_order_btn"])
        await page.wait_for_selector(_SEL["order_number"], timeout=20_000)

        raw = await page.inner_text(_SEL["order_number"])
        supplier_order_id = re.sub(r"\s+", "", raw).strip("#").strip()
        if not supplier_order_id:
            raise SupplierError(
                "Could not parse supplier order ID from confirmation page.",
                retryable=False,
            )
        return supplier_order_id

    async def _save_failure_artifacts(
        self, page: Any, order: "Order", reason: str
    ) -> None:
        """Save screenshot + HTML to STORAGE_PATH/bot_failures/{order_id}/."""
        artifact_dir = self._storage / "bot_failures" / str(order.id)
        artifact_dir.mkdir(parents=True, exist_ok=True)

        screenshot_path = artifact_dir / "screenshot.png"
        html_path       = artifact_dir / "page.html"
        reason_path     = artifact_dir / "reason.txt"

        try:
            await page.screenshot(path=str(screenshot_path), full_page=True)
        except Exception as e:
            logger.warning("stylekorean.artifact.screenshot_failed", error=str(e))

        try:
            html = await page.content()
            html_path.write_text(html, encoding="utf-8")
        except Exception as e:
            logger.warning("stylekorean.artifact.html_failed", error=str(e))

        reason_path.write_text(
            f"{datetime.now(timezone.utc).isoformat()} | order_id={order.id}\n{reason}\n",
            encoding="utf-8",
        )

        logger.info(
            "stylekorean.artifact.saved",
            order_id=str(order.id),
            directory=str(artifact_dir),
        )
