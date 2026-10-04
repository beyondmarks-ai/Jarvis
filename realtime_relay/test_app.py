import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import app


class AudioTests(unittest.TestCase):
    def test_long_recording_is_split_without_losing_samples(self):
        audio = bytes(range(256)) * 470
        frames = list(app.stt_frames(audio))
        self.assertEqual(b"".join(frames), audio)
        self.assertTrue(all(0 < len(frame) <= 3200 and len(frame) % 2 == 0 for frame in frames))

    def test_incomplete_sample_is_rejected(self):
        with self.assertRaises(ValueError):
            list(app.stt_frames(b"123"))

    def test_silence_encodes_to_mulaw_silence(self):
        self.assertEqual(app.pcm24k_to_ulaw12k(b"\0" * 16), b"\xff" * 4)


class AnswerTests(unittest.IsolatedAsyncioTestCase):
    async def call_with_content(self, content, language=None):
        response = MagicMock()
        response.json.return_value = {"choices": [{"message": {"content": content}, "finish_reason": "length"}]}
        client = AsyncMock()
        client.post.return_value = response
        with patch.object(app.httpx, "AsyncClient") as factory, patch.object(app, "require_env", return_value="test"):
            factory.return_value.__aenter__.return_value = client
            answer = await app.ask_sarvam("Test question", language)
            if language:
                prompt = client.post.call_args.kwargs["json"]["messages"][0]["content"]
                self.assertIn(app.SPOKEN_LANGUAGES[language], prompt)
                self.assertIn("native script", prompt)
            return answer

    async def test_null_answer_is_a_clear_error(self):
        with self.assertRaisesRegex(RuntimeError, "returned no text"):
            await self.call_with_content(None)

    async def test_valid_answer(self):
        self.assertEqual(await self.call_with_content(" Four. "), "Four.")

    async def test_detected_language_is_in_answer_prompt(self):
        self.assertEqual(await self.call_with_content("उत्तर", "hi-IN"), "उत्तर")


class LanguageTests(unittest.IsolatedAsyncioTestCase):
    def test_supported_codes_and_aliases(self):
        self.assertEqual(len(app.SPOKEN_LANGUAGES), 11)
        for code in app.SPOKEN_LANGUAGES:
            self.assertEqual(app.spoken_language(code), code)
        self.assertEqual(app.spoken_language("or-IN"), "od-IN")
        self.assertEqual(app.spoken_language("HI_in"), "hi-IN")
        self.assertEqual(app.spoken_language("ta"), "ta-IN")
        for code in (None, "auto", "unknown", "ur-IN", "fr-FR"):
            self.assertIsNone(app.spoken_language(code))

    async def check_session(self, detected, expected):
        device = AsyncMock()
        # The legacy device's English hint must not override detection.
        device.receive_json.return_value = {"type": "start", "language": "en-IN"}
        device.receive.side_effect = [{"bytes": b"\0" * 3200}, {"text": '{"type":"end"}'}]
        answer = AsyncMock(return_value="Test reply")
        tts = AsyncMock()
        transcribe = AsyncMock(return_value=("Test question", detected))
        with patch.object(app, "require_env", return_value="test"), \
             patch.object(app, "transcribe_question", new=transcribe), \
             patch.object(app, "ask_sarvam", new=answer), \
             patch.object(app, "stream_tts_to_device", new=tts):
            await asyncio.wait_for(app.voice_ws(device, "test"), 1)
        transcribe.assert_awaited_once_with(b"\0" * 3200)
        self.assertEqual(tts.call_args.args[2], expected or "en-IN")
        if expected:
            answer.assert_awaited_once_with("Test question", expected)
        else:
            answer.assert_not_awaited()
            self.assertIn("Sorry", tts.call_args.args[1])
        events = [call.args[0] for call in device.send_json.call_args_list]
        self.assertEqual([event["type"] for event in events], ["transcript", "answer", "done"])
        self.assertEqual(events[0]["language"], detected)

    async def test_all_supported_languages_reach_matching_voice(self):
        for code in app.SPOKEN_LANGUAGES:
            with self.subTest(language=code):
                await self.check_session(code, code)
        await self.check_session("or-IN", "od-IN")

    async def test_unsupported_or_missing_language_has_spoken_explanation(self):
        await self.check_session("ur-IN", None)
        await self.check_session(None, None)

    async def test_transcription_preserves_language_and_uses_auto(self):
        response = MagicMock()
        response.json.return_value = {"transcript": " नमस्ते ", "language_code": "hi-IN"}
        client = AsyncMock()
        client.post.return_value = response
        with patch.object(app, "require_env", return_value="test"), patch.object(app.httpx, "AsyncClient") as factory:
            factory.return_value.__aenter__.return_value = client
            self.assertEqual(await app.transcribe_question(b"\0\0"), ("नमस्ते", "hi-IN"))
        data = client.post.call_args.kwargs["data"]
        self.assertEqual(data["language_code"], "unknown")
        self.assertEqual(data["mode"], "transcribe")

    async def test_audio_reassembly_and_limits(self):
        device = AsyncMock()
        device.receive.side_effect = [{"bytes": b"\1\2" * 2000}, {"bytes": b"\3\4"}, {"text": '{"type":"end"}'}]
        self.assertEqual(await app.receive_question(device), b"\1\2" * 2000 + b"\3\4")
        for messages, error in [
            ([{"bytes": b"1"}], "complete 16-bit"),
            ([{"bytes": b"\0" * (app.MAX_INPUT_BYTES + 2)}], "15-second"),
            ([{"text": '{"type":"end"}'}], "No microphone"),
        ]:
            device.receive.side_effect = messages
            with self.assertRaisesRegex(ValueError, error):
                await app.receive_question(device)


if __name__ == "__main__":
    unittest.main()
