"""Тесты логики без обращения к LLM (модель подменяется заглушкой)."""
import json
from pathlib import Path

import pytest

from app import generator
from app.factcheck import invented_numbers
from app.generator import build_key_specs, check_answer, generate_card
from app.importer import parse_product_page
from app.render import to_markdown
from app.schemas import NO_DATA, ProductInput
from app.validation import InputError, detect_type, missing_fields, parse_input

EXAMPLES = Path(__file__).parent.parent / "examples" / "input"

MANOMETR = ProductInput(
    name="Манометр показывающий DM 02",
    manufacturer="Манотомь",
    specs={"диапазон измерений": "0–1,6 МПа", "класс точности": "1,5", "присоединение": "G1/2"},
    description="Прибор предназначен для измерения избыточного давления жидкостей и газов в системах водоснабжения.",
)


# ---------- входные данные ----------

def test_broken_json_gives_clear_error():
    with pytest.raises(InputError, match="Некорректный JSON"):
        parse_input((EXAMPLES / "05_bityi_json.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("raw", ["[]", '"строка"', "42"])
def test_not_an_object(raw):
    with pytest.raises(InputError):
        parse_input(raw)


def test_name_is_required():
    with pytest.raises(InputError, match="name"):
        parse_input('{"manufacturer": "ОВЕН", "specs": {}}')


def test_specs_must_be_object():
    with pytest.raises(InputError, match="specs"):
        parse_input('{"name": "Манометр", "specs": ["0-1 МПа"]}')


def test_empty_and_null_specs_are_dropped():
    p = parse_input((EXAMPLES / "04_malo_dannyh.json").read_text(encoding="utf-8"))
    assert p.specs == {"выходной сигнал": "4–20 мА"}


def test_missing_fields_reported():
    p = parse_input((EXAMPLES / "04_malo_dannyh.json").read_text(encoding="utf-8"))
    missing = missing_fields(p)
    assert any("производитель" in m for m in missing)
    assert any("описание" in m for m in missing)
    assert any("диапазон измерений" in m for m in missing)
    assert any("напряжение питания" in m for m in missing)


def test_detect_type():
    assert detect_type(MANOMETR) == "манометр"
    assert detect_type(ProductInput(name="Преобразователь давления ПД100")) == "датчик давления"
    assert detect_type(ProductInput(name="Паяльная станция")) is None


# ---------- характеристики: только из входа ----------

def test_key_specs_come_only_from_input():
    specs = build_key_specs(MANOMETR)
    from_input = {s.name: s.value for s in specs if s.from_input}
    assert from_input == MANOMETR.specs  # ни одного значения, которого не было на входе
    no_data = [s.name.lower() for s in specs if not s.from_input]
    assert no_data == ["диаметр корпуса"]
    assert all(s.value == NO_DATA for s in specs if not s.from_input)


# ---------- проверка выдуманных чисел ----------

def test_invented_numbers_detected():
    text = "Манометр DM 02 с диапазоном 0–1,6 МПа, класс точности 1,5, гарантия 3 года, IP54."
    assert invented_numbers(text, MANOMETR) == ["3", "54"]


def test_number_formats_normalized():
    # 1.5 и 1,5 — одно и то же; G1/2 — из входа
    assert invented_numbers("класс 1.5, резьба G1/2, DM 02", MANOMETR) == []


PD100 = ProductInput(
    name="Преобразователь давления ОВЕН ПД100",
    manufacturer="ОВЕН",
    specs={"Средний срок службы": "12 лет", "Межповерочный интервал": "2 года", "Степень защиты корпуса": "IP65"},
    description="Датчик для объектов ЖКХ.",
)


def test_number_attached_to_wrong_term():
    # реальная ошибка модели: 12 лет — это срок службы, гарантии в данных нет
    from app.factcheck import misattributed_numbers
    assert misattributed_numbers("Гарантия на устройство составляет 12 лет.", PD100) == ["гарантия: 12"]
    assert misattributed_numbers("Средний срок службы 12 лет, межповерочный интервал 2 года.", PD100) == []
    assert misattributed_numbers("Межповерочный интервал 12 лет.", PD100) == ["межповерочный интервал: 12"]


def test_guarantee_verb_is_not_warranty():
    from app.factcheck import misattributed_numbers
    assert misattributed_numbers("Степень защиты IP65 гарантирует работу.", PD100) == []


def test_decoded_ip_claims():
    from app.factcheck import decoded_claims
    assert decoded_claims("Степень защиты IP65 обеспечивает защиту от пыли и воды.", PD100)
    assert decoded_claims("Корпус влагозащищённый.", PD100)
    assert decoded_claims("Степень защиты корпуса IP65.", PD100) == []
    assert decoded_claims("Применяется в трубопроводах сетевой воды.", PD100) == []


def test_unit_conversion_is_flagged():
    # 1,6 МПа = 16 кгс/см² — но пересчёта во входе нет, значит это «выдумка» модели
    assert "16" in invented_numbers("до 16 кгс/см²", MANOMETR)


# ---------- проверка ответа модели ----------

GOOD_DESC = (
    "Показывающий манометр предназначен для контроля избыточного давления жидкостей и газов. "
    "Прибор выполнен в стальном корпусе и устанавливается на трубопровод через резьбовое присоединение. "
    "Шкала хорошо читается с рабочего места оператора, а стрелка движется плавно.\n\n"
    "Манометр применяют в системах водоснабжения, на тепловых пунктах и в котельных. "
    "Его ставят на насосных станциях, в сетях отопления и на технологическом оборудовании. "
    "Монтаж выполняется гаечным ключом за штуцер без усилия на корпус.\n\n"
    "Перед установкой проверяют совпадение резьбы и отсутствие давления в линии. "
    "При пульсирующем давлении рекомендуется демпфер, а при горячей среде — отвод-охладитель. "
    "Корпус не требует обслуживания, стекло протирают мягкой тканью без растворителей.\n\n"
    "Поверку проводят в аккредитованной лаборатории, отметку ставят в паспорте прибора. "
    "Хранить манометр следует в упаковке, в сухом помещении без агрессивных паров."
)


def _good_answer(**over):
    a = {
        "seo_title": "Манометр показывающий DM 02 Манотомь 0–1,6 МПа",
        "seo_description": GOOD_DESC,
        "search_queries": ["купить манометр DM 02", "манометр 0–1,6 МПа", "манометр Манотомь"],
        "commercial_block": "Подходит для систем водоснабжения.",
    }
    a.update(over)
    return a


def test_good_answer_has_no_problems():
    assert check_answer(_good_answer(), MANOMETR) == []


def test_cliches_flagged():
    problems = check_answer(_good_answer(commercial_block="Идеальное решение для водоснабжения."), MANOMETR)
    assert any(p.field == "style" for p in problems)


def test_find_spec_prefers_thread_over_fitting_position():
    p = ProductInput(name="Манометр МП3-У", specs={"Расположение штуцера": "Радиальное",
                                                   "Резьба присоединительного штуцера": "М20*1,5"})
    specs = build_key_specs(p)
    # порядок для манометра: диапазон, класс точности, присоединение, диаметр корпуса
    assert specs[2].name == "Резьба присоединительного штуцера"
    assert specs[2].from_input


def test_long_title_and_short_description():
    problems = check_answer(_good_answer(seo_title="М" * 80, seo_description="коротко"), MANOMETR)
    fields = {p.field for p in problems}
    assert {"seo_title", "seo_description"} <= fields


# ---------- конвейер целиком с заглушкой вместо модели ----------

def test_retry_fixes_invented_numbers(monkeypatch):
    answers = iter([
        _good_answer(commercial_block="Гарантия 5 лет, IP65."),  # выдумка
        _good_answer(),  # исправлено
    ])
    calls = []

    def fake_chat(messages, schema, **k):
        calls.append(list(messages))
        return next(answers)

    monkeypatch.setattr(generator, "chat_json", fake_chat)
    card = generate_card(MANOMETR)
    assert card.attempts == 2
    assert not any("числа" in w for w in card.warnings)
    # во второй запрос модель получила список своих ошибок
    assert "65" in calls[1][-1]["content"]


def test_llm_failure_on_retry_keeps_best_answer(monkeypatch):
    from app.llm import LLMError
    calls = {"n": 0}

    def flaky(messages, schema, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return _good_answer(commercial_block="IP65")  # есть ошибка → будет повтор
        raise LLMError("Ollama недоступна")

    monkeypatch.setattr(generator, "chat_json", flaky)
    card = generate_card(MANOMETR)  # не падает
    assert card.seo_title
    assert any("ошибка модели" in line for line in card.attempt_log)


def test_llm_failure_on_first_attempt_is_raised(monkeypatch):
    from app.llm import LLMError

    def down(messages, schema, **k):
        raise LLMError("Ollama недоступна")

    monkeypatch.setattr(generator, "chat_json", down)
    with pytest.raises(LLMError):
        generate_card(MANOMETR)


def test_unfixable_answer_ends_with_warnings(monkeypatch):
    monkeypatch.setattr(generator, "chat_json", lambda m, s, **k: _good_answer(commercial_block="IP65"))
    card = generate_card(MANOMETR)
    assert card.attempts == generator.settings.max_attempts
    assert any("65" in w for w in card.warnings)


def test_too_long_description_drops_middle_paragraph():
    from app.generator import _drop_paragraph_to_fit
    paras = ["Первый абзац о приборе. " * 14, "Технические детали. " * 18,
             "Конструкция. " * 25, "Применение в ЖКХ. " * 14]
    desc = "\n\n".join(paras)
    assert len(desc) > 1200
    trimmed, removed = _drop_paragraph_to_fit(desc)
    assert removed in (1, 2)  # первый и последний абзацы сохранены
    assert 800 <= len(trimmed) <= 1200
    assert trimmed.startswith("Первый абзац") and trimmed.rstrip().endswith("ЖКХ.")


def test_paragraphs_are_joined():
    from app.generator import normalize
    a = normalize({"seo_description_paragraphs": ["Первый абзац.", " ", "Второй абзац."]})
    assert a["seo_description"] == "Первый абзац.\n\nВторой абзац."


def test_sparse_input_gets_short_honest_text(monkeypatch):
    sparse = ProductInput(name="Датчик давления ПД-Тест", specs={"выходной сигнал": "4–20 мА"})
    short = _good_answer(seo_title="Датчик давления ПД-Тест 4–20 мА",
                         seo_description="Датчик давления ПД-Тест с выходным сигналом 4–20 мА.",
                         search_queries=["датчик давления ПД-Тест", "купить датчик ПД-Тест", "датчик 4–20 мА"])
    monkeypatch.setattr(generator, "chat_json", lambda m, s, **k: short)
    card = generate_card(sparse)
    assert card.attempts == 1  # короткий текст при скудных данных — это нормально, не переспрашиваем
    assert "Данных мало" in card.warnings[0]
    # а модель получила явную просьбу не растягивать текст
    assert check_answer(short, sparse, sparse=True) == []


def test_markdown_shows_no_data(monkeypatch):
    monkeypatch.setattr(generator, "chat_json", lambda m, s, **k: _good_answer())
    card = generate_card(MANOMETR)
    md = to_markdown(card, MANOMETR)
    assert f"**{NO_DATA}**" in md
    assert "## SEO-описание" in md


# ---------- импорт со страницы ----------

PAGE = """<html><body>
<h1>Манотомь МП3-У - манометр показывающий</h1>
<a class="brand-block__name" href="/brands/x/">Манотомь (Россия)</a>
<div id="description"><div class="product-description"><h3>Назначение</h3>
<p>Для измерения давления.</p><ul><li>теплоснабжение</li></ul></div></div>
<div id="specifications"><table>
<tr><td><strong>Наименование</strong></td><td><strong>Значение</strong></td></tr>
<tr><td colspan="2">Общие</td></tr>
<tr><td>Диаметр корпуса</td><td>100 мм</td></tr>
</table></div></body></html>"""


def test_parse_product_page():
    p = parse_product_page(PAGE, "https://www.souz-pribor.ru/product/x/")
    assert p.name == "Манометр показывающий Манотомь МП3-У"
    assert p.manufacturer == "Манотомь"
    assert p.specs == {"Диаметр корпуса": "100 мм"}
    assert "- теплоснабжение" in p.description


# ---------- фид и параметры из названия модификации ----------

def test_specs_from_modification_name():
    from app.validation import specs_from_name
    p = ProductInput(name="Манометр показывающий Манотомь МП3-У 0 - 100 кгс/см2, кл.т. 1,5")
    assert specs_from_name(p) == {"диапазон измерений": "0 - 100 кгс/см2", "класс точности": "1,5"}
    assert not any("диапазон" in m for m in missing_fields(p))
    specs = build_key_specs(p)
    assert specs[0].name == "Диапазон измерений (из названия модификации)" and specs[0].from_input


def test_codes_in_name_are_not_decoded():
    from app.validation import specs_from_name
    # «ДИ0,1-311-1,0» — код модификации; расшифровывать его — домысливание
    assert specs_from_name(ProductInput(name="Преобразователь давления ОВЕН ПД100-ДИ0,1-311-1,0")) == {}


FEED = """<?xml version="1.0" encoding="UTF-8"?><yml_catalog><shop><offers>
<offer id="1"><name>Другое</name><typePrefix>модем</typePrefix><vendor>ОВЕН</vendor><url>https://www.souz-pribor.ru/product/x/</url></offer>
<offer id="205786"><name>МП3-У 0 - 100 кгс/см2, кл.т. 1,5</name><typePrefix>манометр показывающий</typePrefix>
<vendor>Манотомь</vendor><url>https://www.souz-pribor.ru/product/mp3-u/</url></offer>
</offers></shop></yml_catalog>"""


def test_product_from_feed_offer(tmp_path, monkeypatch):
    from app import feed
    f = tmp_path / "feed.xml"
    f.write_text(FEED, encoding="utf-8")
    monkeypatch.setattr(feed, "fetch_product", lambda url: ProductInput(
        name="x", specs={"Диаметр корпуса": "100 мм"}, description="Для измерения давления."))
    p = feed.product_from_offer("205786", feed_file=f)
    assert p.name == "Манометр показывающий Манотомь МП3-У 0 - 100 кгс/см2, кл.т. 1,5"
    assert p.manufacturer == "Манотомь"
    assert p.specs == {"Диаметр корпуса": "100 мм"}
    assert "205786" in p.source_url


def test_unknown_offer(tmp_path):
    from app import feed
    from app.importer import ImportError_
    f = tmp_path / "feed.xml"
    f.write_text(FEED, encoding="utf-8")
    with pytest.raises(ImportError_, match="не найден"):
        feed.find_offer("999", feed_file=f)


def test_examples_are_valid_inputs():
    for f in ["01_manometr_mp3u.json", "02_datchik_pd100.json", "03_dm02_iz_zadaniya.json"]:
        p = parse_input(json.loads((EXAMPLES / f).read_text(encoding="utf-8")))
        assert p.name


def test_decoded_claim_fix_quotes_sentence_and_raises_temperature(monkeypatch):
    base = dict(seo_title="Преобразователь давления ОВЕН ПД100",
                search_queries=["купить ПД100", "преобразователь давления ОВЕН ПД100", "датчик давления для ЖКХ"])
    answers = iter([
        _good_answer(**base, commercial_block="Степень защиты IP65 обеспечивает защиту от пыли и воды."),
        _good_answer(**base, commercial_block="Степень защиты корпуса IP65."),
    ])
    seen = []

    def fake_chat(messages, schema, temperature=None):
        seen.append((list(messages), temperature))
        return next(answers)

    monkeypatch.setattr(generator, "chat_json", fake_chat)
    card = generate_card(PD100)
    assert card.attempts == 2
    fix_msg = seen[1][0][-1]["content"]
    assert "«Степень защиты IP65 обеспечивает защиту от пыли и воды.»" in fix_msg
    assert seen[1][1] > seen[0][1]  # на повторе температура выше


def test_broken_model_json_is_retried(monkeypatch):
    from app.llm import LLMError
    calls = {"n": 0}

    def flaky(messages, schema, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise LLMError("оборванный JSON", retryable=True)
        return _good_answer()

    monkeypatch.setattr(generator, "chat_json", flaky)
    card = generate_card(MANOMETR)
    assert card.seo_title and calls["n"] == 2
    assert "оборванный JSON" in card.attempt_log[0]


def test_feed_download_sends_user_agent(tmp_path, monkeypatch):
    # без User-Agent сайт отвечает 403 — нашлось при проверке «с нуля»
    from app import feed
    seen = {}

    class FakeResp:
        status_code = 200

        def iter_bytes(self):
            yield FEED.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_stream(method, url, **kw):
        seen.update(kw.get("headers") or {})
        return FakeResp()

    monkeypatch.setattr(feed, "CACHE", tmp_path / "feed.xml")
    monkeypatch.setattr(feed.httpx, "stream", fake_stream)
    assert feed.find_offer("205786")["vendor"] == "Манотомь"
    assert "User-Agent" in seen and "python" not in seen["User-Agent"].lower()


def test_repeated_sentences_detected_and_removed():
    from app.generator import _drop_repeated_sentences, _repeated_sentences
    text = ("Манометр DM 02 предназначен для измерения избыточного давления.\n\n"
            "Манометр DM 02 предназначен для измерения избыточного давления. Присоединение G1/2.")
    assert _repeated_sentences(text) == ["Манометр DM 02 предназначен для измерения избыточного давления."]
    cleaned = _drop_repeated_sentences(text)
    assert cleaned.count("предназначен") == 1 and "G1/2" in cleaned


def test_paraphrased_repeat_detected():
    # реальный повтор модели из примера 03: та же мысль другими словами
    from app.generator import _repeated_sentences
    text = ("Манометр показывающий DM 02 предназначен для измерения избыточного давления в диапазоне "
            "0–1,6 МПа с классом точности 1,5. Присоединение G1/2 и диаметр корпуса 100 мм.\n\n"
            "Прибор предназначен для измерения избыточного давления в диапазоне 0–1,6 МПа с классом "
            "точности 1,5 и присоединением G1/2.")
    assert len(_repeated_sentences(text)) == 1
    assert _repeated_sentences(GOOD_DESC) == []
