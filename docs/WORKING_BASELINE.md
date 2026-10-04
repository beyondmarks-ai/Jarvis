# Baseline before wake-word tuning

Prepared 2026-10-04. Tag: `backup-before-wake-tuning`.

## Hardware and behavior

- Seeed XIAO ESP32-S3, 8 MB flash, octal PSRAM, ESPHome 2026.8.2 / ESP-IDF 5.5.5.
- SSD1306 128x64 OLED: SDA GPIO5, SCL GPIO6, address 0x3C.
- Microphone: BCLK GPIO44, WS GPIO9, DIN GPIO1; 16 kHz / 32-bit / left channel.
- Speaker: BCLK GPIO7, WS GPIO4, DOUT GPIO2; custom 24 kHz / 32-bit / left-slot I2S driver. Preserve these settings.
- Wake model: Hey Jarvis v2; cutoff **0.90**, sliding window **3**; not tuned during backup preparation.
- Capture ends after 400 ms silence, with a 12-second maximum and four-second no-speech timeout.
- Reply audio: 12 kHz mu-law, decoded to the existing speaker format, gain 3x and +/-30000 peak limit; 300 ms prebuffer.
- TCP send/receive windows 32768 bytes, receive mailbox 16, TCP_NODELAY, Wi-Fi power saving disabled.

## Relay baseline

- Health version: `realtime-20261004f`.
- Original deployed image digest: `sha256:4313370a9a9198eebeea6f15585074b79484dfa19a6331044f47f1d99e2e5c26`.
- Saaras v3 REST transcription, `language_code=unknown`, native transcription; Sarvam 105B conversations with 384-token allowance; Bulbul v3 streaming speech at normal pace 1.0.
- Supports 11 spoken output languages. Detected language is passed to the answer prompt and both TTS language fields; `or-IN` maps to `od-IN` for Odia.
- Real-time STT auto tests mislabeled Hindi as English; the standard transcription path correctly identified the same audio. Device upload and reply playback still stream.
- Unsupported or missing detected languages receive a spoken limitation/retry in English. No-speech/network errors are logged; comprehensive spoken error handling is not implemented.

## Evidence and limitations

- User confirmed clear volume and successful English answers before the multilingual update.
- All 11 language mappings and synthetic question/reply audio passed live provider checks. Deployed English, Hindi, Tamil and Telugu checks passed; first cloud audio was around 0.73-0.88 seconds after synthetic input ended. These are not physical-device response-time guarantees.
- Very short Telugu replies containing mostly shared words/place names were inconsistently classified by the independent audio verifier. A fuller Telugu sentence passed.
- Multilingual confirmation from the physical device remains pending. Wake detection sometimes needs repeated attempts, which is the next intended tuning task.
- This backup does not claim perfect wake detection, language recognition or Alexa-style full-duplex operation.

## Backup preparation changes only

Custom firmware headers, wake model/manifest, relay source/tests/Dockerfile and dependency pins were copied from the working project. The firmware YAML changes only its absolute model path into a checkout-relative local model reference. Helper scripts now use portable paths; diagnostics require explicit credentials or explicit Azure lookup settings. No firmware was flashed and no cloud service was changed during preparation.

Firmware binaries in an older local build folder may include unflashed diagnostics. Always rebuild from this source before restoring; do not assume an arbitrary existing binary matches the device.
