"""Разбор и проверка входных данных.

Задача модуля — до обращения к LLM понять, хватает ли данных,
и честно сообщить, чего не хватает.
"""
import json
import re

from .schemas import ProductInput


class InputError(ValueError):
    """Входные данные нельзя использовать: битый JSON, нет названия и т. п."""


# Какие характеристики ожидаем увидеть у разных типов приборов.
# Если такой характеристики нет во входных данных, в карточке будет «данных нет».
# Список намеренно короткий: только то, что покупатель КИПиА смотрит в первую очередь.
EXPECTED_SPECS: dict[str, list[str]] = {
    "манометр": ["диапазон измерений", "класс точности", "присоединение", "диаметр корпуса"],
    "датчик давления": ["диапазон измерений", "основная погрешность", "выходной сигнал",
                        "присоединение", "напряжение питания", "степень защиты"],
    "термометр": ["диапазон измерений", "класс точности", "длина погружаемой части"],
    "термопреобразователь": ["диапазон измерений", "класс допуска", "длина монтажной части"],
    "модем": ["напряжение питания", "интерфейс", "степень защиты"],
}

# Синонимы: как одна и та же характеристика называется в разных паспортах.
SPEC_SYNONYMS: dict[str, list[str]] = {
    "диапазон измерений": ["диапазон измер", "верхний предел", "пределы измерений", "диапазон давлени"],
    "класс точности": ["класс точн", "кл.т"],
    "основная погрешность": ["погрешност"],
    "присоединение": ["присоедин", "резьб", "штуцер для подключ", "штуцер"],
    "диаметр корпуса": ["диаметр корпус", "диаметр"],
    "выходной сигнал": ["выходной сигнал", "выход"],
    "напряжение питания": ["напряжени", "питани"],
    "степень защиты": ["степень защит", "ip"],
    "класс допуска": ["класс допуск"],
    "длина монтажной части": ["длина монтаж"],
    "длина погружаемой части": ["длина погруж"],
    "интерфейс": ["интерфейс"],
}

# Порядок важен: «датчик давления» проверяем раньше «манометра».
_TYPE_KEYWORDS = [
    ("датчик давления", ["датчик давлен", "преобразователь давлен", "пд100", "пд150"]),
    ("манометр", ["манометр", "мановакуумметр", "вакуумметр"]),
    ("термопреобразователь", ["термопреобразовател", "термопар", "тсм", "тсп"]),
    ("термометр", ["термометр"]),
    ("модем", ["модем"]),
]


def parse_input(raw: str | bytes | dict) -> ProductInput:
    """Превращает сырой вход (строку JSON или словарь) в ProductInput.

    Бросает InputError с понятным сообщением, если вход нельзя использовать.
    """
    if isinstance(raw, (str, bytes)):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise InputError(f"Некорректный JSON: {e.msg} (строка {e.lineno}, позиция {e.colno})") from e
    else:
        data = raw

    if not isinstance(data, dict):
        raise InputError("Ожидается JSON-объект с полями name, manufacturer, specs, description")

    specs = data.get("specs") or {}
    if not isinstance(specs, dict):
        raise InputError("Поле specs должно быть объектом вида {\"характеристика\": \"значение\"}")

    clean_specs: dict[str, str] = {}
    for key, value in specs.items():
        key_s = _clean(str(key))
        val_s = _clean("" if value is None else str(value))
        if key_s and val_s:  # пустые значения отбрасываем: это не данные
            clean_specs[key_s] = val_s

    product = ProductInput(
        name=_clean(str(data.get("name") or "")),
        manufacturer=_clean(str(data.get("manufacturer") or "")),
        specs=clean_specs,
        description=_clean(str(data.get("description") or "")),
        source_url=data.get("source_url"),
    )
    if not product.name:
        raise InputError("Нет названия товара (поле name) — без него карточку не собрать")
    return product


def detect_type(product: ProductInput) -> str | None:
    """Грубо определяет тип прибора по названию и описанию."""
    text = f"{product.name} {product.description[:300]}".lower()
    for type_name, keywords in _TYPE_KEYWORDS:
        if any(k in text for k in keywords):
            return type_name
    return None


def find_spec(product: ProductInput, expected: str) -> str | None:
    """Ищет во входных характеристиках ту, что соответствует ожидаемой."""
    patterns = SPEC_SYNONYMS.get(expected, [expected])
    # Сначала самый точный синоним, потом остальные: «присоединительная резьба»
    # важнее, чем «расположение штуцера».
    for p in patterns:
        for key in product.specs:
            if p in key.lower():
                return key
    return None


def missing_fields(product: ProductInput) -> list[str]:
    """Список того, чего не хватает для хорошей карточки."""
    missing = []
    if not product.manufacturer:
        missing.append("производитель (manufacturer)")
    if not product.specs:
        missing.append("характеристики (specs) — пусто")
    if len(product.description) < 50:
        missing.append("описание (description) — пусто или слишком короткое")
    ptype = detect_type(product)
    for expected in EXPECTED_SPECS.get(ptype or "", []):
        if find_spec(product, expected) is None:
            missing.append(f"характеристика «{expected}»")
    return missing


def is_sparse(product: ProductInput) -> bool:
    """Данных так мало, что честное описание на 800+ символов не написать.

    В этом случае лучше короткий текст и просьба дополнить данные,
    чем «вода» и выдуманные преимущества.
    """
    return len(product.description) < 300 and len(product.specs) < 6


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()
