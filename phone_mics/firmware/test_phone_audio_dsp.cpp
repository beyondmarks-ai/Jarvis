#include "phone_audio_dsp.h"
#include <algorithm>
#include <cassert>
#include <cmath>
#include <iostream>
#include <vector>

int main() {
  assert(PhoneAudioDsp::decode(0xff) == 0);
  assert(PhoneAudioDsp::decode(0x7f) == 0);
  assert(PhoneAudioDsp::decode(0x80) == 32124);
  assert(PhoneAudioDsp::decode(0x00) == -32124);
  int32_t previous = 0;
  for (int32_t sample = 0; sample <= 32767; ++sample) {
    const int32_t value = PhoneAudioDsp::limit(sample);
    assert(value >= previous && value < 28000);
    assert(PhoneAudioDsp::limit(-sample) == -value);
    if (sample <= 6000) assert(value == sample * 3);
    assert(value - previous <= 3);
    previous = value;
  }
  std::vector<uint8_t> input;
  for (int repeat = 0; repeat < 8; ++repeat)
    for (int byte = 0; byte < 256; ++byte) input.push_back(byte);
  std::vector<int32_t> whole(input.size()*2), chunked(whole.size());
  PhoneAudioDsp a, b;
  a.render(input.data(), input.size(), whole.data());
  size_t offset = 0;
  while (offset < input.size()) {
    const size_t count = std::min<size_t>((offset % 73) + 1, input.size() - offset);
    b.render(input.data()+offset, count, chunked.data()+2*offset);
    offset += count;
  }
  assert(whole == chunked); // No packet-boundary jumps or reset gain envelopes.
  assert(a.softened == b.softened && a.input_peak == 32124);
  for (int32_t value : whole) assert(std::abs(static_cast<int64_t>(value)) < 28000LL * 65536);
  const uint8_t loud[2] = {0x80, 0x80};
  int32_t resumed[4];
  b.restart(); b.render(loud, 2, resumed);
  assert(resumed[0] > 0 && resumed[1] < PhoneAudioDsp::limit(32124) * 65536);
  std::cout << "PASS all mu-law codes, limiter bounds/symmetry/knee, chunk continuity, restart fade\n";
}
