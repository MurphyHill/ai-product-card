"""Настройки берутся из переменных окружения (или файла .env).

Ключей и адресов в коде нет: всё, что может отличаться между машинами,
задаётся снаружи. Пример — в .env.example.
"""
import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:  # python-dotenv необязателен
    pass


@dataclass(frozen=True)
class Settings:
    ollama_url: str = os.getenv("OLLAMA_URL", "http://localhost:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
    # Локальная модель на процессоре думает долго, поэтому таймаут большой.
    llm_timeout_s: float = float(os.getenv("LLM_TIMEOUT_S", "600"))
    llm_temperature: float = float(os.getenv("LLM_TEMPERATURE", "0.2"))
    # Сколько раз переспрашивать модель, если ответ не прошёл проверки.
    max_attempts: int = int(os.getenv("MAX_ATTEMPTS", "3"))


settings = Settings()
