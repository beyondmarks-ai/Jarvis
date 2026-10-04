#pragma once

#include "esphome.h"
#include "jarvis_speaker.h"
#include <atomic>
#include <cJSON.h>
#include <esp_crt_bundle.h>
#include <esp_heap_caps.h>
#include <esp_random.h>
#include <esp_transport.h>
#include <esp_transport_ssl.h>
#include <esp_transport_ws.h>
#include <lwip/sockets.h>
#include <lwip/tcp.h>
#include <string>
#include <vector>

static constexpr size_t JARVIS_MIC_RATE = 16000;
static constexpr size_t JARVIS_MAX_SAMPLES = JARVIS_MIC_RATE * 12;
static constexpr size_t JARVIS_FRAME_SAMPLES = 1600;  // 100 ms / 3200 bytes.
static constexpr uint32_t JARVIS_END_SILENCE_MS = 400;
static constexpr int32_t JARVIS_SPEECH_RMS = 350;
static constexpr int32_t JARVIS_REPLY_GAIN = 3;
static int16_t *g_jarvis_samples = nullptr;  // PSRAM, allocated once, never resized.
static std::atomic<size_t> g_jarvis_sample_count{0};
static std::atomic<bool> g_jarvis_capturing{false};
static std::atomic<bool> g_jarvis_heard_speech{false};
static std::atomic<bool> g_jarvis_busy{false};
static std::atomic<uint32_t> g_jarvis_capture_end{0};
static uint32_t g_jarvis_capture_start = 0;
static uint32_t g_jarvis_last_voice = 0;

static int16_t jarvis_ulaw_to_linear(uint8_t value) {
  const uint8_t u = static_cast<uint8_t>(~value);
  int32_t sample = (((u & 0x0F) << 3) + 0x84) << ((u & 0x70) >> 4);
  sample -= 0x84;
  return static_cast<int16_t>((u & 0x80) ? -sample : sample);
}

static void jarvis_mic_callback(const std::vector<uint8_t> &data) {
  if (!g_jarvis_capturing.load(std::memory_order_acquire)) return;
  const auto *samples = reinterpret_cast<const int32_t *>(data.data());
  const size_t at = g_jarvis_sample_count.load(std::memory_order_relaxed);
  const size_t count = std::min(data.size() / sizeof(int32_t), JARVIS_MAX_SAMPLES - at);
  uint64_t squares = 0;
  for (size_t i = 0; i < count; ++i) {
    const int16_t sample = static_cast<int16_t>(samples[i] >> 16);
    g_jarvis_samples[at + i] = sample;
    squares += static_cast<int64_t>(sample) * sample;
  }
  g_jarvis_sample_count.store(at + count, std::memory_order_release);
  const uint32_t now = millis();
  if (count && sqrtf(static_cast<float>(squares) / count) >= JARVIS_SPEECH_RMS) {
    g_jarvis_heard_speech = true;
    g_jarvis_last_voice = now;
  }
  const bool heard = g_jarvis_heard_speech.load();
  if (at + count >= JARVIS_MAX_SAMPLES ||
      (heard && now - g_jarvis_capture_start >= 750 && now - g_jarvis_last_voice >= JARVIS_END_SILENCE_MS) ||
      (!heard && now - g_jarvis_capture_start >= 4000)) {
    g_jarvis_capture_end = now;
    g_jarvis_capturing.store(false, std::memory_order_release);
  }
}

static bool jarvis_cloud_init() {
  static bool registered = false;
  if (registered) return true;
  g_jarvis_samples = static_cast<int16_t *>(heap_caps_malloc(
      JARVIS_MAX_SAMPLES * sizeof(int16_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (!g_jarvis_samples) return false;
  id(jarvis_microphone).add_data_callback(jarvis_mic_callback);
  registered = true;
  return true;
}

// ESP-IDF gives each transport independent ownership; destroy the TLS parent too.
struct JarvisSocket {
  esp_transport_handle_t ssl = nullptr;
  esp_transport_handle_t ws = nullptr;
  uint8_t outgoing[JARVIS_FRAME_SAMPLES * sizeof(int16_t) + 8];
  uint32_t send_ms = 0;
  uint32_t slowest_send_ms = 0;
  ~JarvisSocket() {
    if (ws) { esp_transport_close(ws); esp_transport_destroy(ws); }
    if (ssl) esp_transport_destroy(ssl);
  }
  bool connect(const std::string &host, const std::string &key) {
    const uint32_t connect_started = millis();
    ssl = esp_transport_ssl_init();
    if (!ssl) return false;
    esp_transport_ssl_crt_bundle_attach(ssl, esp_crt_bundle_attach);
    ws = esp_transport_ws_init(ssl);
    if (!ws) return false;
    const std::string headers = "X-Jarvis-Key: " + key + "\r\n";
    const esp_transport_ws_config_t config = {
      .ws_path = "/ws", .sub_protocol = nullptr, .user_agent = "Jarvis-ESP32/realtime-20261004",
      .headers = headers.c_str(), .auth = nullptr, .propagate_control_frames = false};
    if (esp_transport_ws_set_config(ws, &config) != ESP_OK) return false;
    if (esp_transport_connect(ws, host.c_str(), 443, 10000) != 0) {
      ESP_LOGE("jarvis_ws", "Relay connection failed (HTTP %d)",
               esp_transport_ws_get_upgrade_request_status(ws));
      return false;
    }
    // Complete frames now share one TLS record, and the relay no longer sends
    // partial transcripts during upload. Send a frame's tail immediately.
    const int fd = esp_transport_get_socket(ssl);
    const int enabled = 1;
    if (fd < 0 || setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &enabled, sizeof(enabled)) != 0)
      ESP_LOGW("jarvis_ws", "Could not disable TCP buffering");
    ESP_LOGI("jarvis_ws", "Relay connected in %u ms", static_cast<unsigned>(millis() - connect_started));
    return true;
  }
  bool send(ws_transport_opcodes_t type, const char *data, size_t size) {
    // One masked RFC 6455 frame in ONE TLS write. IDF's send_raw writes its
    // tiny header separately, triggering delayed ACK stalls on mobile hotspots.
    if (size > JARVIS_FRAME_SAMPLES * sizeof(int16_t)) return false;
    outgoing[0] = static_cast<uint8_t>(type) | 0x80;  // FIN
    size_t header = 2;
    if (size <= 125) {
      outgoing[1] = 0x80 | static_cast<uint8_t>(size);  // MASK
    } else {
      outgoing[1] = 0x80 | 126;
      outgoing[2] = static_cast<uint8_t>(size >> 8);
      outgoing[3] = static_cast<uint8_t>(size);
      header = 4;
    }
    esp_fill_random(outgoing + header, 4);
    for (size_t i = 0; i < size; ++i)
      outgoing[header + 4 + i] = static_cast<uint8_t>(data[i]) ^ outgoing[header + (i % 4)];
    const size_t frame_size = header + 4 + size;
    const uint32_t started = millis();
    size_t sent = 0;
    while (sent < frame_size) {
      const int written = esp_transport_write(ssl, reinterpret_cast<const char *>(outgoing + sent),
                                              frame_size - sent, 5000);
      if (written <= 0) {
        ESP_LOGE("jarvis_ws", "Upload failed after %u of %u frame bytes", static_cast<unsigned>(sent),
                 static_cast<unsigned>(frame_size));
        return false;
      }
      sent += written;
    }
    const uint32_t elapsed = millis() - started;
    send_ms += elapsed;
    slowest_send_ms = std::max(slowest_send_ms, elapsed);
    return true;
  }
};

struct JarvisPlayback {
  uint8_t *audio = nullptr;
  std::atomic<size_t> available{0};
  std::atomic<bool> input_done{false};
  std::atomic<bool> abort{false};
  std::atomic<bool> finished{false};
  std::atomic<bool> failed{false};
  uint32_t question_end = 0;
};

static void jarvis_playback_task(void *param) {
  auto *state = static_cast<JarvisPlayback *>(param);
  constexpr size_t kPrebuffer = 3600;  // 300 ms of 12 kHz mu-law.
  std::vector<int32_t> out(4096);
  size_t played = 0;
  bool started = false;
  bool buffering = true;
  unsigned underruns = 0;
  while (!state->abort.load()) {
    const bool done = state->input_done.load(std::memory_order_acquire);
    const size_t available = state->available.load(std::memory_order_acquire);
    const size_t pending = available - played;
    if (!pending && done) break;
    if (!pending || (buffering && pending < kPrebuffer && !done)) {
      if (started && !buffering) ++underruns;
      buffering = true;
      vTaskDelay(pdMS_TO_TICKS(10));
      continue;
    }
    buffering = false;
    if (!started) {
      started = true;
      jarvis_speaker_playing = true;
      ESP_LOGI("jarvis_cloud", "First reply playback %u ms after end of capture",
               static_cast<unsigned>(millis() - state->question_end));
    }
    const size_t count = std::min(static_cast<size_t>(2048), pending);
    for (size_t i = 0; i < count; ++i) {
      const int32_t amplified = static_cast<int32_t>(jarvis_ulaw_to_linear(state->audio[played + i])) * JARVIS_REPLY_GAIN;
      const int32_t limited = std::clamp<int32_t>(amplified, -30000, 30000);
      const int32_t sample = limited * 65536;
      out[2 * i] = out[2 * i + 1] = sample;
    }
    auto *ptr = reinterpret_cast<const uint8_t *>(out.data());
    size_t remaining = count * 2 * sizeof(int32_t);
    while (remaining && !state->abort.load()) {
      size_t written = 0;
      const esp_err_t err = i2s_channel_write(jarvis_speaker_tx, ptr, remaining, &written, pdMS_TO_TICKS(1000));
      if (err != ESP_OK || !written) {
        ESP_LOGE("jarvis_cloud", "Reply speaker failed: %s", esp_err_to_name(err));
        state->failed = true;
        state->abort = true;
        break;
      }
      ptr += written;
      remaining -= written;
    }
    played += count;
  }
  if (started) vTaskDelay(pdMS_TO_TICKS(70));  // Drain I2S DMA before wake restart.
  jarvis_speaker_playing = false;
  ESP_LOGI("jarvis_cloud", "Played %u reply bytes; buffer underruns: %u",
           static_cast<unsigned>(played), underruns);
  std::vector<int32_t>().swap(out);  // vTaskDelete does not unwind C++ locals.
  state->finished.store(true, std::memory_order_release);
  vTaskDelete(nullptr);
}

static bool jarvis_receive_and_play(JarvisSocket &socket) {
  constexpr size_t kMaxReply = 1024 * 1024;
  JarvisPlayback state;
  state.audio = static_cast<uint8_t *>(heap_caps_malloc(kMaxReply, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (!state.audio) return false;
  state.question_end = g_jarvis_capture_end.load();
  if (xTaskCreate(jarvis_playback_task, "jarvis_play", 4096, &state, 5, nullptr) != pdPASS) {
    heap_caps_free(state.audio);
    return false;
  }
  bool got_done = false;
  bool protocol_ok = true;
  char incoming[4096];
  std::string control;
  size_t frame_read = 0;
  auto message_type = WS_TRANSPORT_OPCODES_NONE;
  while (!got_done && !state.failed.load()) {
    const int bytes = esp_transport_read(socket.ws, incoming, sizeof(incoming), 15000);
    if (bytes <= 0) {
      ESP_LOGE("jarvis_ws", "Relay closed or timed out before response completed");
      protocol_ok = false;
      break;
    }
    const auto opcode = esp_transport_ws_get_read_opcode(socket.ws);
    if (frame_read == 0 && opcode != WS_TRANSPORT_OPCODES_CONT) message_type = opcode;
    frame_read += bytes;
    if (message_type == WS_TRANSPORT_OPCODES_BINARY) {
      const size_t available = state.available.load();
      if (available + bytes > kMaxReply) { protocol_ok = false; break; }
      memcpy(state.audio + available, incoming, bytes);
      state.available.store(available + bytes, std::memory_order_release);
    } else if (message_type == WS_TRANSPORT_OPCODES_TEXT) {
      if (control.size() + bytes > 8192) { protocol_ok = false; break; }
      control.append(incoming, bytes);
    }
    if (frame_read == static_cast<size_t>(esp_transport_ws_get_read_payload_len(socket.ws))) {
      frame_read = 0;
      if (esp_transport_ws_get_fin_flag(socket.ws)) {
        if (message_type == WS_TRANSPORT_OPCODES_TEXT) {
          auto *json = cJSON_Parse(control.c_str());
          auto *type = json ? cJSON_GetObjectItemCaseSensitive(json, "type") : nullptr;
          if (!cJSON_IsString(type)) {
            protocol_ok = false;
          } else if (strcmp(type->valuestring, "error") == 0) {
            auto *message = cJSON_GetObjectItemCaseSensitive(json, "message");
            ESP_LOGE("jarvis_ws", "Relay error: %.160s", cJSON_IsString(message) ? message->valuestring : "Unknown error");
            protocol_ok = false;
          } else if (strcmp(type->valuestring, "done") == 0) {
            got_done = true;
          } else if (strcmp(type->valuestring, "transcript") == 0 || strcmp(type->valuestring, "answer") == 0) {
            ESP_LOGI("jarvis_ws", "Received %s at +%u ms", type->valuestring,
                     static_cast<unsigned>(millis() - state.question_end));
          }
          cJSON_Delete(json);
          control.clear();
        }
        message_type = WS_TRANSPORT_OPCODES_NONE;
      }
    }
    if (!protocol_ok) break;
  }
  if (!protocol_ok) state.abort = true;
  state.input_done.store(true, std::memory_order_release);
  while (!state.finished.load(std::memory_order_acquire)) vTaskDelay(pdMS_TO_TICKS(10));
  const bool ok = protocol_ok && got_done && state.available.load() > 0 && !state.failed.load();
  heap_caps_free(state.audio);
  return ok;
}

static bool jarvis_stream_question(const std::string &host, const std::string &key) {
  JarvisSocket socket;
  // Capture runs during DNS/TLS setup, hiding connection latency behind speech.
  if (!socket.connect(host, key)) return false;
  const char start[] = "{\"type\":\"start\",\"language\":\"en-IN\"}";
  if (!socket.send(WS_TRANSPORT_OPCODES_TEXT, start, sizeof(start) - 1)) return false;
  size_t sent = 0;
  while (true) {
    const bool capturing = g_jarvis_capturing.load(std::memory_order_acquire);
    const size_t available = g_jarvis_sample_count.load(std::memory_order_acquire);
    const size_t pending = available - sent;
    if (!capturing && !pending) break;
    if (pending >= JARVIS_FRAME_SAMPLES || (!capturing && pending)) {
      const size_t count = std::min(pending, JARVIS_FRAME_SAMPLES);
      if (!socket.send(WS_TRANSPORT_OPCODES_BINARY,
                       reinterpret_cast<const char *>(g_jarvis_samples + sent), count * sizeof(int16_t))) return false;
      sent += count;
    } else {
      if (millis() - g_jarvis_capture_start > 13000) {
        ESP_LOGE("jarvis_cloud", "Microphone stopped supplying samples");
        return false;
      }
      vTaskDelay(pdMS_TO_TICKS(10));
    }
  }
  id(jarvis_microphone).stop();
  const char end[] = "{\"type\":\"end\"}";
  if (!socket.send(WS_TRANSPORT_OPCODES_TEXT, end, sizeof(end) - 1)) return false;
  ESP_LOGI("jarvis_cloud", "Streamed %u samples in <=3200-byte messages; upload tail: %u ms",
           static_cast<unsigned>(sent), static_cast<unsigned>(millis() - g_jarvis_capture_end.load()));
  ESP_LOGI("jarvis_ws", "Total send time: %u ms; slowest frame: %u ms",
           static_cast<unsigned>(socket.send_ms), static_cast<unsigned>(socket.slowest_send_ms));
  return jarvis_receive_and_play(socket);
}

struct JarvisConversationArgs { std::string host; std::string key; };

static void jarvis_conversation_task(void *param) {
  auto *args = static_cast<JarvisConversationArgs *>(param);
  bool ok = false;
  if (!esphome::wifi::global_wifi_component->is_connected()) {
    ESP_LOGE("jarvis_cloud", "Cannot answer: Wi-Fi is disconnected");
  } else if (!jarvis_cloud_init()) {
    ESP_LOGE("jarvis_cloud", "Could not allocate microphone memory");
  } else {
    jarvis_speaker_beep();
    const uint32_t beep_start = millis();
    while (jarvis_speaker_playing && millis() - beep_start < 1000) vTaskDelay(pdMS_TO_TICKS(10));
    vTaskDelay(pdMS_TO_TICKS(70));
    g_jarvis_sample_count = 0;
    g_jarvis_heard_speech = false;
    g_jarvis_capture_start = g_jarvis_last_voice = millis();
    g_jarvis_capture_end = g_jarvis_capture_start;
    g_jarvis_capturing.store(true, std::memory_order_release);
    id(jarvis_microphone).start();
    ok = jarvis_stream_question(args->host, args->key);
  }
  g_jarvis_capturing.store(false, std::memory_order_release);
  id(jarvis_microphone).stop();
  if (!ok) ESP_LOGW("jarvis_cloud", "Conversation failed; returning to wake detection");
  delete args;
  g_jarvis_busy = false;
  id(jarvis_wake_word).start();
  vTaskDelete(nullptr);
}

static void jarvis_handle_conversation(const std::string &host, const std::string &key) {
  if (g_jarvis_busy.exchange(true)) return;
  auto *args = new JarvisConversationArgs{host, key};
  if (xTaskCreate(jarvis_conversation_task, "jarvis_conv", 12288, args, 5, nullptr) != pdPASS) {
    delete args;
    g_jarvis_busy = false;
    ESP_LOGE("jarvis_cloud", "Cannot start conversation task");
    id(jarvis_wake_word).start();
  }
}
