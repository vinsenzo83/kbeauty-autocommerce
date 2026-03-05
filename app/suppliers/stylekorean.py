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

Placement failure artifacts → {STORAGE_PATH}/bot_failures/{order_id}/
Tracking failure artifacts  → {STORAGE_PATH}/bot_failures/tracking/{order_id}/

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

# ── Site URLs ─────────────────────────────────────────────────────────────────
_BASE_URL      = "https://www.stylekorean.com"
_LOGIN_URL     = f"{_BASE_URL}/member/login"
_CART_URL      = f"{_BASE_URL}/cart"
_CHECKOUT_URL  = f"{_BASE_URL}/order/checkout"
_MY_ORDERS_URL = f"{_BASE_URL}/member/orders"

# ── CSS / XPath selectors ─────────────────────────────────────────────────────
# Keep all selectors here; adjust when StyleKorean updates their UI.
_SEL: dict[str, str] = {
    # ── Auth ──────────────────────────────────────────────────────────────────
    "login_email":        "input[name='email']",
    "login_password":     "input[name='password']",
    "login_submit":       "button[type='submit']",

    # ── Cart / placement ──────────────────────────────────────────────────────
    "add_to_cart":        "button.add-to-cart, button[data-action='add-to-cart']",
    "cart_checkout_btn":  "a.checkout-btn, button.checkout-btn",

    # ── Shipping address fields ───────────────────────────────────────────────
    "ship_first_name":    "input[name='shipping_first_name']",
    "ship_last_name":     "input[name='shipping_last_name']",
    "ship_address1":      "input[name='shipping_address1']",
    "ship_city":          "input[name='shipping_city']",
    "ship_country":       "select[name='shipping_country']",
    "ship_zip":           "input[name='shipping_zip']",
    "ship_phone":         "input[name='shipping_phone']",

    # ── Order placement confirmation ──────────────────────────────────────────
    "place_order_btn":    "button.place-order, button[data-action='place-order']",
    "order_number":       ".order-confirmation .order-number, [data-order-id]",

    # ── My Orders page ────────────────────────────────────────────────────────
    # Table / list row that contains the order reference
    "my_orders_row":      "table.order-list tr, .order-list-item",
    # Cell / span inside a row that holds the order reference number
    "order_ref_cell":     ".order-number, [data-order-number], td.order-ref",
    # Tracking number cell / link inside a row
    "tracking_number":    ".tracking-number, [data-tracking-number], td.tracking",
    # Carrier name cell (optional – used to build tracking URL)
    "carrier_name":       ".carrier-name, [data-carrier], td.carrier",
    # "Track" link href (if the site renders a direct link)
    "tracking_link":      "a.track-order, a[href*='track']",
}

# ── Known carrier tracking URL templates ─────────────────────────────────────
# Keys are lowercase carrier name fragments found in the carrier cell.
_CARRIER_TRACKING_URLS: dict[str, str] = {
    "dhl":      "https://www.dhl.com/en/express/tracking.html?AWB={tracking_number}",
    "fedex":    "https://www.fedex.com/apps/fedextrack/?tracknumbers={tracking_number}",
    "ups":      "https://www.ups.com/track?tracknum={tracking_number}",
    "usps":     "https://tools.usps.com/go/TrackConfirmAction?tLabels={tracking_number}",
    "ems":      "https://www.ems.com.cn/mailquery?mailNo={tracking_number}",
    "cj":       "https://www.cjlogistics.com/ko/tool/parcel/tracking?gnbInvcNo={tracking_number}",
    "epacket":  "https://t.17track.net/en#nums={tracking_number}",
    "sf":       "https://www.sf-express.com/index_en.html#tid={tracking_number}",
}


def _build_tracking_url(carrier: str | None, tracking_number: str) -> str | None:
    """Return a carrier tracking URL, or None if carrier is unknown."""
    if not carrier:
        return None
    carrier_lower = carrier.lower()
    for key, template in _CARRIER_TRACKING_URLS.items():
        if key in carrier_lower:
            return template.format(tracking_number=tracking_number)
    return None


class StyleKoreanClient(SupplierClient):
    """
    Supplier client for stylekorean.com.

    Parameters
    ----------
    mode : "api" | "playwright"
    headless : bool
    """

    name = "stylekorean"

    def __init__(self, mode: str = "playwright", headless: bool = True) -> None:
        if mode not in ("api", "playwright"):
            raise ValueError(f"Unknown mode: {mode!r}. Choose 'api' or 'playwright'.")
        self.mode      = mode
        self.headless  = headless
        self._email    = os.getenv("STYLEKOREAN_EMAIL", "")
        self._password = os.getenv("STYLEKOREAN_PASSWORD", "")
        self._storage  = Path(os.getenv("STORAGE_PATH", "./storage"))

    # ── Public interface ──────────────────────────────────────────────────────

    # ── Sprint 14: new fulfillment interface ─────────────────────────────────

    async def place_order(self, order_payload: dict) -> "PlacedOrder":
        """
        Place a supplier order for the given payload.

        In production this would use Playwright browser automation.
        Returns a PlacedOrder with a mock supplier_order_id for safety.
        Real network calls are NEVER made unless STYLEKOREAN_EMAIL is set.
        """
        from app.suppliers.base import PlacedOrder

        channel_order_id = order_payload.get("channel_order_id", "unknown")
        supplier_product_id = order_payload.get("supplier_product_id", "")

        if not self._email:
            # Stub / dry-run mode — return deterministic mock
            logger.info(
                "stylekorean.place_order.stub",
                channel_order_id=channel_order_id,
                note="No credentials configured — returning stub PlacedOrder",
            )
            return PlacedOrder(
                supplier_order_id=f"SK-STUB-{channel_order_id[:8]}",
                status="placed",
                cost=order_payload.get("cost"),
                currency=order_payload.get("currency", "USD"),
                raw={"mode": "stub"},
            )

        # Real placement would call _playwright_create_order here
        raise NotImplementedError(
            "StyleKorean real place_order requires Playwright integration. "
            "Set STYLEKOREAN_EMAIL and use mode='playwright'."
        )

    async def get_order_status(self, supplier_order_id: str) -> "OrderStatus":
        """
        Fetch the current status of a previously placed order.

        Stub mode returns 'shipped' with a deterministic tracking number
        when credentials are absent (test-safe).
        """
        from app.suppliers.base import OrderStatus

        if not self._email:
            logger.info(
                "stylekorean.get_order_status.stub",
                supplier_order_id=supplier_order_id,
            )
            return OrderStatus(
                supplier_order_id=supplier_order_id,
                status="shipped",
                tracking_number=f"SK-TRK-{supplier_order_id[-6:]}",
                tracking_carrier="DHL",
                raw={"mode": "stub"},
            )

        raise NotImplementedError("StyleKorean real get_order_status requires Playwright.")

    # ── Legacy Sprint 2–7 interface ───────────────────────────────────────────

    async def create_order(self, order: "Order") -> str:
        if self.mode == "api":
            raise NotImplementedError(
                "StyleKorean REST API integration is not yet implemented. "
                "Use mode='playwright' for the browser-based skeleton."
            )
        return await self._playwright_create_order(order)

    async def get_tracking(
        self, supplier_order_id: str
    ) -> tuple[str | None, str | None]:
        """
        Scrape tracking info for *supplier_order_id* from the My Orders page.

        Returns
        -------
        (tracking_number, tracking_url)
            Both are None when the order has not yet shipped.

        Raises
        ------
        SupplierError
            On any scraping failure (page unavailable, selector not found, etc.).
            Artifacts are saved to STORAGE_PATH/bot_failures/tracking/{supplier_order_id}/.
        """
        if self.mode == "api":
            logger.debug("stylekorean.get_tracking.api_stub", supplier_order_id=supplier_order_id)
            return (None, None)
        return await self._playwright_get_tracking(supplier_order_id)

    # ── Playwright: create_order ──────────────────────────────────────────────

    async def _playwright_create_order(self, order: "Order") -> str:
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
                await self._login(page, log)
                await self._add_items_to_cart(page, order, log)
                await self._fill_checkout(page, order, log)
                supplier_order_id = await self._submit_order(page, log)
                log.info("stylekorean.placement.success", supplier_order_id=supplier_order_id)
                return supplier_order_id

            except SupplierError:
                raise
            except Exception as exc:
                reason = f"Playwright placement error: {exc}"
                log.error("stylekorean.placement.error", error=reason)
                await self._save_placement_artifacts(page, order, reason)
                raise SupplierError(reason, retryable=True) from exc
            finally:
                await browser.close()

    # ── Playwright: get_tracking ──────────────────────────────────────────────

    async def _playwright_get_tracking(
        self, supplier_order_id: str
    ) -> tuple[str | None, str | None]:
        """
        My Orders page scraping flow:

        1. Launch browser → login.
        2. Navigate to My Orders page.
        3. Find the row matching supplier_order_id.
        4. Extract tracking number + carrier name.
        5. Build tracking URL from known carrier templates.
        6. Return (tracking_number, tracking_url).
           If the row has no tracking yet → return (None, None).
        """
        try:
            from playwright.async_api import async_playwright  # type: ignore[import]
        except ImportError as exc:
            raise SupplierError(
                "playwright is not installed. "
                "Run: pip install playwright && playwright install chromium",
                retryable=False,
            ) from exc

        log = logger.bind(supplier_order_id=supplier_order_id, supplier=self.name)
        log.info("stylekorean.get_tracking.start")

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self.headless)
            context = await browser.new_context()
            page    = await context.new_page()

            try:
                await self._login(page, log)
                result = await self._scrape_tracking_from_my_orders(
                    page, supplier_order_id, log
                )
                log.info("stylekorean.get_tracking.done", result=result)
                return result

            except SupplierError:
                raise
            except Exception as exc:
                reason = f"Playwright tracking error: {exc}"
                log.error("stylekorean.get_tracking.error", error=reason)
                await self._save_tracking_artifacts(page, supplier_order_id, reason)
                raise SupplierError(reason, retryable=True) from exc
            finally:
                await browser.close()

    async def _scrape_tracking_from_my_orders(
        self,
        page: Any,
        supplier_order_id: str,
        log: Any,
    ) -> tuple[str | None, str | None]:
        """
        Navigate to My Orders and locate the row for *supplier_order_id*.

        Strategy
        --------
        1. Load the orders list page.
        2. Iterate through all rows; match the one containing supplier_order_id.
        3. From the matched row, read tracking number and carrier cells.
        4. If no tracking cell found → order not yet shipped → return (None, None).
        5. Build tracking URL using _build_tracking_url().

        Notes
        -----
        Selectors are defined in _SEL for easy adjustment.
        We handle pagination by checking for a "next page" link and repeating.
        """
        page_num = 1
        while True:
            url = f"{_MY_ORDERS_URL}?page={page_num}" if page_num > 1 else _MY_ORDERS_URL
            log.debug("stylekorean.my_orders.navigate", url=url)
            await page.goto(url, wait_until="networkidle")

            rows = await page.query_selector_all(_SEL["my_orders_row"])
            if not rows:
                log.info("stylekorean.my_orders.empty", page=page_num)
                return (None, None)

            for row in rows:
                ref_el = await row.query_selector(_SEL["order_ref_cell"])
                if ref_el is None:
                    continue
                ref_text = (await ref_el.inner_text()).strip().strip("#")
                if ref_text != supplier_order_id.strip("#"):
                    continue

                # ── Found the matching row ─────────────────────────────────
                log.debug("stylekorean.my_orders.row_found", ref=ref_text)

                # 1. Try direct tracking link href first
                tracking_link_el = await row.query_selector(_SEL["tracking_link"])
                if tracking_link_el:
                    tracking_url = await tracking_link_el.get_attribute("href") or None
                    tracking_num = await self._extract_tracking_from_url(tracking_url)
                    if tracking_num:
                        return (tracking_num, tracking_url)

                # 2. Try explicit tracking number cell
                tracking_el = await row.query_selector(_SEL["tracking_number"])
                if tracking_el is None:
                    # No tracking cell → not shipped yet
                    return (None, None)

                tracking_raw = (await tracking_el.inner_text()).strip()
                if not tracking_raw or tracking_raw in ("-", "—", "N/A", ""):
                    return (None, None)

                tracking_num = re.sub(r"\s+", "", tracking_raw)

                # 3. Try carrier cell to build tracking URL
                carrier_el  = await row.query_selector(_SEL["carrier_name"])
                carrier_raw = (await carrier_el.inner_text()).strip() if carrier_el else None
                tracking_url = _build_tracking_url(carrier_raw, tracking_num)

                log.info(
                    "stylekorean.tracking.extracted",
                    tracking_number=tracking_num,
                    carrier=carrier_raw,
                    tracking_url=tracking_url,
                )
                return (tracking_num, tracking_url)

            # ── Check for next page ────────────────────────────────────────
            next_btn = await page.query_selector("a.next-page, [aria-label='Next page']")
            if next_btn is None:
                break
            page_num += 1

        log.info("stylekorean.my_orders.order_not_found", supplier_order_id=supplier_order_id)
        return (None, None)

    @staticmethod
    async def _extract_tracking_from_url(url: str | None) -> str | None:
        """Attempt to parse a tracking number from a tracking link URL."""
        if not url:
            return None
        # Common patterns: ?tracknum=XXX, ?track=XXX, #nums=XXX, /track/XXX
        match = re.search(
            r"(?:tracknum|tracknumbers|track|AWB|mailNo|gnbInvcNo|nums)[=/#]([A-Z0-9]+)",
            url,
            re.IGNORECASE,
        )
        return match.group(1) if match else None

    # ── Private helpers: login / cart / checkout ──────────────────────────────

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
            await page.wait_for_timeout(800)

    def _resolve_product_url(self, item: dict) -> str:
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

    # ── Artifact helpers ──────────────────────────────────────────────────────

    async def _save_placement_artifacts(
        self, page: Any, order: "Order", reason: str
    ) -> None:
        """Save screenshot + HTML to STORAGE_PATH/bot_failures/{order_id}/."""
        await self._write_artifacts(
            page,
            self._storage / "bot_failures" / str(order.id),
            f"order_id={order.id}\n{reason}",
        )

    async def _save_tracking_artifacts(
        self, page: Any, supplier_order_id: str, reason: str
    ) -> None:
        """Save screenshot + HTML to STORAGE_PATH/bot_failures/tracking/{supplier_order_id}/."""
        await self._write_artifacts(
            page,
            self._storage / "bot_failures" / "tracking" / supplier_order_id,
            f"supplier_order_id={supplier_order_id}\n{reason}",
        )

    async def _write_artifacts(
        self, page: Any, artifact_dir: Path, reason_body: str
    ) -> None:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        try:
            await page.screenshot(path=str(artifact_dir / "screenshot.png"), full_page=True)
        except Exception as e:
            logger.warning("stylekorean.artifact.screenshot_failed", error=str(e))
        try:
            (artifact_dir / "page.html").write_text(await page.content(), encoding="utf-8")
        except Exception as e:
            logger.warning("stylekorean.artifact.html_failed", error=str(e))
        (artifact_dir / "reason.txt").write_text(
            f"{datetime.now(timezone.utc).isoformat()} | {reason_body}\n",
            encoding="utf-8",
        )
        logger.info("stylekorean.artifact.saved", directory=str(artifact_dir))
