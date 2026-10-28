"""Распознавание голосовых: аудио → текст через облачный сервис.

Сейчас — Yandex SpeechKit. Другой сервис подключается ещё одной реализацией Transcriber,
выбор — переменной STT_PROVIDER в .env.
"""
import asyncio
import base64
import json
import os
from typing import Protocol

import httpx

SYNC_URL = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"
ASYNC_URL = "https://stt.api.cloud.yandex.net/stt/v3/recognizeFileAsync"
OPERATION_URL = "https://operation.api.cloud.yandex.net/operations/{id}"
RESULT_URL = "https://stt.api.cloud.yandex.net/stt/v3/getRecognition"

SYNC_MAX_SECONDS = 29       # синхронный режим: до 30 секунд и 1 МБ
SYNC_MAX_BYTES = 1_000_000
ASYNC_TIMEOUT = 180         # сколько ждать длинное голосовое, секунд
POLL_INTERVAL = 2


class TranscriptionError(Exception):
    pass


class Transcriber(Protocol):
    async def transcribe(self, audio: bytes, duration: int) -> str:
        """Текст из голосового Telegram (OGG/Opus). duration — длительность в секундах."""


class YandexTranscriber:
    def __init__(self, api_key: str, folder_id: str | None = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.headers = {"Authorization": f"Api-Key {api_key}"}
        if folder_id:
            self.headers["x-folder-id"] = folder_id
        self.transport = transport  # для тестов

    async def transcribe(self, audio: bytes, duration: int) -> str:
        async with httpx.AsyncClient(timeout=60, transport=self.transport) as client:
            if duration <= SYNC_MAX_SECONDS and len(audio) <= SYNC_MAX_BYTES:
                return await self._sync(client, audio)
            return await self._async(client, audio)

    async def _sync(self, client: httpx.AsyncClient, audio: bytes) -> str:
        r = await client.post(SYNC_URL, headers=self.headers, content=audio,
                              params={"lang": "ru-RU", "format": "oggopus"})
        _check(r)
        return r.json().get("result", "").strip()

    async def _async(self, client: httpx.AsyncClient, audio: bytes) -> str:
        body = {
            "content": base64.b64encode(audio).decode(),
            "recognitionModel": {
                "model": "general",
                "audioFormat": {"containerAudio": {"containerAudioType": "OGG_OPUS"}},
                "textNormalization": {"textNormalization": "TEXT_NORMALIZATION_ENABLED"},
                "languageRestriction": {"restrictionType": "WHITELIST", "languageCode": ["ru-RU"]},
            },
        }
        r = await client.post(ASYNC_URL, headers=self.headers, json=body)
        _check(r)
        op_id = r.json()["id"]

        waited = 0
        while True:
            r = await client.get(OPERATION_URL.format(id=op_id), headers=self.headers)
            _check(r)
            op = r.json()
            if op.get("done"):
                if op.get("error"):
                    raise TranscriptionError(op["error"].get("message", "ошибка распознавания"))
                break
            if waited >= ASYNC_TIMEOUT:
                raise TranscriptionError("распознавание заняло слишком много времени")
            await asyncio.sleep(POLL_INTERVAL)
            waited += POLL_INTERVAL

        r = await client.get(RESULT_URL, headers=self.headers, params={"operationId": op_id})
        _check(r)
        return parse_v3_result(r.text)


def parse_v3_result(raw: str) -> str:
    """Ответ getRecognition — JSON-объекты построчно. Берём нормализованный текст фраз,
    а если нормализации нет — обычный final."""
    finals, refined = {}, {}
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        msg = msg.get("result", msg)
        if str(msg.get("channelTag", "0")) != "0":
            continue
        if "finalRefinement" in msg:
            ref = msg["finalRefinement"]
            alts = ref.get("normalizedText", {}).get("alternatives", [])
            if alts:
                refined[int(ref.get("finalIndex", len(refined)))] = alts[0].get("text", "")
        elif "final" in msg:
            alts = msg["final"].get("alternatives", [])
            if alts:
                finals[len(finals)] = alts[0].get("text", "")
    parts = refined or finals
    return " ".join(t.strip() for _, t in sorted(parts.items()) if t.strip())


def _check(r: httpx.Response) -> None:
    if r.status_code >= 400:
        raise TranscriptionError(f"SpeechKit {r.status_code}: {r.text[:300]}")


def make_transcriber() -> Transcriber | None:
    """Распознаватель по настройкам .env или None, если голос не настроен."""
    provider = os.getenv("STT_PROVIDER", "yandex")
    if provider == "yandex":
        key = os.getenv("YANDEX_API_KEY")
        return YandexTranscriber(key, os.getenv("YANDEX_FOLDER_ID") or None) if key else None
    raise SystemExit(f"Неизвестный STT_PROVIDER: {provider}")
