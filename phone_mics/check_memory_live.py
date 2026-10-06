"""Opt-in real Cosmos + Sarvam checks, isolated test partition, always cleaned up.

Run: python -m phone_mics.check_memory_live
Requires JARVIS_MEMORY_ENDPOINT, SARVAM_API_KEY, Azure CLI login and data role.
Never writes to the production household partition or prints stored user data.
"""
import asyncio
import time
import uuid
from .memory_embeddings import Embedder
from .memory_llm import SarvamMemoryModel
from .memory_service import MemoryService, validate_intent
from .memory_store import CosmosMemoryStore, fact_id


async def main():
    partition = 'integration-' + uuid.uuid4().hex
    store = CosmosMemoryStore(household=partition, cli=True)
    other = CosmosMemoryStore(household=partition + '-other', cli=True)
    model, embedder = SarvamMemoryModel(), Embedder()
    try:
        await store.ready()
        memory = MemoryService(store, embedder, model)
        async def ask(text, expected):
            start = time.monotonic()
            result = await memory.respond(text)
            assert result['action'] == expected, (expected, result['action'])
            print('PASS', expected, f'{time.monotonic()-start:.2f}s', flush=True)
            return result
        await ask('My name is Memory Test Rakesh', 'remember')
        result = await ask('What is my name?', 'recall')
        assert 'Rakesh' in result['answer']
        # A new client/service must see the same persisted fact.
        restored = CosmosMemoryStore(household=partition, cli=True)
        try:
            assert (await restored.get(fact_id('user.name')))['text'].endswith('Rakesh.')
        finally:
            await restored.close()
        assert await other.get(fact_id('user.name')) is None
        assert await other.search(embedder.embed('name', True)) == []
        print('PASS restart persistence and partition isolation', flush=True)
        await ask('My name is Memory Test Priya', 'remember')
        assert len(await store.list()) == 1
        await ask('Remember that my favourite drink is filter coffee.', 'remember')
        doc = await store.get(fact_id('user.favorite_drink'))
        assert doc and 'coffee' in doc['text'].lower()
        ranked = await store.search(embedder.embed('What beverage do I prefer?', True))
        assert ranked[0]['id'] == doc['id'], 'Vector relevance ordering incorrect'
        result = await ask('मुझे कौन सा पेय पसंद है?', 'recall')
        assert result['language'] == 'hi-IN'
        assert any(word in result['answer'].lower() for word in ('coffee', 'कॉफी', 'कॉफ़ी'))
        print('PASS multilingual recall and vector ranking', flush=True)
        await ask('Forget my name', 'forget')
        before = await store.get(fact_id('user.name'))
        # A changed fact must survive confirmation of an old preview.
        await store.put([dict(before, text='The shared user name is Updated Test Name.')])
        await ask('confirm forget', 'delete_failed')
        assert await store.get(before['id'])
        await ask('Forget my name', 'forget')
        await ask('confirm forget', 'forgotten')
        assert await store.get(before['id']) is None
        assert all(d['id'] != before['id'] for d in await store.search(embedder.embed('name', True)))
        result = await ask('What is my name?', 'recall')
        assert 'Priya' not in result['answer'] and 'Rakesh' not in result['answer']
        print('PASS confirmed deletion, ETag conflict, vector removal, no stale name', flush=True)
        for text, action in [
            ('Do not remember that my favourite colour is blue.', 'chat'),
            ('Do not forget that my favourite colour is blue.', 'remember'),
            ('If I said "forget everything", would you ask me to confirm?', 'chat'),
            ('What is two plus two?', 'chat'),
            ('Remember my password is test-only-not-a-secret.', 'remember')]:
            intent = validate_intent(await model.analyze(text))
            assert intent['action'] == action, (text, intent['action'])
            if 'password' in text:
                assert intent.get('sensitive')
        print('PASS real model negation, hypothetical, general question, sensitive classification', flush=True)
        await ask('forget everything', 'forget_all')
        await ask('confirm forget', 'forgotten')
        assert await store.list() == []
    finally:
        try:
            targets = await store.list()
            if targets:
                await store.delete(targets)
            assert await store.list() == []
            print('Test partition cleanup verified', flush=True)
        finally:
            await store.close()
            await other.close()


if __name__ == '__main__':
    asyncio.run(main())
