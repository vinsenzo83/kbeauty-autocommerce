from __future__ import annotations

"""
app/crawlers/image_downloader.py
─────────────────────────────────
Download product images and store them under:
    {STORAGE_PATH}/product_images/{product_id}/

Usage
-----
    from app.crawlers.image_downloader import download_images
    paths = await download_images(product)

The function is async and uses ``httpx`` for HTTP requests.
If ``httpx`` is not available it falls back to a no-op stub and logs a warning.

File naming convention
----------------------
Images are stored as 0-indexed filenames preserving original extension:
    storage/product_images/<product_id>/0.jpg
    storage/product_images/<product_id>/1.jpg
    ...

Idempotent: if a file already exists with the same size it is skipped.
"""

import asyncio
import mimetypes
import os
import re
from pathlib import Path
from typing import Any

import structlog

from app.config import get_settings

logger = structlog.get_logger(__name__)

_settings = get_settings()


def _storage_path() -> Path:
    raw = os.getenv("STORAGE_PATH", "./storage")
    return Path(raw).expanduser().resolve()


def _image_dir(product_id: str) -> Path:
    return _storage_path() / "product_images" / str(product_id)


def _ext_from_url(url: str) -> str:
    """Extract file extension from URL, default to '.jpg'."""
    match = re.search(r"\.(jpg|jpeg|png|webp|gif)(\?|$)", url, re.IGNORECASE)
    if match:
        return f".{match.group(1).lower()}"
    return ".jpg"


async def _download_one(client: Any, url: str, dest: Path) -> bool:
    """
    Download a single URL to ``dest``.

    Returns True on success, False on failure.
    """
    try:
        resp = await client.get(url, follow_redirects=True, timeout=20)
        resp.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.content)
        logger.debug("image_downloader.saved", url=url, dest=str(dest))
        return True
    except Exception as exc:
        logger.warning("image_downloader.failed", url=url, error=str(exc))
        return False


async def download_images(product: Any) -> list[str]:
    """
    Download all images for a product model instance (or dict).

    Parameters
    ----------
    product : ORM Product instance or dict-like
        Must have ``id`` and ``image_urls_json`` (list[str] or None).

    Returns
    -------
    List of local file paths that were successfully downloaded.
    """
    # Accept both ORM instances and plain dicts
    if isinstance(product, dict):
        product_id  = product.get("id") or product.get("supplier_product_id", "unknown")
        image_urls  = product.get("image_urls_json") or product.get("image_urls") or []
    else:
        product_id = str(product.id)
        image_urls = product.image_urls_json or []

    if not image_urls:
        logger.debug("image_downloader.no_images", product_id=product_id)
        return []

    dest_dir = _image_dir(str(product_id))
    dest_dir.mkdir(parents=True, exist_ok=True)

    saved_paths: list[str] = []

    # Try to import httpx
    try:
        import httpx  # type: ignore[import]
    except ImportError:
        logger.warning(
            "image_downloader.httpx_missing",
            note="httpx not installed; image download skipped.",
        )
        return []

    async with httpx.AsyncClient(
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        }
    ) as client:
        tasks = []
        dests: list[Path] = []

        for idx, url in enumerate(image_urls):
            ext  = _ext_from_url(url)
            dest = dest_dir / f"{idx}{ext}"

            # Idempotent: skip if file already exists and is non-empty
            if dest.exists() and dest.stat().st_size > 0:
                saved_paths.append(str(dest))
                continue

            tasks.append(_download_one(client, url, dest))
            dests.append(dest)

        results = await asyncio.gather(*tasks)

    for ok, dest in zip(results, dests):
        if ok:
            saved_paths.append(str(dest))

    logger.info(
        "image_downloader.done",
        product_id=product_id,
        downloaded=len(saved_paths),
        total=len(image_urls),
    )
    return saved_paths
