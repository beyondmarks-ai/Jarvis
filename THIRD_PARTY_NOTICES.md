# Third-party notices

## Hey Jarvis wake model

`hey_jarvis.tflite` and `hey_jarvis.json` originate from the [ESPHome micro_wake_word model collection](https://github.com/esphome/micro-wake-word-models). The manifest credits Kevin Ahrendt. The collection's terms cover the model weights and manifests under Apache License 2.0.

The copied model SHA-256 is `21a7976add39ee24ec96c63d96b7aaa18e24d1d9824b963e451da8feb4b78b77`.

The current manifest's 0.90 confidence cutoff and three-frame window are the existing project's settings. They are preserved in this baseline and may differ from current upstream defaults.

See [license](licenses/micro-wake-word-LICENSE.txt) and [upstream notice](licenses/micro-wake-word-NOTICE.txt). Wake-word names confer no trademark rights or endorsement.

## OLED drawing

The existing firmware YAML identifies its drawing code as an ESP-IDF port of [FluxGarage RoboEyes](https://github.com/FluxGarage/RoboEyes) geometry. Upstream is licensed under GNU GPL version 3; its license is retained in [RoboEyes-LICENSE.txt](licenses/RoboEyes-LICENSE.txt). The port/integration differs from the upstream library. Review GPL obligations before publicly redistributing derivative code or firmware.

## Dependencies and user code

ESPHome, ESP-IDF, Python dependencies and FFmpeg retain their respective licenses; they are referenced or installed, not vendored into this source backup. No new blanket license is assigned to the user's own code by this preparation.
