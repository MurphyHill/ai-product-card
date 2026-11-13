"""Основной конвейер: вход → проверка → LLM → проверка ответа → карточка.

Схема:
1. parse_input / missing_fields — разбираем вход и находим, чего не хватает.
2. Модель пишет тексты (заголовок, описание, запросы, блок для менеджера).
3. Код проверяет ответ: длины, количество запросов, «выдуманные» числа.
4. Если есть ошибки — модель получает их список и переписывает (до MAX_ATTEMPTS раз).
5. Список характеристик собирается кодом из входных данных, без участия модели.
6. Всё, что не удалось исправить, попадает в warnings — контент-менеджер видит это сразу.
"""
import json
from dataclasses import dataclass

from .config import settings
from .factcheck import decoded_claims, invented_numbers, misattributed_numbers
from .llm import LLMError, chat_json
from .prompts import RESPONSE_SCHEMA, SYSTEM_PROMPT, build_fix_prompt, build_user_prompt
from .schemas import (
    DESC_MAX,
    DESC_MIN,
    NO_DATA,
    QUERIES_MAX,
    QUERIES_MIN,
    TITLE_MAX,
    ProductCard,
    ProductInput,
    SpecItem,
)
from .validation import EXPECTED_SPECS, detect_type, find_spec, is_sparse, missing_fields, specs_from_name


def build_key_specs(product: ProductInput) -> list[SpecItem]:
    """Характеристики берутся только из входных данных.

    Сначала — ожидаемые для этого типа прибора (если их нет, «данных нет»),
    затем все остальные из входа в исходном порядке.
    """
    items: list[SpecItem] = []
    used: set[str] = set()
    from_name = specs_from_name(product)
    for expected in EXPECTED_SPECS.get(detect_type(product) or "", []):
        key = find_spec(product, expected)
        if expected in from_name:
            # значение конкретной модификации из названия важнее общего значения по серии
            items.append(SpecItem(name=f"{expected.capitalize()} (из названия модификации)",
                                  value=from_name[expected], from_input=True))
        elif key and key not in used:
            items.append(SpecItem(name=key, value=product.specs[key], from_input=True))
            used.add(key)
        elif not key:
            items.append(SpecItem(name=expected.capitalize(), value=NO_DATA, from_input=False))
    for key, value in product.specs.items():
        if key not in used:
            items.append(SpecItem(name=key, value=value, from_input=True))
    return items


BANNED_WORDS = ["идеальн", "лучший", "лучшее", "уникальн", "инновацион", "высочайш", "непревзойд", "революцион",
                "универсальное решение", "универсальным решением"]


@dataclass
class Problem:
    field: str  # какое поле ответа
    what: str  # что не так — для человека
    fix: str  # что сделать — для модели


def normalize(answer: dict) -> dict:
    """Модель присылает описание списком абзацев — склеиваем в один текст."""
    out = dict(answer)
    paragraphs = answer.get("seo_description_paragraphs")
    if isinstance(paragraphs, list):
        out["seo_description"] = "\n\n".join(str(p).strip() for p in paragraphs if str(p).strip())
    return out


def check_answer(answer: dict, product: ProductInput, sparse: bool = False) -> list[Problem]:
    """Возвращает список проблем в ответе модели (пустой — всё хорошо).

    sparse=True — данных мало: нижнюю границу длины описания не требуем,
    чтобы не провоцировать модель на «воду» и выдумки.
    """
    problems: list[Problem] = []
    title = str(answer.get("seo_title") or "").strip()
    desc = str(answer.get("seo_description") or "").strip()
    n_par = len([p for p in desc.split("\n\n") if p.strip()])
    queries = [str(q).strip() for q in (answer.get("search_queries") or []) if str(q).strip()]
    commercial = str(answer.get("commercial_block") or "").strip()

    if not title:
        problems.append(Problem("seo_title", "SEO-заголовок пустой", "Напиши seo_title."))
    elif len(title) > TITLE_MAX:
        problems.append(Problem("seo_title", f"SEO-заголовок длиной {len(title)} символов (нужно ≤ {TITLE_MAX})",
                                f"Сократи seo_title до {TITLE_MAX} символов (сейчас {len(title)})."))
    if not desc:
        problems.append(Problem("seo_description", "SEO-описание пустое", "Напиши seo_description_paragraphs."))
    elif len(desc) < DESC_MIN and not sparse:
        problems.append(Problem(
            "seo_description", f"SEO-описание длиной {len(desc)} символов (нужно {DESC_MIN}–{DESC_MAX})",
            f"Описание получилось {len(desc)} символов в {n_par} абзацах, а нужно от {DESC_MIN} до {DESC_MAX}. "
            f"Сделай 5 абзацев по 2–3 предложения. Не хватает примерно {DESC_MIN + 100 - len(desc)} символов — "
            "добавь абзацы о том, что есть в ДАННЫХ и ещё не упомянуто: измеряемая среда, исполнения и опции, "
            "материалы и конструкция, условия эксплуатации, поверка."))
    elif len(desc) > DESC_MAX:
        problems.append(Problem(
            "seo_description", f"SEO-описание длиной {len(desc)} символов (нужно {DESC_MIN}–{DESC_MAX})",
            f"Описание получилось {len(desc)} символов, сократи до {DESC_MAX - 100}: убери второстепенный абзац."))
    if not (QUERIES_MIN <= len(queries) <= QUERIES_MAX):
        problems.append(Problem("search_queries", f"Поисковых запросов {len(queries)} (нужно {QUERIES_MIN}–{QUERIES_MAX})",
                                f"Дай от {QUERIES_MIN} до {QUERIES_MAX} поисковых запросов."))
    if not commercial:
        problems.append(Problem("commercial_block", "Блок для менеджера пустой", "Напиши commercial_block."))

    all_text = " ".join([title, desc, commercial, *queries])
    cliches = sorted({w for w in BANNED_WORDS if w in all_text.lower()})
    if cliches:
        problems.append(Problem("style", "Рекламные штампы: " + ", ".join(cliches),
                                "Убери рекламные штампы: " + ", ".join(cliches) + ". Пиши по-деловому."))

    invented = invented_numbers(all_text, product)
    if invented:
        problems.append(Problem(
            "facts", "В тексте есть числа, которых нет во входных данных: " + ", ".join(invented),
            "В тексте есть числа, которых нет в ДАННЫХ: " + ", ".join(invented)
            + ". Убери их или замени на значения строго из ДАННЫХ."))

    wrong = misattributed_numbers(all_text, product)
    if wrong:
        problems.append(Problem(
            "facts", "Числа отнесены не к той характеристике: " + "; ".join(wrong),
            "Ты приписал значения не тем характеристикам: " + "; ".join(wrong) + ". В ДАННЫХ эти числа относятся "
            "к другим параметрам (или такого параметра в ДАННЫХ нет). Исправь: каждое значение — только рядом "
            "с той характеристикой, к которой оно относится в ДАННЫХ."))

    decoded = decoded_claims(all_text, product)
    if decoded:
        sentences = _sentences_with(all_text, decoded)
        problems.append(Problem(
            "facts", "Расшифровка кодов, которой нет в данных: " + ", ".join(decoded),
            "В ДАННЫХ нет расшифровки кодов защиты, а ты её добавил. Перепиши эти предложения, оставив только "
            "сам код (например «Степень защиты — IP40, по заказу — IP54.»), без слов о пыли, воде и влаге:\n"
            + "\n".join(f"  «{s}»" for s in sentences)))
    return problems


def _sentences_with(text: str, phrases: list[str]) -> list[str]:
    """Предложения, в которых встречаются найденные фразы, — чтобы показать модели, что именно исправить."""
    import re

    out = []
    for s in re.split(r"(?<=[.!?])\s+|\n+", text):
        if any(p in s.lower() for p in phrases) and s.strip() not in out:
            out.append(s.strip())
    return out


def generate_card(product: ProductInput) -> ProductCard:
    missing = missing_fields(product)
    sparse = is_sparse(product)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(product, missing, sparse)},
    ]

    best: dict | None = None
    best_problems: list[Problem] | None = None
    log: list[str] = []
    attempts = 0
    for attempts in range(1, settings.max_attempts + 1):
        try:
            # На повторах чуть повышаем температуру: при одной и той же температуре
            # модель часто присылает слово в слово тот же ответ и не исправляется.
            temperature = settings.llm_temperature + 0.2 * (attempts - 1)
            raw = chat_json(messages, RESPONSE_SCHEMA, temperature=temperature)
        except LLMError as e:
            if best is None:
                if e.retryable and attempts < settings.max_attempts:
                    # модель ответила, но ответ испорчен (оборван, не JSON) — пробуем ещё раз
                    log.append(f"попытка {attempts}: {e}")
                    continue
                raise  # ни одного годного ответа — показываем ошибку пользователю
            # Ответ уже есть, сломалась только попытка исправления — отдаём лучший вариант.
            log.append(f"попытка {attempts}: ошибка модели ({e}); оставлен лучший из полученных вариантов")
            attempts -= 1
            break
        answer = normalize(raw)
        problems = check_answer(answer, product, sparse)
        log.append(f"попытка {attempts}: " + ("; ".join(p.what for p in problems) if problems else "ок"))
        if best_problems is None or _score(problems) < _score(best_problems):
            best, best_problems = answer, problems
        if not problems:
            break
        messages.append({"role": "assistant", "content": json.dumps(raw, ensure_ascii=False)})
        messages.append({"role": "user", "content": build_fix_prompt([p.fix for p in problems])})

    assert best is not None and best_problems is not None
    card = _assemble(best, best_problems, product, missing, attempts)
    if sparse and len(card.seo_description) < DESC_MIN:
        card.warnings.insert(0, f"Данных мало: честное описание на {DESC_MIN}–{DESC_MAX} символов не написать, "
                                "текст намеренно короткий. Дополните описание и характеристики и сгенерируйте заново.")
    card.attempt_log = log
    return card


def _score(problems: list[Problem]) -> int:
    # Выдуманные числа — самое опасное, поэтому весят больше, чем длина текста.
    return sum(10 if p.field == "facts" else 1 for p in problems)


def _assemble(answer: dict, problems: list[Problem], product: ProductInput,
              missing: list[str], attempts: int) -> ProductCard:
    warnings: list[str] = []

    title = str(answer.get("seo_title") or "").strip()
    if len(title) > TITLE_MAX:
        title = _cut_words(title, TITLE_MAX)
        warnings.append(f"SEO-заголовок был длиннее {TITLE_MAX} символов и обрезан по слову.")

    queries: list[str] = []
    for q in answer.get("search_queries") or []:
        q = str(q).strip()
        if q and q.lower() not in {x.lower() for x in queries}:
            queries.append(q)
    queries = queries[:QUERIES_MAX]

    desc = str(answer.get("seo_description") or "").strip()
    if len(desc) > DESC_MAX:
        trimmed, removed = _drop_paragraph_to_fit(desc)
        if removed is not None:
            desc = trimmed
            problems = [p for p in problems if p.field != "seo_description"]
            warnings.append(f"Модель не уложилась в {DESC_MAX} символов — убран абзац №{removed + 1} "
                            "(технические детали, они есть в таблице характеристик).")
    warnings += [p.what for p in problems if p.field != "seo_title"]

    if missing:
        warnings.append("Во входных данных не хватает сведений — карточка написана без них (см. missing_fields).")

    return ProductCard(
        seo_title=title,
        seo_description=desc,
        key_specs=build_key_specs(product),
        search_queries=queries,
        commercial_block=str(answer.get("commercial_block") or "").strip(),
        missing_fields=missing,
        warnings=warnings,
        model=f"ollama/{settings.ollama_model}",
        attempts=attempts,
    )


def _drop_paragraph_to_fit(desc: str) -> tuple[str, int | None]:
    """Если описание длиннее нормы — пробуем убрать один абзац, чтобы попасть в 800–1200.

    Первый абзац (что за прибор) и последний (применение) важнее всего для SEO,
    поэтому сначала пробуем средние: в них обычно технические параметры,
    которые и так есть в таблице характеристик.
    """
    paragraphs = [p for p in desc.split("\n\n") if p.strip()]
    if len(paragraphs) < 3:
        return desc, None
    middle_first = list(range(1, len(paragraphs) - 1)) + [len(paragraphs) - 1]
    for i in sorted(middle_first, key=lambda i: (i == len(paragraphs) - 1, -i)):
        rest = "\n\n".join(p for j, p in enumerate(paragraphs) if j != i)
        if DESC_MIN <= len(rest) <= DESC_MAX:
            return rest, i
    return desc, None


def _cut_words(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(" ,.;:—-")
