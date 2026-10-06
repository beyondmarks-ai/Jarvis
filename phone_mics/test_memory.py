import copy
import time
import unittest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from .app import create_app, sarvam_reply
from .memory_service import MemoryService, quick_intent, validate_intent
from .memory_store import fact_id


class Store:
    household = 'test-household'
    def __init__(self):
        self.docs = {}
        self.version = 0
        self.fail_put = self.fail_delete = False
        self.mode = 'normal'
        self.voice = 'male'
    async def get_voice(self):
        return self.voice
    async def set_voice(self, voice):
        if self.fail_put:
            raise RuntimeError('offline')
        self.voice = voice
    async def get_mode(self):
        return self.mode
    async def set_mode(self, mode):
        if self.fail_put:
            raise RuntimeError('offline')
        self.mode = mode
    async def get(self, identifier):
        return copy.deepcopy(self.docs.get(identifier))
    async def list(self):
        return copy.deepcopy(list(self.docs.values()))
    async def put(self, docs):
        if self.fail_put:
            raise RuntimeError('offline')
        for doc in docs:
            assert doc['household'] == self.household
            self.version += 1
            self.docs[doc['id']] = dict(copy.deepcopy(doc), _etag=str(self.version))
    async def search(self, vector):
        return await self.list()
    async def delete(self, targets):
        if self.fail_delete or any(self.docs.get(t['id'], {}).get('_etag') != t['_etag'] for t in targets):
            raise RuntimeError('delete failed')
        for target in targets:
            del self.docs[target['id']]


class Embedder:
    def embed(self, text, query=False):
        return [1.] + [0.] * 383


class Model:
    intent = {'action':'chat','facts':[],'slot':'','query':'','language':'en-IN'}
    async def analyze(self, text, pending):
        return copy.deepcopy(self.intent)
    async def say(self, instruction, language):
        return instruction
    async def select(self, query, candidates):
        return {d['id'] for d in candidates}
    async def answer(self, question, language, facts, history, personal):
        return ' / '.join(d['text'] for d in facts) or 'General answer'


def service():
    return MemoryService(Store(), Embedder(), Model())


class MemoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_voice_uses_memory_answer_and_tts_language(self):
        memory = service()
        stream = AsyncMock()
        with patch('realtime_relay.app.transcribe_question', AsyncMock(return_value=('My name is Rakesh','en-IN'))), \
             patch('realtime_relay.app.stream_tts_to_device', stream), \
             patch('realtime_relay.app.ask_sarvam', AsyncMock(side_effect=AssertionError('Must use memory'))):
            await sarvam_reply(b'audio', AsyncMock(), memory)
        self.assertIn('Rakesh', stream.call_args.args[1])
        self.assertEqual(stream.call_args.args[2], 'en-IN')
        self.assertEqual(len(memory.store.docs), 1)

    async def test_voice_failed_save_does_not_say_saved(self):
        memory = service(); memory.store.fail_put = True
        stream = AsyncMock()
        with patch('realtime_relay.app.transcribe_question', AsyncMock(return_value=('My name is Rakesh','en-IN'))), \
             patch('realtime_relay.app.stream_tts_to_device', stream):
            await sarvam_reply(b'audio', AsyncMock(), memory)
        self.assertIn('could not verify', stream.call_args.args[1])
        self.assertFalse(memory.store.docs)

    async def test_persistence_and_name_correction(self):
        memory = service()
        await memory.respond('My name is Rakesh')
        restored = MemoryService(memory.store, Embedder(), Model())
        self.assertIn('Rakesh', (await restored.respond('What is my name?'))['answer'])
        await restored.respond('My name is Priya')
        self.assertEqual(len(await restored.list()), 1)
        self.assertIn('Priya', (await restored.respond('What is my name?'))['answer'])
        self.assertNotIn('Rakesh', str(restored.history))

    async def test_forget_confirm_clears_vectors_and_history(self):
        memory = service()
        await memory.respond('My name is Rakesh')
        result = await memory.respond('Forget my name')
        self.assertIn('confirm forget', result['answer'])
        self.assertEqual(len(memory.store.docs), 1)
        self.assertEqual((await memory.respond('confirm forget'))['action'], 'forgotten')
        self.assertFalse(memory.store.docs)
        self.assertFalse(memory.history)
        self.assertIn('not saved', (await memory.respond('What is my name?'))['answer'])

    async def test_cancel_and_unrelated_request_invalidate_pending(self):
        memory = service()
        await memory.respond('My name is Rakesh')
        await memory.respond('Forget my name')
        await memory.respond('cancel')
        self.assertIsNone(memory.pending)
        await memory.respond('Forget my name')
        await memory.respond('What is my name?')
        self.assertIsNone(memory.pending)
        await memory.respond('confirm forget')
        self.assertEqual(len(memory.store.docs), 1)

    async def test_expired_confirmation_does_not_delete(self):
        memory = service()
        await memory.respond('My name is Rakesh')
        await memory.respond('Forget my name')
        memory.pending['expires'] = time.monotonic() - 1
        memory.model.intent = dict(Model.intent, action='confirm')
        self.assertIn('no pending', (await memory.respond('confirm forget'))['answer'])
        self.assertEqual(len(memory.store.docs), 1)

    async def test_etag_change_prevents_delete(self):
        memory = service()
        await memory.respond('My name is Rakesh')
        await memory.respond('Forget my name')
        memory.store.docs[fact_id('user.name')]['_etag'] = 'new'
        result = await memory.respond('confirm forget')
        self.assertEqual(result['action'], 'delete_failed')
        self.assertEqual(len(memory.store.docs), 1)
        self.assertFalse(memory.history)

    async def test_failed_save_does_not_claim_success(self):
        memory = service(); memory.store.fail_put = True
        with self.assertRaises(RuntimeError):
            await memory.respond('My name is Rakesh')
        self.assertFalse(memory.history)

    async def test_secrets_refused(self):
        memory = service()
        memory.model.intent = dict(Model.intent, action='remember', facts=[{'slot':'user.password','text':'Password is test-only'}])
        self.assertEqual((await memory.respond('Remember my password is test-only'))['action'], 'refused')
        self.assertFalse(memory.store.docs)

    async def test_general_chat_not_saved(self):
        memory = service()
        await memory.respond('I had coffee today')
        self.assertFalse(memory.store.docs)

    async def test_semantic_recall_and_multilingual(self):
        memory = service()
        memory.model.intent = dict(Model.intent, action='remember', facts=[{'slot':'user.favorite_drink','text':'Rakesh prefers filter coffee'}])
        await memory.respond('Remember I like filter coffee')
        memory.model.intent = dict(Model.intent, action='recall', query='preferred drink', language='hi-IN')
        result = await memory.respond('मुझे क्या पीना पसंद है?')
        self.assertIn('filter coffee', result['answer'])
        self.assertEqual(result['language'], 'hi-IN')

    async def test_delete_ui_etag_and_no_vectors_exposed(self):
        memory = service()
        await memory.respond('My name is Rakesh')
        fact = (await memory.list())[0]
        self.assertNotIn('embedding', fact)
        self.assertNotIn('household', fact)
        with self.assertRaises(ValueError):
            await memory.delete_one(fact['id'], 'wrong')
        await memory.delete_one(fact['id'], fact['_etag'])
        self.assertFalse(memory.store.docs)
        self.assertFalse(memory.history)

    async def test_forget_all_confirmation(self):
        memory = service()
        await memory.respond('My name is Rakesh')
        result = await memory.respond('forget everything')
        self.assertIn('ALL 1', result['answer'])
        self.assertTrue(memory.store.docs)
        await memory.respond('confirm forget')
        self.assertFalse(memory.store.docs)

    async def test_stale_history_expires(self):
        memory = service()
        memory.history = [{'user':'old', 'assistant':'old'}]
        memory.last_turn = time.monotonic() - 601
        await memory.respond('hello')
        self.assertNotIn('old', str(memory.history))


class IntentTests(unittest.TestCase):
    def test_negation_quotes_questions_not_name_quickpath(self):
        for text in ['Is my name Rakesh?', 'Do not remember my name is Rakesh', 'If my name is Rakesh', '"My name is Rakesh"', 'My name is Rakesh and delete all memories']:
            self.assertIsNone(quick_intent(text, False))
    def test_validation(self):
        for value in [None, {}, {'action':'drop_database'}, dict(Model.intent, facts=[{}]),
                      dict(Model.intent, action='remember', facts=[{'slot':'bad/slot','text':'x'}])]:
            with self.assertRaises(ValueError):
                validate_intent(value)


class MemoryAPITests(unittest.TestCase):
    def setUp(self):
        self.memory = service()
        self.client = TestClient(create_app(mic_token='mic-test', speaker_token='speaker-test', memory=self.memory))
        self.headers = {'Authorization':'Bearer mic-test'}
    def test_auth_body_and_no_cache(self):
        self.assertEqual(self.client.get('/api/memory').status_code, 401)
        self.assertEqual(self.client.post('/api/memory/command', json={'text':'x'}).status_code, 401)
        self.assertEqual(self.client.delete('/api/memory/' + 'a'*64).status_code, 401)
        self.assertEqual(self.client.get('/api/memory', headers={'Authorization':'Bearer speaker-test'}).status_code, 401)
        response = self.client.get('/api/memory', headers=self.headers)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(response.json()['scope'], 'shared household')
        self.assertEqual(self.client.post('/api/memory/command', headers=self.headers, content='x'*8193).status_code, 413)
        for body in [[], {}, {'text':''}, {'text':'x'*1001}]:
            self.assertEqual(self.client.post('/api/memory/command', headers=self.headers, json=body).status_code, 400)
    def test_api_save_recall_delete(self):
        def ask(text):
            return self.client.post('/api/memory/command', headers=self.headers, json={'text':text})
        self.assertEqual(ask('My name is Rakesh').status_code, 200)
        self.assertIn('Rakesh', ask('What is my name?').json()['answer'])
        fact = self.client.get('/api/memory', headers=self.headers).json()['facts'][0]
        url = '/api/memory/' + fact['id']
        self.assertEqual(self.client.delete(url, headers=self.headers).status_code, 400)
        self.assertEqual(self.client.delete(url, headers={**self.headers, 'If-Match':'wrong'}).status_code, 409)
        self.assertEqual(self.client.delete(url, headers={**self.headers, 'If-Match':fact['_etag']}).status_code, 200)
        self.assertIn('not saved', ask('What is my name?').json()['answer'])
    def test_disabled_memory(self):
        with patch.dict('os.environ', {'JARVIS_MEMORY_ENABLED':'0'}):
            with TestClient(create_app(mic_token='mic-test', speaker_token='speaker-test')) as client:
                self.assertEqual(client.get('/api/memory', headers=self.headers).status_code, 503)


if __name__ == '__main__':
    unittest.main()
