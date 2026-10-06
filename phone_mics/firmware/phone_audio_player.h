#pragma once
#include "esphome.h"
#include "jarvis_speaker.h"
#include "phone_audio_dsp.h"
#include "phone_audio_startup.h"
#include <atomic>
#include <esp_timer.h>
#include <freertos/semphr.h>
#include <freertos/stream_buffer.h>

// Single network producer + dedicated playback consumer. The bounded startup
// reserve is independent of queue capacity and waits at most 200 ms for data.
class PhoneAudioPlayer {
 public:
  static constexpr size_t CAPACITY = 32768;
  StreamBufferHandle_t queue = nullptr;
  SemaphoreHandle_t exited = nullptr;
  TaskHandle_t task = nullptr;
  std::atomic<bool> stop{false}, enabled{false}, eof{false}, abort{false}, done{true}, failed{false};
  std::atomic<size_t> high_water{0};
  std::atomic<uint32_t> first_packet_ms{0};
  uint32_t last_packet_ms = 0, max_packet_gap_ms = 0;

  bool init() {
    queue = xStreamBufferCreate(CAPACITY, 1);
    exited = xSemaphoreCreateBinary();
    if (!queue || !exited) return false;
    return xTaskCreate([](void *arg) { static_cast<PhoneAudioPlayer *>(arg)->run(); },
                       "phone_playback", 8192, this, 6, &task) == pdPASS;
  }
  ~PhoneAudioPlayer() {
    stop = true;
    if (task) {
      xTaskNotifyGive(task);
      // All worker waits are bounded (10ms queue / 1000ms I2S); never free live state.
      xSemaphoreTake(exited, portMAX_DELAY);
    }
    if (queue) vStreamBufferDelete(queue);
    if (exited) vSemaphoreDelete(exited);
  }
  bool begin() {
    if (!done || failed || xStreamBufferBytesAvailable(queue)) return false;
    first_packet_ms = last_packet_ms = max_packet_gap_ms = 0;
    high_water = 0;
    eof = false; abort = false; done = false;
    enabled = true;
    xTaskNotifyGive(task);
    return true;
  }
  bool enqueue(const uint8_t *data, size_t size) {
    if (!enabled || eof || abort || failed) return false;
    const uint32_t now = millis();
    if (!first_packet_ms) first_packet_ms = now;
    if (last_packet_ms) max_packet_gap_ms = std::max(max_packet_gap_ms, now - last_packet_ms);
    last_packet_ms = now;
    size_t offset = 0;
    while (offset < size && !failed && !stop) {
      const size_t sent = xStreamBufferSend(queue, data + offset, size - offset, pdMS_TO_TICKS(500));
      if (!sent) return false;
      offset += sent;
      if (offset == sent) xTaskNotifyGive(task);
      const size_t queued = xStreamBufferBytesAvailable(queue);
      if (queued > high_water) high_water = queued;
    }
    return offset == size && !failed;
  }
  bool finish() {
    eof = true;
    const uint32_t started = millis();
    // At most 2.74s queued audio, plus DMA drain. EOF bypasses startup waiting.
    while (!done && !failed && millis() - started < 5000) vTaskDelay(pdMS_TO_TICKS(5));
    return done && !failed;
  }
  bool cancel() {
    if (done) return true;
    abort = true;
    const uint32_t started = millis();
    while (!done && millis() - started < 1500) vTaskDelay(pdMS_TO_TICKS(5));
    return done;
  }

 private:
  int64_t queued_until_us = 0;
  bool write(const int32_t *samples, size_t count) {
    const int64_t before = esp_timer_get_time();
    size_t at = 0, total = count * sizeof(int32_t);
    while (at < total && !stop) {
      size_t written = 0;
      // ESP-IDF expects milliseconds here, NOT FreeRTOS ticks.
      const esp_err_t error = i2s_channel_write(jarvis_speaker_tx,
          reinterpret_cast<const uint8_t *>(samples) + at, total - at, &written, 1000);
      if (error != ESP_OK || !written) {
        ESP_LOGE("phone_audio", "I2S write failed: %s (%u/%u bytes)", esp_err_to_name(error),
                 static_cast<unsigned>(at), static_cast<unsigned>(total));
        failed = true;
        return false;
      }
      at += written;
    }
    queued_until_us = std::max(queued_until_us, before) + static_cast<int64_t>(count) * 1000000 / 24000;
    return at == total;
  }
  void tail(PhoneAudioDsp &dsp) {
    if (!dsp.previous || stop || failed) return;
    int32_t fade[48];
    for (int i = 0; i < 48; ++i) fade[i] = (dsp.previous * (47-i) / 48) * 65536;
    write(fade, 48);
    dsp.previous = 0;
  }
  void run() {
    uint8_t input[240];  // 20ms max per write; fixed allocations, no per-packet heap churn.
    int32_t output[480];
    while (!stop) {
      if (!enabled) { ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(10)); continue; }
      PhoneAudioDsp dsp;
      bool started = false;
      uint32_t first_write_ms = 0, starvations = 0;
      size_t bytes = 0, startup_bytes = 0;
      queued_until_us = 0;
      while (!stop && !failed && !abort) {
        if (!started) {
          const size_t available = xStreamBufferBytesAvailable(queue);
          if (!available) {
            if (eof) break;
            ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(10));
            continue;
          }
          // Starts once sufficient reserve arrives OR the approved 200 ms budget
          // expires, whichever comes first. Never rebuffer during the reply.
          if (!PhoneAudioStartup::ready(available, millis()-first_packet_ms.load(), eof)) {
            vTaskDelay(1);
            continue;
          }
          startup_bytes = available;
        }
        const size_t count = xStreamBufferReceive(queue, input, sizeof(input), pdMS_TO_TICKS(10));
        if (count) {
          const int64_t now = esp_timer_get_time();
          // This is an estimated DMA starvation, not microphone or model latency.
          if (started && now > queued_until_us + 2000) { ++starvations; dsp.restart(); }
          dsp.render(input, count, output);
          if (!started) { first_write_ms = millis(); jarvis_speaker_playing = true; }
          started = true;
          if (!write(output, count * 2)) break;
          bytes += count;
        } else if (eof) break;
      }
      if (abort || failed || stop) {
        while (xStreamBufferReceive(queue, input, sizeof(input), 0)) {}
      }
      if (started && !stop) {
        tail(dsp);
        vTaskDelay(pdMS_TO_TICKS(70)); // existing 60ms DMA drain, only at the END.
      }
      jarvis_speaker_playing = false;
      ESP_LOGI("phone_audio", "Played %u bytes; first_write=%ums, startup_bytes=%u, queued_peak=%u, packet_gap=%ums, gaps=%u, softened=%u, input_peak=%d",
          static_cast<unsigned>(bytes), static_cast<unsigned>(first_write_ms ? first_write_ms-first_packet_ms.load() : 0),
          static_cast<unsigned>(startup_bytes),
          static_cast<unsigned>(high_water.load()), static_cast<unsigned>(max_packet_gap_ms),
          static_cast<unsigned>(starvations), static_cast<unsigned>(dsp.softened), static_cast<int>(dsp.input_peak));
      enabled = false;
      done = true;
    }
    xSemaphoreGive(exited);
    vTaskDelete(nullptr);
  }
};
