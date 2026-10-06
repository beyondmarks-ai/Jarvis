"""Opt-in isolated Cosmos/Sarvam voice checks; no IoT playback or household writes."""
import asyncio
import uuid
from .memory_store import CosmosMemoryStore, VOICE_ID, MODE_ID
from .memory_service import MemoryService, validate_intent
from .memory_llm import SarvamMemoryModel
from .memory_embeddings import Embedder
from .voice import speaker_for
from realtime_relay.app import stream_tts_to_device


async def main():
    partition = 'voice-test-' + uuid.uuid4().hex
    store = CosmosMemoryStore(household=partition, cli=True)
    restored = CosmosMemoryStore(household=partition, cli=True)
    model = SarvamMemoryModel()
    try:
        await store.ready()
        memory = MemoryService(store, Embedder(), model)
        await memory.respond('My name is Voice Test Person')
        facts = await store.list()
        await memory.set_mode('genz')
        result = await memory.respond('Change to a female voice')
        assert result['voice'] == 'female' and result['action'] == 'set_voice'
        assert await restored.get_voice() == 'female'
        assert await memory.get_mode() == 'genz' and await store.list() == facts
        assert (await memory.respond('Change voice'))['action'] == 'choose_voice'
        assert await memory.get_voice() == 'female'
        await memory.set_mode('normal')
        assert await memory.get_voice() == 'female'
        print('PASS durable voice choice, ambiguity, unchanged modes and facts', flush=True)
        for text, expected in [('Could you speak with a woman sounding voice?', 'set_voice'),
                               ('लड़की की आवाज़ में बोलो।', 'set_voice'),
                               ('Do not change to a female voice.', 'chat'),
                               ('What is a female voice?', 'chat'),
                               ('If I said "change to a female voice", what happens?', 'chat'),
                               ('Change voice please.', 'choose_voice')]:
            intent = validate_intent(await model.analyze(text))
            assert intent['action'] == expected, (text, intent)
            if expected == 'set_voice':
                assert intent['voice'] == 'female'
        print('PASS real model English/Hindi intent and non-command safety', flush=True)
        class Sink:
            def __init__(self):
                self.chunks = []
            async def send_bytes(self, chunk):
                self.chunks.append(chunk)
        for voice in ('male', 'female'):
            for language, text in [('en-IN', 'Hello! This is my speaking voice.'),
                                   ('hi-IN', 'क्या सीन है, ब्रो? चल, कुछ नया करते हैं।')]:
                sink = Sink()
                await stream_tts_to_device(sink, text, language, speaker=speaker_for(voice))
                audio = b''.join(sink.chunks)
                assert len(audio) > 12000 and len(set(audio)) > 20
                print('PASS actual streaming TTS', voice, language, round(len(audio)/12000, 2), 'seconds', flush=True)
        await memory.respond('Change to a male voice')
        assert await restored.get_voice() == 'male'
        await memory.respond('forget everything')
        await memory.respond('confirm forget')
        assert not await store.list() and await store.get_voice() == 'male'
    finally:
        try:
            targets = await store.list()
            for identifier in (VOICE_ID, MODE_ID):
                doc = await store.get(identifier)
                if doc:
                    targets.append(doc)
            if targets:
                await store.delete(targets)
            assert not await store.list() and await store.get(VOICE_ID) is None and await store.get(MODE_ID) is None
            print('Isolated voice test cleanup verified', flush=True)
        finally:
            await store.close()
            await restored.close()


if __name__ == '__main__':
    asyncio.run(main())
