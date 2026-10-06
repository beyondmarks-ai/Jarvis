import unittest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from .app import create_app, sarvam_reply
from .voice import VOICES, voice_command, speaker_for
from .memory_service import MemoryService, validate_intent
from .test_memory import service, Model, Embedder


class VoiceCommands(unittest.TestCase):
    def test_mapping_and_validation(self):
        self.assertEqual(VOICES, {'male':'shubh', 'female':'priya'})
        for value in ['', None, [], 'random', 'priya', 'FEMALE']:
            with self.assertRaises(ValueError):
                speaker_for(value)
            with self.assertRaises(ValueError):
                validate_intent(dict(Model.intent, action='set_voice', voice=value))
    def test_explicit_commands(self):
        for text in ['Change to a female voice', 'Switch your voice to female voice',
                     'Use a female voice', 'Female voice mein bolo', 'फीमेल वॉइस में बोलो']:
            self.assertEqual(voice_command(text), 'female', text)
        for text in ['Change to a male voice', 'set your voice to male', 'male voice karo']:
            self.assertEqual(voice_command(text), 'male', text)
    def test_ambiguous_asks_not_toggle(self):
        self.assertEqual(voice_command('change voice'), 'choose')
        self.assertEqual(voice_command('voice change karo'), 'choose')
    def test_negation_quotes_and_questions_do_not_switch(self):
        for text in ['Do not change to a female voice', 'What is a female voice?',
                     '"Change to a female voice"', 'If I said change voice',
                     'Remember that my voice is male', 'Female voice and Gen Z mode']:
            self.assertIsNone(voice_command(text), text)


class VoiceService(unittest.IsolatedAsyncioTestCase):
    async def test_persistence_and_independence(self):
        memory = service()
        self.assertEqual(await memory.get_voice(), 'male')
        await memory.respond('My name is Rakesh')
        await memory.set_mode('genz')
        facts = await memory.list()
        result = await memory.respond('Change to a female voice')
        self.assertEqual(result['voice'], 'female')
        self.assertEqual(result['action'], 'set_voice')
        self.assertEqual(await memory.get_mode(), 'genz')
        self.assertEqual(await memory.list(), facts)
        restored = MemoryService(memory.store, Embedder(), Model())
        self.assertEqual(await restored.get_voice(), 'female')
        await restored.set_mode('normal')
        self.assertEqual(await restored.get_voice(), 'female')
        self.assertIn('Rakesh', (await restored.respond('What is my name?'))['answer'])
    async def test_ambiguity_and_hinglish(self):
        memory = service()
        result = await memory.respond('change voice')
        self.assertEqual(result['action'], 'choose_voice')
        self.assertEqual(result['voice'], 'male')
        self.assertIn('male or female', result['answer'])
        result = await memory.respond('Female voice mein bolo')
        self.assertEqual(result['language'], 'hi-IN')
        self.assertEqual(result['voice'], 'female')
    async def test_failed_save_and_deletion_context(self):
        memory = service()
        await memory.respond('My name is Rakesh')
        await memory.respond('Forget my name')
        await memory.set_voice('female')
        self.assertIsNone(memory.pending)
        self.assertFalse(memory.history)
        memory.store.fail_put = True
        with self.assertRaises(RuntimeError):
            await memory.set_voice('male')
        self.assertEqual(await memory.get_voice(), 'female')
        memory.store.fail_put = False
        await memory.respond('forget everything')
        await memory.respond('confirm forget')
        self.assertEqual(await memory.get_voice(), 'female')
        self.assertFalse(await memory.list())
    async def test_switch_confirmation_uses_new_actual_tts_speaker(self):
        memory = service()
        stream = AsyncMock()
        with patch('realtime_relay.app.transcribe_question', AsyncMock(return_value=('Change to a female voice','en-IN'))), \
             patch('realtime_relay.app.stream_tts_to_device', stream):
            await sarvam_reply(b'audio', AsyncMock(), memory)
        self.assertEqual(stream.call_args.kwargs['speaker'], 'priya')
        self.assertIn('female', stream.call_args.args[1])
    async def test_reply_snapshot_survives_later_setting_change(self):
        memory = service()
        result = await memory.respond('Change to a female voice')
        await memory.set_voice('male')
        self.assertEqual(result['voice'], 'female')
    async def test_classic_default_is_unchanged(self):
        stream = AsyncMock()
        with patch('realtime_relay.app.transcribe_question', AsyncMock(return_value=('Hello','en-IN'))), \
             patch('realtime_relay.app.ask_sarvam', AsyncMock(return_value='Hi')), \
             patch('realtime_relay.app.stream_tts_to_device', stream):
            await sarvam_reply(b'audio', AsyncMock())
        self.assertEqual(stream.call_args.kwargs['speaker'], 'shubh')


class VoiceAPI(unittest.TestCase):
    def setUp(self):
        self.memory = service()
        self.client = TestClient(create_app(mic_token='mic', speaker_token='speaker', memory=self.memory))
        self.headers = {'Authorization':'Bearer mic'}
    def test_auth_defaults_and_update(self):
        self.assertEqual(self.client.get('/api/voice').status_code, 401)
        self.assertEqual(self.client.put('/api/voice', json={'voice':'female'}).status_code, 401)
        response = self.client.get('/api/voice', headers=self.headers)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(response.json()['available'], ['male','female'])
        self.assertEqual(response.json()['voice'], 'male')
        result = self.client.put('/api/voice', headers=self.headers, json={'voice':'female'})
        self.assertEqual(result.json()['voice'], 'female')
        self.assertEqual(self.client.get('/api/mode', headers=self.headers).json()['mode'], 'normal')
    def test_invalid_or_failed_save_does_not_change_voice(self):
        for body in [[], {}, {'voice':'other'}, {'voice':'female','mode':'genz'}]:
            self.assertEqual(self.client.put('/api/voice', headers=self.headers, json=body).status_code, 400)
        self.assertEqual(self.client.put('/api/voice', headers=self.headers, content='x'*1025).status_code, 413)
        self.memory.store.fail_put = True
        self.assertEqual(self.client.put('/api/voice', headers=self.headers, json={'voice':'female'}).status_code, 503)
        self.assertEqual(self.memory.voice, 'male')
