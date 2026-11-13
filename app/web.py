"""Веб-интерфейс и HTTP API.

Запуск: uvicorn app.web:app --reload  →  http://127.0.0.1:8000

- GET  /              — форма для контент-менеджера
- POST /api/card      — JSON товара → карточка (JSON + Markdown)
- POST /api/import    — ссылка на товар souz-pribor.ru → входной JSON
- GET  /api/health    — запущена ли Ollama и скачана ли модель
"""
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from . import llm
from .config import settings
from .generator import generate_card
from .importer import ImportError_, fetch_product
from .llm import LLMError
from .render import to_markdown
from .validation import InputError, parse_input

app = FastAPI(title="AI-помощник для карточек товаров")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    example = (Path(__file__).parent.parent / "examples" / "input" / "01_manometr_mp3u.json")
    return templates.TemplateResponse(request, "index.html", {
        "model": settings.ollama_model,
        "example": example.read_text(encoding="utf-8") if example.exists() else "",
    })


@app.post("/api/card")
async def make_card(request: Request):
    raw = await request.body()
    try:
        product = parse_input(raw.decode("utf-8", errors="replace"))
    except InputError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    try:
        # Генерация долгая и синхронная — выносим из event loop, чтобы сервер не «замерзал».
        card = await run_in_threadpool(generate_card, product)
    except LLMError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    return {"card": card.model_dump(), "markdown": to_markdown(card, product)}


class ImportRequest(BaseModel):
    url: str


@app.post("/api/import")
def import_product(req: ImportRequest):
    try:
        product = fetch_product(req.url)
    except ImportError_ as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    return product.model_dump(exclude_none=True)


@app.get("/api/health")
def health():
    return llm.health()
