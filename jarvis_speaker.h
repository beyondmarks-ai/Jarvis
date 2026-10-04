#pragma once

#include "esphome.h"
#include <driver/i2s_std.h>
#include <cmath>
#include <vector>

static i2s_chan_handle_t jarvis_speaker_tx = nullptr;
static volatile bool jarvis_speaker_playing = false;

static bool jarvis_speaker_init() {
  if (jarvis_speaker_tx != nullptr) return true;
  i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
  chan_cfg.dma_desc_num = 6;
  chan_cfg.dma_frame_num = 240;
  chan_cfg.auto_clear_after_cb = true;
  esp_err_t err = i2s_new_channel(&chan_cfg, &jarvis_speaker_tx, nullptr);
  if (err != ESP_OK) {
    ESP_LOGE("jarvis_speaker", "channel creation failed: %s", esp_err_to_name(err));
    jarvis_speaker_tx = nullptr;
    return false;
  }

  i2s_std_config_t cfg = {
    .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(24000),
    .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(
        I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_MONO),
    .gpio_cfg = {
      .mclk = I2S_GPIO_UNUSED,
      .bclk = GPIO_NUM_7,
      .ws = GPIO_NUM_4,
      .dout = GPIO_NUM_2,
      .din = I2S_GPIO_UNUSED,
      .invert_flags = {},
    },
  };
  cfg.slot_cfg.slot_mask = I2S_STD_SLOT_LEFT;
  cfg.slot_cfg.slot_bit_width = I2S_SLOT_BIT_WIDTH_32BIT;
  cfg.slot_cfg.ws_width = 32;
#ifdef I2S_HW_VERSION_2
  cfg.clk_cfg.ext_clk_freq_hz = 0;
  cfg.slot_cfg.left_align = true;
  cfg.slot_cfg.big_endian = false;
  cfg.slot_cfg.bit_order_lsb = false;
#endif
  err = i2s_channel_init_std_mode(jarvis_speaker_tx, &cfg);
  if (err == ESP_OK) err = i2s_channel_enable(jarvis_speaker_tx);
  if (err != ESP_OK) {
    ESP_LOGE("jarvis_speaker", "initialization failed: %s", esp_err_to_name(err));
    return false;
  }
  ESP_LOGI("jarvis_speaker", "Ready on BCLK=7 WS=4 DOUT=2");
  return true;
}

static void jarvis_speaker_beep() {
  if (jarvis_speaker_playing || !jarvis_speaker_init()) return;
  jarvis_speaker_playing = true;
  constexpr int sample_rate = 24000;
  constexpr int sample_count = sample_rate / 6;
  auto *samples = new std::vector<int32_t>(sample_count);
  float phase = 0.0f;
  for (int i = 0; i < sample_count; i++) {
    const float t = static_cast<float>(i) / sample_count;
    const float frequency = t < 0.5f ? 650.0f : 1000.0f;
    phase += 2.0f * 3.14159265f * frequency / sample_rate;
    const float envelope = sinf(3.14159265f * t);
    const int16_t pcm16 = static_cast<int16_t>(22000.0f * envelope * sinf(phase));
    (*samples)[i] = static_cast<int32_t>(pcm16) * 65536;
  }
  xTaskCreate([](void *arg) {
    auto *tone = static_cast<std::vector<int32_t> *>(arg);
    size_t written = 0;
    const esp_err_t err = i2s_channel_write(
        jarvis_speaker_tx, tone->data(), tone->size() * sizeof(int32_t),
        &written, pdMS_TO_TICKS(1000));
    ESP_LOGI("jarvis_speaker", "Wake beep: %u bytes, %s",
             static_cast<unsigned>(written), esp_err_to_name(err));
    delete tone;
    jarvis_speaker_playing = false;
    vTaskDelete(nullptr);
  }, "jarvis_beep", 4096, samples, 5, nullptr);
}
