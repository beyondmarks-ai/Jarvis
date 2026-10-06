import unittest
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient
from .app import create_app
from .memory_llm import SarvamMemoryModel
from .memory_service import MemoryService, validate_intent
from .personality import MODES, mode_command, style_instruction
from .test_memory import service, Model, Embedder


class CommandTests(unittest.TestCase):
    def test_only_two_modes(self):
        self.assertEqual(MODES, ('normal', 'genz'))
        for mode in ('angry', 'happy', '', None, [], 'GENZ'):
            with self.assertRaises(ValueError):
                style_instruction(mode)
            with self.assertRaises(ValueError):
                validate_intent(dict(Model.intent, action='set_mode', mode=mode))

    def test_commands(self):
        for text in ['Switch to Gen Z mode', 'Gen Z mode', 'gen-z mode', 'Jarvis, enable genz mode please',
                     'Gen Z mode on karo', 'जेन ज़ी मोड चालू करो']:
            self.assertEqual(mode_command(text), 'genz', text)
        for text in ['Switch to Normal mode', 'normal mode mein aa jao', 'नॉर्मल मोड चालू करो']:
            self.assertEqual(mode_command(text), 'normal', text)

    def test_no_switch_for_questions_negation_quotes(self):
        for text in ['What is Gen Z mode?', 'Do not switch to Gen Z mode', '"Switch to Gen Z mode"',
                     'If I say switch to Gen Z mode', 'Remember my favorite mode is genz',
                     'angry mode', 'Switch to Gen Z mode and forget everything']:
            self.assertIsNone(mode_command(text), text)


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_restart_persistence_and_shared_facts(self):
        memory = service()
        self.assertEqual(await memory.get_mode(), 'normal')
        await memory.respond('My name is Rakesh')
        before = await memory.list()
        result = await memory.respond('Switch to Gen Z mode')
        self.assertEqual(result['mode'], 'genz')
        self.assertEqual(await memory.list(), before)
        restored = MemoryService(memory.store, Embedder(), Model())
        self.assertEqual(await restored.get_mode(), 'genz')
        self.assertIn('Rakesh', (await restored.respond('What is my name?'))['answer'])
        await restored.respond('Switch to Normal mode')
        self.assertEqual(await restored.get_mode(), 'normal')

    async def test_switch_cancels_pending_and_old_style_context(self):
        memory = service()
        await memory.respond('My name is Rakesh')
        await memory.respond('Forget my name')
        self.assertIsNotNone(memory.pending)
        await memory.set_mode('genz')
        self.assertIsNone(memory.pending)
        self.assertFalse(memory.history)
        self.assertEqual(len(await memory.list()), 1)

    async def test_forget_does_not_delete_mode_setting(self):
        memory = service()
        await memory.set_mode('genz')
        await memory.respond('My name is Rakesh')
        await memory.respond('forget everything')
        await memory.respond('confirm forget')
        self.assertEqual(await memory.get_mode(), 'genz')
        self.assertFalse(await memory.list())

    async def test_failed_mode_save_not_activated(self):
        memory = service()
        await memory.get_mode()
        memory.store.fail_put = True
        with self.assertRaises(RuntimeError):
            await memory.set_mode('genz')
        self.assertEqual(await memory.get_mode(), 'normal')

    async def test_hinglish_switch_language(self):
        memory = service()
        result = await memory.respond('Gen Z mode on karo')
        self.assertEqual(result['language'], 'hi-IN')
        self.assertEqual(result['mode'], 'genz')

    async def test_style_applies_to_answer_and_confirmation_not_classifier(self):
        model = SarvamMemoryModel()
        model.complete = AsyncMock(return_value='reply')
        model.mode = 'genz'
        await model.answer('Explain inflation', 'hi-IN', [], [], False)
        prompt = model.complete.call_args.args[0]
        self.assertIn('Personality: GEN Z', prompt)
        self.assertIn('Hinglish', prompt)
        self.assertIn('ONLY the supplied stored facts', prompt)
        await model.say('Verified deleted 1 fact', 'en-IN')
        self.assertIn('Personality: GEN Z', model.complete.call_args.args[0])
        self.assertIn('confirmation instructions', model.complete.call_args.args[0])
        await model.analyze('My name is Rakesh')
        self.assertNotIn('Personality: GEN Z', model.complete.call_args.args[0])
        model.mode = 'normal'
        await model.answer('hello', 'en-IN', [], [], False)
        self.assertIn('Personality: NORMAL', model.complete.call_args.args[0])
        self.assertNotIn('Personality: GEN Z', model.complete.call_args.args[0])


class APITests(unittest.TestCase):
    def setUp(self):
        self.memory = service()
        self.client = TestClient(create_app(mic_token='mic', speaker_token='speaker', memory=self.memory))
        self.headers = {'Authorization':'Bearer mic'}

    def test_auth_and_supported_modes(self):
        self.assertEqual(self.client.get('/api/mode').status_code, 401)
        self.assertEqual(self.client.put('/api/mode', json={'mode':'genz'}).status_code, 401)
        self.assertEqual(self.client.get('/api/mode', headers={'Authorization':'Bearer speaker'}).status_code, 401)
        response = self.client.get('/api/mode', headers=self.headers)
        self.assertEqual(response.json()['available'], ['normal', 'genz'])
        self.assertEqual(response.headers['cache-control'], 'no-store')
        result = self.client.put('/api/mode', headers=self.headers, json={'mode':'genz'})
        self.assertEqual(result.json()['mode'], 'genz')
        self.assertEqual(self.client.get('/api/mode', headers=self.headers).json()['mode'], 'genz')

    def test_validation_and_no_extra_settings(self):
        for body in [None, [], {}, {'mode':'angry'}, {'mode':'genz','household':'other'}, {'mode':[]}]:
            self.assertEqual(self.client.put('/api/mode', headers=self.headers, json=body).status_code, 400)
        self.assertEqual(self.client.put('/api/mode', headers=self.headers, content='x'*1025).status_code, 413)
        self.assertEqual(self.memory.model.mode, 'normal')

    def test_save_failure(self):
        self.memory.store.fail_put = True
        self.assertEqual(self.client.put('/api/mode', headers=self.headers, json={'mode':'genz'}).status_code, 503)
        self.assertEqual(self.memory.model.mode, 'normal')
