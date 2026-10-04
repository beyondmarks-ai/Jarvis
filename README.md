# Jarvis ESP32 voice assistant

Source backup of the working ESP32-S3 assistant, prepared **before wake-word sensitivity tuning**. Nothing in this repository preparation changes the running device or cloud deployment.

Say **Hey Jarvis**, wait for the beep, then ask your question. The relay detects the question's language and answers in English, Hindi, Bengali, Tamil, Telugu, Gujarati, Kannada, Malayalam, Marathi, Punjabi or Odia. Speaking pace is 1.0; speaker gain is 3x with peak limiting.

## What is backed up

- `jarvis_voice.yaml`, `jarvis_cloud.h`, `jarvis_speaker.h`: ESPHome firmware configuration and custom audio/network code.
- `hey_jarvis.json`, `hey_jarvis.tflite`: the current local wake-word model, **0.90 cutoff / 3-frame window**, unchanged.
- `realtime_relay/`: current multilingual Sarvam relay, Dockerfile and offline tests.
- Build, upload, serial-monitor and optional live diagnostic scripts.
- [Working baseline](docs/WORKING_BASELINE.md), [recovery guide](docs/RECOVERY.md) and third-party notices.

**This is a source backup, not a complete device image or credentials backup.** Wi-Fi credentials, API keys, recordings, compiled firmware and full flash dumps are deliberately excluded. Keep a separate encrypted backup of your private settings and existing full flash dump. Older experimental firmware and the inactive Azure Function backend are not included.

## Local setup (Windows PowerShell)

Install Git, Python 3.13, FFmpeg and your board's USB driver. ESPHome installs its compiler dependencies on the first build; that can take several minutes.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item secrets.example.yaml secrets.yaml
```

Edit `secrets.yaml` privately. Use 2.4 GHz Wi-Fi, the relay hostname (without `https://`) and a random device key matching `JARVIS_DEVICE_KEY` on the relay. The `jarvis_backend_host` substitution is retained for legacy compatibility but is unused by the current voice path.

Validate without flashing:

```powershell
.\scripts\esphome.ps1 -Action config
.\.venv\Scripts\python.exe -m unittest discover -s realtime_relay -p test_app.py -v
```

Build and upload only when you intentionally want to replace the installed firmware:

```powershell
.\compile_clean.ps1
# Run the next command only after a successful build and after checking the port.
.\upload_clean.ps1 -Port COM8
.\logs_clean.ps1 -Port COM8
```

Alternatively set `JARVIS_ESPHOME` to an existing ESPHome executable. The scripts use paths relative to this checkout; they do not depend on the original PC username or directory. Close serial monitors before uploading. `monitor_device.ps1 -Port COM8 -Seconds 45` only reads serial output.

## Cloud relay

The ESP32 needs a publicly reachable **HTTPS/WSS** service with `/ws` on port 443. The container listens on port 8000; a platform such as Azure Container Apps must terminate TLS and forward WebSocket connections. `/health` reports the deployed version.

```powershell
docker build -t jarvis-relay ./realtime_relay
```

Configure `SARVAM_API_KEY` and `JARVIS_DEVICE_KEY` as runtime secrets, using `realtime_relay/.env.example` as a template. Do not place real credentials in the image, source, command history or GitHub workflow. The app reads process environment variables; it does **not** load `.env` files itself. Local HTTP testing alone does not make a TLS endpoint available to the ESP32.

Device audio uploads while the user speaks. The relay calls Saaras v3's standard transcription API after the question ends, using automatic language detection. It then generates a short same-language answer and streams Bulbul v3 speech back. This is turn-based interaction, not full-duplex interruption. Very short or mixed-language questions can be misidentified.

## Optional live diagnostics

These tests call real providers and can incur usage charges. They are **not** part of CI. Set `SARVAM_API_KEY` and `JARVIS_DEVICE_KEY` securely in the process environment first; FFmpeg must be on PATH.

```powershell
.\.venv\Scripts\python.exe probe_multilingual.py --languages en-IN hi-IN ta-IN te-IN --verify-spoken
# Also requires your private secrets.yaml to locate/authenticate the deployed relay:
.\.venv\Scripts\python.exe probe_multilingual.py --deployed --languages hi-IN --verify-spoken
```

`probe_pipeline.py` and `probe_relay.ps1` additionally use Windows System.Speech to create test questions. `probe_provider.py` can optionally look up plaintext Azure environment values only when you explicitly set `JARVIS_AZURE_APP` and `JARVIS_AZURE_RESOURCE_GROUP` and are logged in with Azure CLI. Secret-reference deployments require supplying the process environment yourself; credentials are never printed.

## GitHub preparation

A private repository is recommended for your personal hardware project. No remote or push is configured by this preparation. After reviewing staged files, create an **empty** GitHub repository (without a generated README or license), then run:

```powershell
git status
.\.venv\Scripts\python.exe scripts/check_backup.py
git remote add origin https://github.com/YOUR_ACCOUNT/YOUR_REPOSITORY.git
git push -u origin main
git push origin backup-before-wake-tuning
```

The local baseline commit/tag allow recovery before future tuning. See [recovery](docs/RECOVERY.md). CI runs only offline relay tests and a credential/artifact guard. The guard catches common mistakes, not every possible secret; review every diff before publishing. Never use `git add -f` on ignored secrets or firmware binaries.

## Licensing

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). The wake model carries Apache-2.0 terms; the OLED drawing is identified in the existing source as a RoboEyes-derived port, whose upstream license is GPL-3.0. No new blanket license is chosen for user-authored code. Review those obligations before public redistribution.
