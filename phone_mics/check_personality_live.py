"""Opt-in mode persistence and live Sarvam style checks in an isolated partition."""
import asyncio
import uuid
from .memory_store import CosmosMemoryStore, MODE_ID
from .memory_embeddings import Embedder
from .memory_llm import SarvamMemoryModel
from .memory_service import MemoryService, validate_intent


async def main():
    partition = 'mode-test-' + uuid.uuid4().hex
    store = CosmosMemoryStore(household=partition, cli=True)
    restored = CosmosMemoryStore(household=partition, cli=True)
    model = SarvamMemoryModel()
    try:
        await store.ready()
        memory = MemoryService(store, Embedder(), model)
        assert await memory.get_mode() == 'normal'
        await memory.respond('My name is Mode Test Person')
        facts = await store.list()
        result = await memory.respond('Gen Z mode on karo')
        assert result['mode'] == 'genz' and result['language'] == 'hi-IN'
        assert await restored.get_mode() == 'genz'
        assert await store.list() == facts
        print('PASS persisted Gen Z selection, Hindi switch, unchanged shared facts', flush=True)
        for question, language in [('Explain inflation simply.', 'en-IN'), ('ब्रो, आज काम करने का मन नहीं है, क्या करूँ?', 'hi-IN')]:
            result = await memory.respond(question, language)
            assert result['answer'] and result['action'] == 'chat'
            # Synthetic prompts only: output for manual quality review, not user memory.
            print('GENZ:', result['answer'].encode('ascii', 'backslashreplace').decode(), flush=True)
        await memory.respond('Switch to Normal mode')
        assert await restored.get_mode() == 'normal'
        result = await memory.respond('Explain inflation simply.')
        print('NORMAL:', result['answer'].encode('ascii', 'backslashreplace').decode(), flush=True)
        for text in ['What is Gen Z mode?', 'Do not switch to Gen Z mode.',
                     'If I say "switch to Gen Z mode", what happens?', 'Enable angry mode.']:
            assert validate_intent(await model.analyze(text))['action'] == 'chat', text
        for text, mode in [('Please change your personality to Gen Z.', 'genz'), ('नॉर्मल मोड में वापस आ जाओ।', 'normal')]:
            intent = validate_intent(await model.analyze(text))
            assert intent['action'] == 'set_mode' and intent['mode'] == mode
        await memory.respond('forget everything')
        await memory.respond('confirm forget')
        assert await store.list() == []
        assert await store.get_mode() == 'normal'
        print('PASS real intent routing, Normal switch, memory deletion leaves setting intact', flush=True)
    finally:
        try:
            facts = await store.list()
            if facts:
                await store.delete(facts)
            setting = await store.get(MODE_ID)
            if setting:
                await store.delete([setting])
            assert not await store.list() and await store.get(MODE_ID) is None
            print('Isolated test facts and setting cleanup verified', flush=True)
        finally:
            await store.close()
            await restored.close()


if __name__ == '__main__':
    asyncio.run(main())
