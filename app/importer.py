"""Дополнительно: загрузка данных о товаре со страницы souz-pribor.ru.

В задании вход — JSON. Но у реального магазина данные уже лежат на страницах
товаров (а в YML-фиде их нет — там только название, цена и ссылка).
Импорт превращает страницу товара в тот же входной JSON, дальше всё как обычно.

Разрешён только домен souz-pribor.ru — сервис не должен ходить по произвольным адресам.
"""
import re
from urllib.parse import urlparse

import httpx
from lxml import html

from .schemas import ProductInput

ALLOWED_HOSTS = {"souz-pribor.ru", "www.souz-pribor.ru"}
# Символов описания, которые отдаём модели. Главное (назначение, применение, параметры)
# на страницах стоит в начале, а в хвосте — монтаж и эксплуатация. Длиннее — модель на
# процессоре заметно дольше читает вход, а качество не растёт.
DESCRIPTION_LIMIT = 2500


class ImportError_(ValueError):
    pass


def fetch_product(url: str) -> ProductInput:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in ALLOWED_HOSTS:
        raise ImportError_("Поддерживаются только ссылки на товары souz-pribor.ru")
    try:
        resp = httpx.get(url, timeout=30, follow_redirects=True, verify=_ssl_context(),
                         headers={"User-Agent": "Mozilla/5.0 (product-card-prototype)"})
    except httpx.HTTPError as e:
        raise ImportError_(f"Не удалось загрузить страницу: {e}") from e
    if resp.status_code != 200:
        raise ImportError_(f"Страница вернула код {resp.status_code}")
    return parse_product_page(resp.text, url)


def _ssl_context():
    """Доверенные сертификаты берём из системы (Windows/macOS/Linux), а не только из certifi.

    В корпоративных сетях, с антивирусом или VPN трафик часто идёт через свой сертификат,
    который есть в системном хранилище, но которого нет в certifi. Проверка сертификата
    при этом остаётся включённой.
    """
    try:
        import ssl

        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        return True


def parse_product_page(page: str, url: str | None = None) -> ProductInput:
    doc = html.fromstring(page)

    h1 = _text(doc.xpath("//h1")[0]) if doc.xpath("//h1") else ""
    # «ОВЕН ПМ01 - GSM-модем» → «GSM-модем ОВЕН ПМ01»
    name = h1
    m = re.match(r"^(.*?)\s+-\s+(.+)$", h1)
    if m:
        name = f"{m.group(2)[:1].upper()}{m.group(2)[1:]} {m.group(1)}"

    brand_nodes = doc.xpath("//a[contains(@class,'brand-block__name')]")
    manufacturer = _text(brand_nodes[0]) if brand_nodes else ""
    manufacturer = re.sub(r"\s*\(.*\)$", "", manufacturer)  # «ОВЕН (Россия)» → «ОВЕН»

    desc_nodes = doc.xpath("//*[@id='description']//div[contains(@class,'product-description')]")
    description = _block_text(desc_nodes[0]) if desc_nodes else ""
    if len(description) > DESCRIPTION_LIMIT:
        # обрезаем по границе абзаца, чтобы не оборвать фразу на середине
        description = description[:DESCRIPTION_LIMIT].rsplit("\n", 1)[0]

    specs: dict[str, str] = {}
    for row in doc.xpath("//*[@id='specifications']//table//tr"):
        cells = [_text(td) for td in row.xpath("./td|./th")]
        cells = [c for c in cells if c]
        if len(cells) != 2:
            continue  # заголовки разделов и пустые строки
        key, value = cells
        if key.lower() in {"наименование", "параметр", "характеристика"}:
            continue  # шапка таблицы
        specs.setdefault(key, value)

    return ProductInput(name=name, manufacturer=manufacturer, specs=specs,
                        description=description, source_url=url)


def _text(node) -> str:
    return re.sub(r"\s+", " ", node.text_content()).strip()


def _block_text(node) -> str:
    """Текст блока описания с сохранением абзацев и пунктов списков."""
    parts: list[str] = []
    for el in node.iter("h2", "h3", "h4", "p", "li", "strong"):
        if el.tag == "strong" and el.getparent() is not None and el.getparent().tag in {"p", "li"}:
            continue  # жирный внутри абзаца уже попадёт с абзацем
        t = _text(el)
        if t:
            parts.append(("- " if el.tag == "li" else "") + t)
    return "\n".join(parts)
