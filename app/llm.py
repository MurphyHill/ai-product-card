"""Клиент локальной LLM через Ollama (http://localhost:11434).

Используется эндпоинт /api/chat с параметром format=<JSON-схема>: Ollama
заставляет модель отвечать строго по схеме (structured outputs).
"""
import json

import httpx

from .config import settings


class LLMError(RuntimeError):
    """Модель недоступна или ответила так, что ответ нельзя использовать."""


def chat_json(messages: list[dict], schema: dict) -> dict:
    """Отправляет диалог в модель и возвращает разобранный JSON-ответ."""
    payload = {
        "model": settings.ollama_model,
        "messages": messages,
        "format": schema,
        "stream": False,
        "options": {"temperature": settings.llm_temperature, "num_ctx": 8192},
    }
    url = f"{settings.ollama_url.rstrip('/')}/api/chat"
    try:
        resp = httpx.post(url, json=payload, timeout=settings.llm_timeout_s)
    except httpx.ConnectError as e:
        raise LLMError(
            f"Не удалось подключиться к Ollama по адресу {settings.ollama_url}. "
            "Запущена ли Ollama? (команда: ollama serve)"
        ) from e
    except httpx.TimeoutException as e:
        raise LLMError(
            f"Модель не ответила за {settings.llm_timeout_s:.0f} с. "
            "Увеличьте LLM_TIMEOUT_S или возьмите модель поменьше."
        ) from e
    except httpx.HTTPError as e:
        raise LLMError(f"Ошибка связи с Ollama: {e}") from e

    if resp.status_code == 404:
        raise LLMError(
            f"Модель {settings.ollama_model} не найдена в Ollama. "
            f"Скачайте её: ollama pull {settings.ollama_model}"
        )
    if resp.status_code != 200:
        raise LLMError(f"Ollama вернула ошибку {resp.status_code}: {resp.text[:300]}")

    try:
        content = resp.json()["message"]["content"]
    except (ValueError, KeyError, TypeError) as e:
        raise LLMError(f"Неожиданный формат ответа Ollama: {resp.text[:300]}") from e

    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise LLMError(f"Модель вернула не JSON: {content[:300]}") from e
    if not isinstance(data, dict):
        raise LLMError(f"Модель вернула JSON не того вида: {content[:300]}")
    return data


def health() -> dict:
    """Проверка: запущена ли Ollama и скачана ли нужная модель."""
    try:
        resp = httpx.get(f"{settings.ollama_url.rstrip('/')}/api/tags", timeout=5)
        models = [m.get("name", "") for m in resp.json().get("models", [])]
    except Exception as e:  # noqa: BLE001 — для health нужна любая ошибка
        return {"ok": False, "error": f"Ollama недоступна: {e}"}
    has_model = any(m == settings.ollama_model or m.startswith(settings.ollama_model + ":") for m in models)
    return {"ok": has_model, "model": settings.ollama_model, "available_models": models,
            "error": None if has_model else f"Модель {settings.ollama_model} не скачана"}
