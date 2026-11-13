"""Проверка, что модель не выдумала технических значений.

Идея простая: любое число в сгенерированном тексте должно встречаться
во входных данных (название, характеристики, описание). Если число
взялось «из головы» модели — это подозрение на выдумку.

Проверка грубая, но надёжная: она не понимает смысл, зато ничего
не пропускает молча. Ложные срабатывания (например, «3 года гарантии»,
если гарантии не было во входе) — тоже повод для человека посмотреть.
"""
import re

from .schemas import ProductInput

# Число: 1,6 / 0.25 / 100 / 1/2 (для резьб G1/2)
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?(?:/\d+)?")


def _norm(num: str) -> str:
    n = num.replace(",", ".")
    if "." in n and "/" not in n:
        n = n.rstrip("0").rstrip(".") or "0"
    return n


def numbers_in(text: str) -> set[str]:
    return {_norm(m) for m in _NUM_RE.findall(text or "")}


def source_numbers(product: ProductInput) -> set[str]:
    parts = [product.name, product.manufacturer, product.description]
    for k, v in product.specs.items():
        parts.append(k)
        parts.append(v)
    # Составные значения вроде «120,1x107,1x22,5» и «0–1,6» регулярка
    # разбирает на отдельные числа сама.
    return numbers_in(" ".join(parts))


def invented_numbers(generated: str, product: ProductInput) -> list[str]:
    """Числа из сгенерированного текста, которых нет во входных данных."""
    allowed = source_numbers(product)
    found = numbers_in(generated)
    return sorted(n for n in found if n not in allowed)


# Понятия, к которым модель любит «приклеивать» чужие числа.
# Реальный случай: «гарантия 12 лет», хотя 12 лет — это срок службы, а гарантии в данных нет.
# Ключ — регулярное выражение (корень слова), значение — понятное название.
CONTEXT_TERMS = {
    r"гаранти[яиюей]\b|гарантийн": "гарантия",  # но не «гарантирует»
    r"срок\w* служб": "срок службы",
    r"межповерочн": "межповерочный интервал",
    r"наработк": "наработка на отказ",
    r"класс\w* точн": "класс точности",
    r"погрешност": "погрешность",
    r"\bмасс[аыуе]\b": "масса",
    r"диаметр": "диаметр",
}
_WINDOW_BEFORE, _WINDOW_AFTER = 25, 45


def _source_segments(product: ProductInput) -> list[str]:
    segs = [f"{k}: {v}".lower() for k, v in product.specs.items()]
    for line in re.split(r"\n|(?<=[.;!?])\s+", product.description):
        if line.strip():
            segs.append(line.lower())
    segs.append(product.name.lower())
    return segs


def misattributed_numbers(generated: str, product: ProductInput) -> list[str]:
    """Числа, которые стоят рядом с понятием (гарантия, срок службы…), но во входных
    данных относятся к другому понятию — или такого понятия в данных нет вообще.

    Возвращает фразы вида «гарантия: 12».
    """
    segments = _source_segments(product)
    found: list[str] = []
    for sentence in re.split(r"\n|(?<=[.!?])\s+(?=[А-ЯA-Z])", generated):
        low = sentence.lower()
        for pattern, label in CONTEXT_TERMS.items():
            for m in re.finditer(pattern, low):
                window = low[max(0, m.start() - _WINDOW_BEFORE): m.end() + _WINDOW_AFTER]
                nums = numbers_in(window)
                if not nums:
                    continue
                ok = any(re.search(pattern, seg) and nums & numbers_in(seg) for seg in segments)
                if not ok:
                    found.append(f"{label}: {', '.join(sorted(nums))}")
    return sorted(set(found))


# Расшифровки кодов защиты, которых модель не должна добавлять от себя
# («IP40 защищает от пыли и воды» — неверно, от воды IP40 не защищает).
_DECODED_RE = re.compile(r"защи\w*[^.]{0,40}?\b(пыл\w*|вод\w*|влаг\w*|брызг\w*)|пылезащ\w*|влагозащ\w*|водонепрониц\w*")


def decoded_claims(generated: str, product: ProductInput) -> list[str]:
    source = " ".join(_source_segments(product))
    claims = set()
    for m in _DECODED_RE.finditer(generated.lower()):
        phrase = m.group(0)
        if phrase not in source:
            claims.add(phrase.strip())
    return sorted(claims)
