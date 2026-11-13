"""Загрузка товара из YML-фида магазина (https://www.souz-pribor.ru/yml/any/feed.xml).

В фиде каждая позиция — конкретная модификация с названием вида
«МП3-У 0 - 100 кгс/см2, кл.т. 1,5», типом прибора, производителем и ссылкой
на страницу. Характеристик и описания в фиде нет — их дотягиваем со страницы
товара (importer.py).

Фид большой (~40 МБ), поэтому он скачивается один раз и кэшируется на сутки
в папке .cache/, а читается потоково (iterparse), не целиком в память.
"""
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

from .importer import ImportError_, _ssl_context, fetch_product
from .schemas import ProductInput

FEED_URL = "https://www.souz-pribor.ru/yml/any/feed.xml"
CACHE = Path(__file__).resolve().parent.parent / ".cache" / "feed.xml"
CACHE_TTL_S = 24 * 3600


def _feed_path() -> Path:
    if CACHE.exists() and time.time() - CACHE.stat().st_mtime < CACHE_TTL_S:
        return CACHE
    CACHE.parent.mkdir(exist_ok=True)
    tmp = CACHE.with_suffix(".part")
    try:
        with httpx.stream("GET", FEED_URL, timeout=120, follow_redirects=True, verify=_ssl_context()) as r:
            if r.status_code != 200:
                raise ImportError_(f"Фид вернул код {r.status_code}")
            with tmp.open("wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
    except httpx.HTTPError as e:
        if CACHE.exists():
            return CACHE  # сеть недоступна — работаем со старой копией
        raise ImportError_(f"Не удалось скачать фид: {e}") from e
    tmp.replace(CACHE)
    return CACHE


def find_offer(offer_id: str, feed_file: Path | None = None) -> dict:
    """Ищет позицию фида по id (атрибут offer/@id)."""
    offer_id = str(offer_id).strip()
    path = feed_file or _feed_path()
    for _, el in ET.iterparse(path):
        if el.tag != "offer":
            continue
        if el.get("id") == offer_id:
            return {
                "id": offer_id,
                "name": (el.findtext("name") or "").strip(),
                "type": (el.findtext("typePrefix") or "").strip(),
                "vendor": (el.findtext("vendor") or "").strip(),
                "url": (el.findtext("url") or "").strip(),
            }
        el.clear()
    raise ImportError_(f"Товар с id={offer_id} в фиде не найден")


def product_from_offer(offer_id: str, feed_file: Path | None = None) -> ProductInput:
    offer = find_offer(offer_id, feed_file)
    page = fetch_product(offer["url"]) if offer["url"] else ProductInput()
    type_ = offer["type"][:1].upper() + offer["type"][1:]
    name = " ".join(x for x in [type_, offer["vendor"], offer["name"]] if x)
    return ProductInput(
        name=name,
        manufacturer=offer["vendor"] or page.manufacturer,
        specs=page.specs,
        description=page.description,
        source_url=f"{offer['url']} (фид: offer id {offer_id})",
    )
