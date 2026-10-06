// Compile the production DSP with clang --target=wasm32 -ffreestanding -nostdlib,
// then run test_dsp_wasm.mjs in Node. No Windows SDK or substituted DSP math needed.
#include "phone_audio_dsp.h"
#include "phone_audio_startup.h"
static PhoneAudioDsp dsp;
static uint8_t input[4096];
static int32_t output[8192];
extern "C" {
int startup_ready(int available, uint32_t elapsed, int eof) {
  return PhoneAudioStartup::ready(available, elapsed, eof != 0);
}
int32_t decode(int byte) { return PhoneAudioDsp::decode(static_cast<uint8_t>(byte)); }
int32_t limit(int sample) { return PhoneAudioDsp::limit(sample); }
void reset() { dsp = PhoneAudioDsp{}; }
void restart() { dsp.restart(); }
void render(int count) { if (count >= 0 && count <= 4096) dsp.render(input, count, output); }
uint8_t *input_pointer() { return input; }
int32_t *output_pointer() { return output; }
}
