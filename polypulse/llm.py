"""Клиент DeepSeek: генерация комментария к алерту. Фолбэк — вернуть None."""
from __future__ import annotations

import asyncio
import logging
import re

import httpx

from .config import Config
from .texts import SYSTEM_PROMPT, build_user_prompt

log = logging.getLogger(__name__)

TIMEOUT = 30.0
RETRIES = 2
MAX_COMMENT_LEN = 700


def _validate(text: str, source_prompt: str) -> str | None:
    """Отсекаем мусор: длину, markdown-заголовки, посторонние ссылки."""
    text = text.strip()
    if not text or len(text) > MAX_COMMENT_LEN:
        return None
    if re.search(r"^#{1,6}\s", text, re.MULTILINE):
        return None
    for url in re.findall(r"https?://\S+|\bwww\.\S+", text):
        if url not in source_prompt:
            return None
    return text


class DeepSeekClient:
    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._client = httpx.AsyncClient(
            base_url=cfg.deepseek_base_url,
            timeout=TIMEOUT,
            headers={"Authorization": f"Bearer {cfg.deepseek_api_key}"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def generate_comment(self, alert_type: str, question: str, payload: dict,
                               volume_total: float | None, end_date: str | None) -> str | None:
        """Один вызов на алерт. None => publisher использует чистый шаблон."""
        user_prompt = build_user_prompt(alert_type, question, payload, volume_total, end_date)
        body = {
            "model": self._cfg.deepseek_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.4,
            # deepseek-v4-pro — reasoning-модель: бюджет должен вмещать и
            # рассуждения (~300-500 токенов), и сам ответ, иначе finish_reason=length
            "max_tokens": 2000,
        }
        for attempt in range(RETRIES):
            try:
                resp = await self._client.post("/chat/completions", json=body)
                resp.raise_for_status()
                choice = resp.json()["choices"][0]
                if choice.get("finish_reason") != "stop":
                    log.warning("DeepSeek: finish_reason=%s (ответ обрезан), фолбэк",
                                choice.get("finish_reason"))
                    return None
                content = choice["message"]["content"]
                valid = _validate(content, user_prompt)
                if valid is None:
                    log.warning("DeepSeek: ответ не прошёл валидацию, фолбэк на шаблон")
                return valid
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
                log.warning("DeepSeek: попытка %d/%d не удалась: %s", attempt + 1, RETRIES, exc)
                if attempt < RETRIES - 1:
                    await asyncio.sleep(2)
        log.warning("DeepSeek недоступен, публикуем без LLM-комментария")
        return None
