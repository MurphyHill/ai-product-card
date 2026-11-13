"""Вывод карточки в Markdown — удобно копировать в CMS или отправить на вычитку."""
from .schemas import NO_DATA, ProductCard, ProductInput


def to_markdown(card: ProductCard, product: ProductInput) -> str:
    out: list[str] = [f"# {product.name}", ""]
    if product.source_url:
        out += [f"Источник данных: {product.source_url}", ""]

    if card.warnings:
        out.append("> **Проверить перед публикацией:**")
        out += [f"> - {w}" for w in card.warnings]
        out.append("")

    out += ["## SEO-заголовок", "", card.seo_title, "", f"_{len(card.seo_title)} символов_", ""]
    out += ["## SEO-описание", "", card.seo_description, "", f"_{len(card.seo_description)} символов_", ""]

    out += ["## Ключевые характеристики", "", "| Характеристика | Значение |", "|---|---|"]
    for s in card.key_specs:
        value = f"**{NO_DATA}**" if not s.from_input else s.value
        out.append(f"| {_cell(s.name)} | {_cell(value)} |")
    out.append("")

    out += ["## Поисковые запросы", ""]
    out += [f"- {q}" for q in card.search_queries]
    out.append("")

    out += ["## Для менеджера", "", card.commercial_block, ""]

    if card.missing_fields:
        out += ["## Не хватает во входных данных", ""]
        out += [f"- {m}" for m in card.missing_fields]
        out.append("")

    out.append(f"_Модель: {card.model}, попыток: {card.attempts}_")
    if card.attempt_log:
        out.append("")
        out.append("<details><summary>Журнал проверок</summary>")
        out.append("")
        out += [f"- {line}" for line in card.attempt_log]
        out.append("")
        out.append("</details>")
    return "\n".join(out) + "\n"


def _cell(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")
