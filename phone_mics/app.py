"""Phone UI + microphone WebSockets + authenticated IoT speaker WebSocket."""
import asyncio
from contextlib import asynccontextmanager, suppress
import hmac
import os
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from .detector import JarvisDetector
from .hub import Hub, Microphone, Speaker
from .voice import speaker_for


async def sarvam_reply(audio, output, memory=None):
    from realtime_relay import app as relay
    text, detected = await relay.transcribe_question(audio)
    language = relay.spoken_language(detected)
    voice = memory.voice if memory else 'male'
    if memory and language:
        try:
            result = await memory.respond(text, language)
            answer, language = result['answer'], result['language']
            voice = result['voice']
        except Exception:
            # A failed write must never be announced as a successful save.
            answer, language = "Sorry, memory is unavailable. I could not verify that request. Please try again.", "en-IN"
    else:
        answer = (await relay.ask_sarvam(text, language)) if language else (
            "Sorry, I could not identify a supported language. Please ask again.")

    class Sink:
        async def send_bytes(self, chunk):
            await output(chunk)

    await relay.stream_tts_to_device(Sink(), answer, language or "en-IN", speaker=speaker_for(voice))


def create_app(detector_factory=None, reply=None, mic_token=None, speaker_token=None, memory=None):
    mic_token = mic_token if mic_token is not None else os.getenv("JARVIS_MIC_TOKEN", "")
    speaker_token = speaker_token if speaker_token is not None else os.getenv("JARVIS_SPEAKER_TOKEN", "")
    factory = detector_factory or (lambda: JarvisDetector(os.getenv("JARVIS_MODEL_DIR", ".tmp_phone_models")))
    async def voice_reply(audio, output):
        await sarvam_reply(audio, output, app.state.memory)

    hub = Hub(reply or voice_reply, threshold=float(os.getenv("JARVIS_WAKE_THRESHOLD", ".5")),
              max_mics=int(os.getenv("JARVIS_MAX_MICROPHONES", "3")),
              speech_rms=int(os.getenv("JARVIS_SPEECH_RMS", "500")))

    @asynccontextmanager
    async def lifespan(app):
        if not mic_token or not speaker_token or mic_token == speaker_token:
            raise RuntimeError("Set distinct JARVIS_MIC_TOKEN and JARVIS_SPEAKER_TOKEN values")
        store = None
        try:
            if app.state.memory is None and os.getenv('JARVIS_MEMORY_ENABLED') == '1':
                from .memory_store import CosmosMemoryStore
                from .memory_embeddings import Embedder
                from .memory_llm import SarvamMemoryModel
                from .memory_service import MemoryService
                store = CosmosMemoryStore()
                await store.ready()
                embedder = await asyncio.to_thread(Embedder)
                app.state.memory = MemoryService(store, embedder, SarvamMemoryModel())
                await app.state.memory.get_mode()
                await app.state.memory.get_voice()
            yield
        finally:
            await hub.close()
            if store:
                await store.close()

    app = FastAPI(lifespan=lifespan)
    app.state.hub = hub
    app.state.memory = memory
    memory_http_lock = asyncio.Lock()
    static = Path(__file__).parent / "static"

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        # Revalidate UI/modules on reload so phones pick up microphone fixes.
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        response.headers["Permissions-Policy"] = "microphone=(self), camera=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            "img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'self'")
        return response

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "jarvis-phone-mics", "version": "7",
                "memory": app.state.memory is not None}

    def authorized_memory(request):
        token = request.headers.get('authorization', '')
        if not mic_token or not hmac.compare_digest(token, 'Bearer ' + mic_token):
            raise HTTPException(401, 'Microphone access key required')
        if app.state.memory is None:
            raise HTTPException(503, 'Memory is not enabled')
        return app.state.memory

    @app.get('/api/mode')
    async def mode_get(request: Request):
        service = authorized_memory(request)
        try:
            return {'mode': await service.get_mode(), 'available': ['normal', 'genz'], 'scope': 'shared household'}
        except Exception:
            raise HTTPException(503, 'Mode could not be loaded') from None

    @app.get('/api/voice')
    async def voice_get(request: Request):
        service = authorized_memory(request)
        try:
            return {'voice': await service.get_voice(), 'available': ['male', 'female'], 'scope': 'shared household'}
        except Exception:
            raise HTTPException(503, 'Voice could not be loaded') from None

    @app.put('/api/voice')
    async def voice_set(request: Request):
        service = authorized_memory(request)
        if memory_http_lock.locked() or service.lock.locked():
            raise HTTPException(429, 'Jarvis is busy; try again shortly')
        async with memory_http_lock:
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 1024:
                    raise HTTPException(413, 'Request too large')
            try:
                import json
                data = json.loads(body)
                if not isinstance(data, dict) or set(data) != {'voice'}:
                    raise ValueError('Supply only voice')
                return {'voice': await service.set_voice(data['voice']), 'scope': 'shared household'}
            except (ValueError, TypeError):
                raise HTTPException(400, 'Choose male or female') from None
            except Exception:
                raise HTTPException(503, 'Voice change could not be verified; refresh before retrying') from None

    @app.put('/api/mode')
    async def mode_set(request: Request):
        service = authorized_memory(request)
        if memory_http_lock.locked() or service.lock.locked():
            raise HTTPException(429, 'Jarvis is busy; try again shortly')
        async with memory_http_lock:
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 1024:
                    raise HTTPException(413, 'Request too large')
            try:
                import json
                data = json.loads(body)
                if not isinstance(data, dict) or set(data) != {'mode'}:
                    raise ValueError('Supply only mode')
                return {'mode': await service.set_mode(data['mode']), 'scope': 'shared household'}
            except (ValueError, TypeError):
                raise HTTPException(400, 'Choose normal or genz') from None
            except Exception:
                raise HTTPException(503, 'Mode change could not be verified; refresh before retrying') from None

    @app.get('/api/memory')
    async def memory_list(request: Request):
        service = authorized_memory(request)
        try:
            return {'scope': 'shared household', 'facts': await service.list()}
        except Exception:
            raise HTTPException(503, 'Memory is unavailable') from None

    @app.post('/api/memory/command')
    async def memory_command(request: Request):
        service = authorized_memory(request)
        if memory_http_lock.locked() or service.lock.locked():
            raise HTTPException(429, 'Jarvis is handling another memory request; try again shortly')
        async with memory_http_lock:
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 8192:
                    raise HTTPException(413, 'Request too large')
            try:
                import json
                data = json.loads(body)
                if not isinstance(data, dict):
                    raise ValueError('Expected an object')
                return await service.respond(data.get('text'), data.get('language'))
            except (ValueError, TypeError):
                raise HTTPException(400, 'Invalid memory request') from None
            except Exception:
                raise HTTPException(503, 'Request could not be verified. Refresh memory before retrying.') from None

    @app.delete('/api/memory/{identifier}')
    async def memory_delete(identifier: str, request: Request):
        service = authorized_memory(request)
        etag = request.headers.get('if-match')
        if not etag or len(identifier) != 64:
            raise HTTPException(400, 'Exact memory ID and If-Match required')
        try:
            await service.delete_one(identifier, etag)
            return {'deleted': True}
        except ValueError:
            raise HTTPException(409, 'Memory changed or no longer exists; refresh first') from None
        except Exception:
            raise HTTPException(503, 'Deletion could not be verified; refresh memory') from None

    @app.get("/")
    async def index():
        return FileResponse(static / "index.html")

    @app.websocket("/ws/mic")
    async def mic_socket(ws: WebSocket):
        origin = ws.headers.get("origin")
        allowed = set(filter(None, os.getenv("JARVIS_ALLOWED_ORIGINS", "").split(",")))
        # Native clients may omit Origin; browsers must be same-origin or explicitly allowed.
        if origin and origin not in allowed and urlsplit(origin).netloc != ws.headers.get("host"):
            await ws.close(1008)
            return
        await ws.accept()
        peer = None
        try:
            hello = await asyncio.wait_for(ws.receive_json(), 5)
            token = hello.get("token", "")
            if not isinstance(token, str) or not mic_token or not hmac.compare_digest(token, mic_token):
                await ws.close(1008)
                return
            name = hello.get("name", "Android microphone")
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40:
                raise ValueError("Use a device name of 1–40 characters")
            # Reserve a slot before the model load yields; connections cannot race the limit.
            peer = Microphone(ws, None, name.strip())
            await hub.add(peer)
            peer.detector = await asyncio.to_thread(factory)
            await peer.send({"type": "ready", "protocol":4, "capabilities":["distance-test", "audio-metrics"]})
            while True:
                message = await asyncio.wait_for(ws.receive(), 20)
                if message["type"] == "websocket.disconnect":
                    break
                if message.get("bytes") is not None:
                    await hub.packet(peer, message["bytes"])
                elif message.get("text") == "ping":
                    await peer.send({"type": "pong", "metrics":hub.metrics(peer)})
                elif message.get("text") in ("test:1", "test:3", "test:5"):
                    await hub.start_test(peer, int(message["text"][-1]))
                elif message.get("text") == "test:cancel":
                    if hub.phase == "testing" and hub.owner == peer.id:
                        await hub.close()
                else:
                    raise ValueError("Unsupported microphone message")
        except (WebSocketDisconnect, asyncio.TimeoutError):
            pass
        except Exception:
            with suppress(Exception):
                await ws.send_json({"type": "notice", "message": "Connection rejected, full, or model unavailable. Check the hub."})
                await ws.close(1008)
        finally:
            if peer and peer.id in hub.mics:
                await hub.remove(peer)

    @app.websocket("/ws/speaker")
    async def speaker_socket(ws: WebSocket):
        token = ws.headers.get("x-jarvis-speaker-key", "")
        if not speaker_token or not hmac.compare_digest(token, speaker_token) or hub.speaker:
            await ws.close(1008)
            return
        speaker = Speaker(ws)
        hub.speaker = speaker  # reserve before await
        try:
            await ws.accept()
            await hub.broadcast()
            while True:
                event = await asyncio.wait_for(ws.receive_json(), 35)
                if event.get("type") == "ping":
                    await speaker.send({"type": "pong"})
                else:
                    await hub.speaker_event(event)
        except (WebSocketDisconnect, asyncio.TimeoutError, ValueError):
            pass
        finally:
            if hub.speaker is speaker:
                hub.speaker = None
                await hub.close()
                await hub.broadcast()

    app.mount("/static", StaticFiles(directory=static), name="static")
    return app


app = create_app()
