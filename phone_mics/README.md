# Jarvis: three Android microphone satellites

Phone microphone -> local/hosted Python hub -> one IoT speaker.
The hub runs the existing openWakeWord **Hey Jarvis** ONNX model. Each phone has
its own model history. Audio from different phones is never mixed into one detector.
The first detected wake claims one turn; the other microphones pause for that turn.
All beeps and replies belong to the IoT, never the phone. The winning phone captures
the question too, avoiding the ESP32 microphone's far-distance limitation.

## Current status and limits

- App v4 adds private per-phone hub measurements (received frames, gaps, input
  level, clipping, model score and inference time) and network round-trip display.
  These are diagnostics, not recognition-accuracy estimates. UI/modules revalidate
  on reload to avoid leaving phones on old microphone code.
- **Distance check** is an explicit, silent 20-second test: 5 seconds of room noise,
  then three prompted 5-second Hey Jarvis attempts at the selected 1/3/5 m distance.
  Only the test owner streams; other phones pause. No beep, answer-provider request
  or recording is made. Completion, cancellation or owner disconnect releases the
  test and resumes normal listening. It also works without an IoT speaker connected.
  Results include separate attempt detections, peak model scores, audio completeness,
  noise-trigger warnings, clipping and gaps. A lingering positive score cannot count
  as multiple attempts: rearming requires 600 ms below half the current cutoff.
- Test reports can be copied explicitly and contain measurements only, not credentials
  or audio. No sensitivity or gain setting is changed by testing. Default wake cutoff
  remains 0.5; speaker volume and speech pace are unchanged. A 3/3 result in one short
  test is NOT proof of reliable five-metre operation or a long-term false-trigger rate.

- Browser app, hub, offline tests and an optional ESP32 speaker firmware are included.
- Verified locally: 12 hub/app tests, 8 diagnostic tests, 10 pairing tests, 11 existing relay tests, audio framing at
  16/44.1/48 kHz, and three concurrent browser clients using simulated microphones.
  Five existing synthetic Hey Jarvis clips triggered the pretrained detector;
  that is a smoke test, not a real-room accuracy measurement.
- On 2026-10-05 this workspace's hub was deployed as a separate Azure Container App.
  Three simulated browser microphones connected over HTTPS/WSS, including QR-link
  key autofill and removal from browser history. The original relay remains unchanged.
- The speaker firmware compiled with ESPHome 2026.8.2 / ESP-IDF 5.5.5
  (about 838 KB image, 109 KB static DIRAM) and was uploaded to the connected ESP32-S3.
  A synthetic wake/question through Azure caused a successful device beep write,
  reply playback acknowledgement, and return to listening. Human listening quality
  and physical Android microphones still require the user's real-room test.
- The original on-device-wake firmware remains available as the rollback source;
  a hash-verified factory image was saved locally in ignored `backups/` before upload.
- The Android browser app must remain visible/unlocked. Wake-lock is requested but
  may be refused by Android. Locked-screen/background reliability needs a native
  Android foreground-service app, not this browser version.
- App v3 fixes a transferable-buffer bug that previously stopped browser audio
  after the first 80 ms frame, despite the WebSocket showing connected. The next
  PCM buffer is now allocated before transferring the completed one. Tests cover
  actual buffer detachment and continuous browser AudioWorklet frames, not just
  successful WebSocket connections.
  A deployed v3 test using synthetic audio through Chrome's actual microphone
  capture/worklet path completed wake detection, IoT beep, question, spoken reply
  acknowledgement and return to listening (134 captured frames, 65 transmitted).
- The phone displays audio-context state, microphone-track state and captured/sent
  frame counts. Paused, muted, stalled or digitally silent input does not show a
  misleading listening status. Restart microphone reacquires capture on a user tap;
  returning to the foreground also attempts to resume the audio context.
- Wake detection needs no new training. Detection at 5 metres is not guaranteed.
  Put a phone near the person speaking; this is distributed microphones, not improved
  far-field acoustics on the ESP32.
- The existing Sarvam speech/answer pipeline is reused. Azure can host the hub, but
  Azure Speech is NOT integrated here. Do not confuse hosting with a model provider.

## Local setup

From the repository root, use a separate Python 3.11 environment (already created
locally as `.tmp_phone_env`):

```powershell
uv venv --python 3.11 .tmp_phone_env
uv pip install --python .tmp_phone_env/Scripts/python.exe -r phone_mics/requirements.txt
.\.tmp_phone_env\Scripts\python.exe -m phone_mics.download_models
.\.tmp_phone_env\Scripts\python.exe -m phone_mics.run_local
```

Open http://127.0.0.1:8765 on this PC for the preview. The launcher creates separate
local phone and speaker keys in ignored `.tmp_phone_access.json`; do not publish it.
Enter `JARVIS_MIC_TOKEN` in the phone app. Never give phones the speaker/provider key.
Set `SARVAM_API_KEY` in the server environment for live questions; it is not read from
the example file automatically. FFmpeg must be on PATH. Live questions incur provider usage.

### Real phones require HTTPS

Chrome permits microphone capture on localhost, but not on `http://PC-LAN-IP`.
Use a certificate trusted by every phone and the ESP32, or deploy the container
behind a public HTTPS/WSS endpoint (for example Azure Container Apps). Never disable
TLS validation to make it work. The supplied ESP32 client uses the public CA bundle,
so a self-signed development certificate is NOT sufficient for it.

For Android: open the HTTPS URL in Chrome, use Add to home screen, enter a distinct
phone name plus the shared microphone key, and allow the microphone. Repeat on up
to three phones. Keep the app open. A lost connection stops capture; reconnect explicitly.

### Optional private QR pairing

Install the optional local-only helper with `pip install qrcode==8.2`, then run:

```powershell
python -m phone_mics.pairing https://YOUR-HUB.azurecontainerapps.io
```

Open the generated ignored `.tmp_phone_setup.html` on the PC and scan its QR code
with each Android phone. The key is carried in a URL fragment (not an HTTP query)
and removed from the address bar immediately. The phone still requires a name,
an explicit Connect tap, and microphone permission. The QR/link is a credential:
share only with trusted devices; do not publish the generated setup page.

## IoT speaker firmware

`jarvis_phone_speaker.yaml` uses the same XIAO ESP32-S3 speaker pins and provides
simple animated eyes. It is a phone-speaker-only mode: no on-device wake detector
or question microphone is active. The original `jarvis_voice.yaml` remains the rollback.
The variant waits for a trusted WSS hub on port 443. Public source defaults are
placeholders and intentionally cannot connect; the deployed device uses a private wrapper.

Create a private ignored `secrets.phone.yaml` wrapper at the repository root:

```yaml
packages:
  phone_speaker: !include jarvis_phone_speaker.yaml
substitutions:
  phone_hub_host: !secret jarvis_phone_hub_host
  phone_hub_key: !secret jarvis_phone_hub_key
```

Place those two values in your private `secrets.yaml` (host without scheme/path;
key equal to the hub's `JARVIS_SPEAKER_TOKEN`, at least 24 characters). Keep existing
Wi-Fi credentials. Compile/check this wrapper before any intentionally approved upload.
No automatic flashing is performed by any phone app or hub command.

The verified Windows compile used a separate build folder and prevented the
ESP-IDF CMake scripts from discovering unrelated parent Git repositories:

```powershell
$env:GIT_CEILING_DIRECTORIES='C:/Users/rkmai;D:/jarvis'
$env:ESPHOME_BUILD_PATH='D:\jarvis\.tmp_phone_firmware_build'
# Use the ESPHome executable from your existing environment:
esphome compile jarvis_phone_speaker.yaml
```

Adjust these machine-specific paths on another PC. This command compiles only;
placeholder hub values must be replaced privately and recompiled before deployment.

## Azure/container deployment requirements

```powershell
docker build -f phone_mics/Dockerfile -t jarvis-phone-hub .
```

Configure the three runtime secrets from `.env.example`. Use HTTPS ingress with
WebSockets, port 8000, **one worker, exactly one replica**, no scale-to-zero while
listening. Session ownership is in memory; multiple replicas are NOT supported.
Start with one CPU and test memory/latency with all three phones. Continuous phone
audio is sent to that host; using Azure means wake audio leaves the local network.
No raw recordings are stored by the hub. Restarting loses active conversations.
Authorize deployment/costs and choose a hostname before changing the IoT firmware.

## Protocol and privacy

- `/ws/mic`: first JSON `{token, name}` within 5 s; server sends welcome/state/ready.
  Each binary message is little-endian uint32 state epoch followed by 1,280 int16
  samples (80 ms at 16 kHz). Old-epoch data is ignored. Text `ping` keeps idle peers alive.
- `/ws/speaker`: `X-Jarvis-Speaker-Key` upgrade header. Server sends `begin` with a
  turn ID; speaker beeps and returns `beep_done` with that same ID. Only then is the
  winning phone allowed to record the question. Replies are 12 kHz mono G.711 mu-law
  binary frames. Server sends `end`; speaker returns `played` after playback drains.
- `cancel` aborts a turn. JSON `ping`/`pong` are heartbeats. Missing acknowledgements,
  disconnects, missing speech and provider failures release ownership and resume listening.
- Phones never choose their own role or send accepted wake events: the server runs
  the detector. Frame size, audio rate, utterance duration, reply bytes and peers are bounded.
- Wake-only audio is discarded after inference; questions are forwarded to the configured
  provider. A shared mic key permits joining; use only with trusted household devices.
  This is a personal prototype, not a hardened public multi-tenant service.

## Low-latency speaker playback (bounded startup reserve)

The phone-speaker firmware now receives network audio and feeds I2S in separate
tasks. A bounded 32 KiB mu-law queue absorbs audio arriving ahead of playback; it
is **not** a prefill target. With the user's approved startup budget, playback
starts when 6,000 bytes (500 ms of audio) arrive or 200 ms has elapsed since the
first packet, whichever happens first. Fast delivery fills this reserve in less
than its audio duration. EOF bypasses the wait for short replies; cancellation
and shutdown remain interruptible. No further buffering waits are inserted during
the answer. The 200 ms deadline is a software budget, subject to scheduler jitter,
not an acoustic end-to-end latency guarantee. Fixed 20 ms work blocks replace
per-packet PCM allocations.
The existing 60 ms hardware DMA buffer and end-of-turn drain remain unchanged.
The TCP receive window is 32 KiB (previously 5,760 bytes), with a 32-entry receive
mailbox, to allow more in-flight data on higher-latency hotspot connections. These
are transport capacity limits, separate from the startup reserve.

Quiet speech retains a 3x boost, but a continuous soft-knee limiter replaces the
old hard clamp. A continuous 12-to-24 kHz interpolation and two-millisecond onset/
recovery fade reduce sharp transitions. This is causal processing, without
lookahead or slower speech. Cancellation drains queued data before another turn.

Serial `phone_audio` summaries report bytes played, first I2S-write delay from the
first received packet, bytes reserved at startup, peak queue fill, maximum packet interval, estimated DMA
starvations, softened sample count and input peak. `softened` is not a clipping
count; `gaps` estimates starvation rather than directly measuring speaker output.
Cloud `Speaker delivery` logs contain aggregate byte/chunk counts, source chunk
intervals and send-call durations only, not speech or household facts. Compare
these with device packet intervals to distinguish upstream generation stalls from
downstream delivery stalls; send completion alone does not prove device receipt.
The packet-interval metric also includes producer backpressure when the queue is
full, so it is not a pure network-jitter measurement. Delivery stalls can exceed
the startup reserve; this bounded wait cannot guarantee uninterrupted audio during
arbitrary network outages.

The real production C++ DSP can be tested without an embedded board:

```powershell
& 'C:\Program Files\LLVM\bin\clang++.exe' --target=wasm32 -ffreestanding -nostdlib -std=c++17 -O2 '-Wl,--no-entry' '-Wl,--export-all' phone_mics/firmware/test_dsp_wasm.cpp -o .tmp_audio_dsp.wasm
node phone_mics/firmware/test_dsp_wasm.mjs .tmp_audio_dsp.wasm
```

This covers startup reserve/deadline/EOF decisions, all 16-bit amplitudes, mu-law decoding, limiter bounds/symmetry/knee,
packet-boundary continuity and recovery fade. A native C++ test is also provided.
Firmware compilation and physical timing/cancellation testing are still required
when modifying the FreeRTOS playback code.

## Male and female speaking voices (v7)

Say “Change to a female voice”, “Change to a male voice”, or “Female voice mein bolo”
after the wake beep. “Change voice” alone asks which one, without guessing. You can
also use **Speaking voice** in the phone app. Male is the existing Sarvam Shubh
voice; Female is Sarvam Priya. Pace stays 1.0. This is synthesized output selection,
not speaker identification or voice cloning.

Voice selection is saved household-wide across restarts, separately from Normal/
Gen Z mode and shared memory. The switch confirmation uses the new voice. An answer
already being generated retains its own voice snapshot. Switching cancels a pending
forget confirmation and clears temporary context, but never deletes saved facts.
Forgetting all memory facts does not reset the selected voice. The default remains Male.
Visible phone panels refresh the current selection within 15 seconds.

`GET`/`PUT /api/voice` require the microphone bearer key. PUT accepts only
`{"voice":"male"}` or `{"voice":"female"}`. Controls require enabled memory storage.
Voice choice is strictly mapped to approved provider IDs, not arbitrary voice URLs.
`test_voice.py` covers parsing, persistence, independent settings, failures, TTS
routing and API validation. `check_browser_voice.py` tests selector/reload/failure
behaviour with synthetic HTTP. Optional `python -m phone_mics.check_voice_live`
uses the same credentials as other live checks, unique Cosmos test data with cleanup,
and real Sarvam English/Hindi streams for both speakers (no IoT playback).

The shared relay's TTS helper now accepts an optional keyword-only speaker argument;
existing callers still default to Shubh. No wake settings or firmware are changed.

## Normal and Gen Z modes (v6)

Exactly two personality modes are available. **Normal** is the default: clear,
friendly, straightforward replies. **Gen Z** uses casual, playful phrasing and
natural Hinglish for Hindi conversations, while keeping other supported languages.
Serious topics remain respectful; accuracy and memory confirmation rules stay intact.
Both use whichever speaking voice you select at pace 1.0. This changes wording, not an emotional
voice model, wake sensitivity, speaker volume, or microphone behaviour.

After the wake beep, say “Switch to Gen Z mode”, “Gen Z mode on karo”, or
“Switch to Normal mode”. Alternatively use the two buttons in **Jarvis personality**.
The selected mode is household-wide and saved in Cosmos DB across restarts.
Other open phones refresh their displayed mode within 15 seconds. The next reply
uses the newly selected mode; a reply already underway finishes in its previous style.
Switching cancels pending deletion confirmations and clears temporary conversation
context, but keeps all saved facts. Forgetting all memory facts leaves this app setting
alone. No automatic mood switching or additional modes are available.

Mode APIs (`GET`/`PUT /api/mode`) require the microphone bearer key; PUT accepts only
`{"mode":"normal"}` or `{"mode":"genz"}`. Mode controls require memory to be enabled.
`test_personality.py` covers strict validation, voice routing, persistence, prompt
styles, unchanged facts, deletion safety and authenticated HTTP behaviour.
`check_browser_personality.py` checks two-phone UI synchronisation with synthetic
HTTP fixtures. `python -m phone_mics.check_personality_live` optionally tests real
Cosmos and Sarvam with unique test data that is removed in `finally`; use the same
credentials and opt-in setup as the memory live check.

## Shared household memory (v5)

Optional Azure Cosmos DB memory now supports explicit remember requests, name
introductions, corrections, multilingual recall and confirmed forgetting.
The new **Household memory** panel also works using text without an online IoT
speaker. Use the existing microphone key; all phones share the same facts.
See [memory behaviour, privacy, Azure setup and tests](azure_memory/README.md).
This is not a password vault or automatic speaker identification.

## Verification

```powershell
.\.tmp_phone_env\Scripts\python.exe -m pytest phone_mics realtime_relay/test_app.py -q
node phone_mics/test_audio.mjs
```

With the local hub running, `python phone_mics/check_browser_capture.py` uses
Playwright plus installed Chrome and simulated microphone audio to verify continuous
frames, meter activity, suspension recovery, stalled processing, digital silence,
muted tracks and cleanup. Install Playwright separately in the test environment.
Failure injection bypasses CSP only in that isolated test browser, not in the app.
`python phone_mics/check_browser_quality.py` runs the silent distance-test UI check
with two simulated microphone clients, including automatic completion, cancellation,
other-phone pausing, hub diagnostics and mobile layout. `test_diagnostics.py` covers
test ownership, interruption safety, score windows, false/background detections,
audio completeness and the lingering-score regression. There are 84 Python tests
across memory, the hub, diagnostics, pairing and original speech relay.

For real-room acceptance, run at 1 m first, then 3 m and 5 m with the fan operating.
Repeat several times with the actual speakers/accents and normal room activity.
Keep other people's permission in mind when using microphones. Test unwanted wakes
over a longer quiet/background-only period separately; the five-second baseline is
only a short screening check. Improve placement or add a closer phone when results
are weak. Do not lower the wake cutoff solely because one far-away attempt was missed.

Tests cover three-way wake arbitration, owner-only question capture, one reply,
listening again after a reply, disconnect recovery, stale frames, rate/frame limits,
authentication, browser origin checks, private pairing links and sample-rate conversion.
Device beep writes and reply acknowledgements passed a live synthetic Azure test.
Three physical Android phones, audible playback quality and real-room accuracy still
need user testing. Synthetic checks do not establish reliable detection at five metres.

## Licenses

openWakeWord code: Apache-2.0. Its included pretrained models (including Hey Jarvis
and the downloaded feature models) are **CC BY-NC-SA 4.0**: attribution,
non-commercial use and share-alike obligations apply. Not automatically suitable
for a commercial product. Sources and hashes are recorded by the downloader.

- https://github.com/dscripka/openWakeWord#license
- https://creativecommons.org/licenses/by-nc-sa/4.0/

Downloaded weights and generated private access keys are ignored, not committed.
