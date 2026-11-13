"""Формат входных и выходных данных."""
from pydantic import BaseModel, Field

NO_DATA = "данных нет"

TITLE_MAX = 70
DESC_MIN = 800
DESC_MAX = 1200
QUERIES_MIN = 3
QUERIES_MAX = 5


class ProductInput(BaseModel):
    name: str = ""
    manufacturer: str = ""
    specs: dict[str, str] = Field(default_factory=dict)
    description: str = ""
    source_url: str | None = None


class SpecItem(BaseModel):
    name: str
    value: str
    from_input: bool  # False — поле ожидается для такого прибора, но во входных данных его нет


class ProductCard(BaseModel):
    seo_title: str
    seo_description: str
    key_specs: list[SpecItem]
    search_queries: list[str]
    commercial_block: str
    missing_fields: list[str]  # чего не хватает во входных данных
    warnings: list[str]  # что контент-менеджеру проверить руками
    model: str
    attempts: int
    attempt_log: list[str] = Field(default_factory=list)  # что не прошло проверки на каждой попытке
