#pragma once
#include <stddef.h>
#include <stdint.h>

// Causal, continuous soft knee: preserves the quiet-speech boost without flat clipping.
// No lookahead, packet accumulation, or change to speech timing.
struct PhoneAudioDsp {
  int32_t previous = 0;
  unsigned fade = 0;
  uint32_t softened = 0;
  int32_t input_peak = 0;

  static int32_t decode(uint8_t byte) {
    const uint8_t value = static_cast<uint8_t>(~byte);
    const int32_t sample = ((((value & 15) << 3) + 0x84) << ((value & 0x70) >> 4)) - 0x84;
    return (value & 0x80) ? -sample : sample;
  }
  static int32_t limit(int32_t sample) {
    const int32_t boosted = sample * 3;
    const int32_t magnitude = boosted < 0 ? -boosted : boosted;
    if (magnitude <= 18000) return boosted;
    const int32_t over = magnitude - 18000;
    const int32_t smooth = 18000 + static_cast<int32_t>(10000.0f * over / (10000.0f + over));
    return boosted < 0 ? -smooth : smooth;
  }
  void restart() { previous = 0; fade = 0; }
  void render(const uint8_t *input, size_t count, int32_t *output) {
    for (size_t i = 0; i < count; ++i) {
      const int32_t decoded = decode(input[i]);
      const int32_t magnitude = decoded < 0 ? -decoded : decoded;
      input_peak = magnitude > input_peak ? magnitude : input_peak;
      if (magnitude > 6000) ++softened;
      int32_t sample = limit(decoded);
      // Two-ms fade at stream start/recovery; no delayed playback or inserted silence.
      if (fade < 24) sample = sample * static_cast<int32_t>(++fade) / 24;
      // Continuous linear interpolation, including across network packet boundaries.
      output[2*i] = ((previous + sample) / 2) * 65536;
      output[2*i+1] = sample * 65536;
      previous = sample;
    }
  }
};
