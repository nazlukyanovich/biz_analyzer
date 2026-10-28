"""Распознавание без реальных запросов в SpeechKit: python -m unittest discover tests"""
import asyncio
import json
import unittest

import httpx

from bot import transcriber as t
from bot.transcriber import TranscriptionError, YandexTranscriber, parse_v3_result


def run(coro):
    return asyncio.run(coro)


class ParseTest(unittest.TestCase):
    def test_prefers_normalized_text(self):
        raw = "\n".join(json.dumps({"result": m}) for m in [
            {"channelTag": "0", "final": {"alternatives": [{"text": "у нас десять заявок"}]}},
            {"channelTag": "0", "finalRefinement": {"finalIndex": "0",
                                                    "normalizedText": {"alternatives": [{"text": "У нас 10 заявок."}]}}},
            {"channelTag": "0", "final": {"alternatives": [{"text": "в месяц"}]}},
            {"channelTag": "0", "finalRefinement": {"finalIndex": "1",
                                                    "normalizedText": {"alternatives": [{"text": "В месяц."}]}}},
            {"channelTag": "1", "final": {"alternatives": [{"text": "другой канал"}]}},
        ])
        self.assertEqual(parse_v3_result(raw), "У нас 10 заявок. В месяц.")

    def test_falls_back_to_final(self):
        raw = json.dumps({"result": {"channelTag": "0", "final": {"alternatives": [{"text": "привет"}]}}})
        self.assertEqual(parse_v3_result(raw), "привет")


class YandexTest(unittest.TestCase):
    def setUp(self):
        t.POLL_INTERVAL = 0

    def test_short_voice_uses_sync(self):
        def handler(req: httpx.Request):
            self.assertEqual(req.url.path, "/speech/v1/stt:recognize")
            self.assertEqual(req.headers["Authorization"], "Api-Key k")
            return httpx.Response(200, json={"result": "десять заявок в месяц"})

        tr = YandexTranscriber("k", transport=httpx.MockTransport(handler))
        self.assertEqual(run(tr.transcribe(b"ogg", 12)), "десять заявок в месяц")

    def test_long_voice_uses_async(self):
        calls = {"op": 0}

        def handler(req: httpx.Request):
            if req.url.path == "/stt/v3/recognizeFileAsync":
                self.assertIn("content", json.loads(req.content))
                return httpx.Response(200, json={"id": "op1", "done": False})
            if req.url.path == "/operations/op1":
                calls["op"] += 1
                return httpx.Response(200, json={"id": "op1", "done": calls["op"] > 1})
            if req.url.path == "/stt/v3/getRecognition":
                line = {"result": {"channelTag": "0", "final": {"alternatives": [{"text": "длинный ответ"}]}}}
                return httpx.Response(200, text=json.dumps(line))
            return httpx.Response(404)

        tr = YandexTranscriber("k", transport=httpx.MockTransport(handler))
        self.assertEqual(run(tr.transcribe(b"ogg", 90)), "длинный ответ")
        self.assertEqual(calls["op"], 2)

    def test_api_error(self):
        tr = YandexTranscriber("bad", transport=httpx.MockTransport(
            lambda req: httpx.Response(401, json={"error_message": "Unauthorized"})))
        with self.assertRaises(TranscriptionError):
            run(tr.transcribe(b"ogg", 5))


if __name__ == "__main__":
    unittest.main()
