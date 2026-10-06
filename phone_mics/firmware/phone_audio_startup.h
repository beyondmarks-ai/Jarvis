#pragma once
#include <stddef.h>
#include <stdint.h>

// Audio reserve and wall-clock wait are different: fast delivery can provide
// 500 ms of mu-law audio in much less than 200 ms. Never wait for a full reply.
struct PhoneAudioStartup {
  static constexpr size_t TARGET_BYTES = 6000; // 500 ms at 12 kHz mu-law.
  static constexpr uint32_t MAX_WAIT_MS = 200;
  static bool ready(size_t available, uint32_t elapsed_ms, bool eof) {
    return eof || available >= TARGET_BYTES || elapsed_ms >= MAX_WAIT_MS;
  }
};
