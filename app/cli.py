"""Запуск из командной строки.

    python -m app.cli examples/input/01_manometr_mp3u.json
    python -m app.cli examples/input/*.json --out examples/output
    python -m app.cli --url https://www.souz-pribor.ru/product/mp3-u/

Для каждого товара сохраняет <имя>.json (структура) и <имя>.md (для копирования).
"""
import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

from .generator import generate_card
from .schemas import ProductInput
from .importer import ImportError_, fetch_product
from .llm import LLMError
from .render import to_markdown
from .validation import InputError, parse_input


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Черновик карточки товара по данным о приборе")
    ap.add_argument("files", nargs="*", help="JSON-файлы с данными о товарах")
    ap.add_argument("--url", action="append", default=[], help="ссылка на товар souz-pribor.ru")
    ap.add_argument("--out", default="examples/output", help="куда сохранить результат")
    args = ap.parse_args(argv)
    # Консоль Windows по умолчанию не в UTF-8: не падаем на редких символах вроде «≤».
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    if not args.files and not args.url:
        ap.print_help()
        return 2

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    failed = 0

    jobs: list[tuple[str, Callable[[], ProductInput]]] = []
    for f in args.files:
        path = Path(f)
        jobs.append((path.stem, lambda p=path: parse_input(p.read_text(encoding="utf-8"))))
    for url in args.url:
        slug = url.rstrip("/").rsplit("/", 1)[-1] or "product"
        jobs.append((slug, lambda u=url: fetch_product(u)))

    for stem, load in jobs:
        print(f"- {stem}: ", end="", flush=True)
        try:
            product = load()
        except (InputError, ImportError_, OSError) as e:
            print(f"ошибка входных данных: {e}")
            failed += 1
            continue
        try:
            card = generate_card(product)
        except LLMError as e:
            print(f"ошибка модели: {e}")
            failed += 1
            continue
        (out_dir / f"{stem}.json").write_text(
            json.dumps({"input": product.model_dump(exclude_none=True), "card": card.model_dump()},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        (out_dir / f"{stem}.md").write_text(to_markdown(card, product), encoding="utf-8")
        note = f", предупреждений: {len(card.warnings)}" if card.warnings else ""
        print(f"готово (попыток: {card.attempts}{note}): {out_dir / (stem + '.md')}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
