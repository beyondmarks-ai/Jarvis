#pragma once
// Optional phone-hub speaker client. This file is not used by jarvis_voice.yaml.
#include "esphome.h"
#include "jarvis_speaker.h"
#include "phone_audio_player.h"
#include <algorithm>
#include <atomic>
#include <cJSON.h>
#include <esp_crt_bundle.h>
#include <esp_random.h>
#include <esp_transport.h>
#include <esp_transport_ssl.h>
#include <esp_transport_ws.h>
#include <string>
#include <memory>
#include <vector>

static std::atomic<bool> phone_hub_connected{false};
static std::atomic<bool> phone_hub_active{false};

struct PhoneSpeakerConnection {
  esp_transport_handle_t tls = nullptr, ws = nullptr;
  std::string turn;
  uint32_t last_ping = 0;
  size_t reply_bytes = 0;
  std::unique_ptr<PhoneAudioPlayer> player;
  ~PhoneSpeakerConnection() {
    player.reset();
    if (ws) { esp_transport_close(ws); esp_transport_destroy(ws); }
    if (tls) esp_transport_destroy(tls);
    phone_hub_connected = false;
    phone_hub_active = false;
    jarvis_speaker_playing = false;
  }
  bool connect(const std::string &host, const std::string &key) {
    if (host.find("CHANGE_ME") != std::string::npos || key.size() < 24) return false;
    tls = esp_transport_ssl_init();
    if (!tls) return false;
    esp_transport_ssl_crt_bundle_attach(tls, esp_crt_bundle_attach);
    ws = esp_transport_ws_init(tls);
    if (!ws) return false;
    const std::string headers = "X-Jarvis-Speaker-Key: " + key + "\r\n";
    const esp_transport_ws_config_t cfg = {
      .ws_path = "/ws/speaker", .sub_protocol = nullptr,
      .user_agent = "Jarvis-Phone-Speaker/2", .headers = headers.c_str(),
      .auth = nullptr, .propagate_control_frames = false};
    if (esp_transport_ws_set_config(ws, &cfg) != ESP_OK) return false;
    if (esp_transport_connect(ws, host.c_str(), 443, 10000) != 0) return false;
    player = std::make_unique<PhoneAudioPlayer>();
    if (!player->init()) return false;
    phone_hub_connected = true;
    return true;
  }
  bool send(const char *type, bool with_turn = true) {
    // turn is validated as a 32-character hex identifier on receipt.
    const std::string json = with_turn
      ? "{\"type\":\"" + std::string(type) + "\",\"turn\":\"" + turn + "\"}"
      : "{\"type\":\"" + std::string(type) + "\"}";
    uint8_t frame[256];
    if (json.size() > 125) return false;
    frame[0] = 0x81; frame[1] = 0x80 | static_cast<uint8_t>(json.size());
    esp_fill_random(frame + 2, 4);
    for (size_t i = 0; i < json.size(); ++i) frame[6+i] = json[i] ^ frame[2+i%4];
    size_t sent = 0, total = json.size()+6;
    while (sent < total) {
      int n = esp_transport_write(tls, reinterpret_cast<const char *>(frame + sent), total - sent, 3000);
      if (n <= 0) return false;
      sent += n;
    }
    return true;
  }
  bool control(const std::string &text) {
    cJSON *doc = cJSON_Parse(text.c_str());
    if (!doc) return false;
    cJSON *kind = cJSON_GetObjectItemCaseSensitive(doc, "type");
    cJSON *turn_item = cJSON_GetObjectItemCaseSensitive(doc, "turn");
    const std::string type = cJSON_IsString(kind) ? kind->valuestring : "";
    const std::string requested = cJSON_IsString(turn_item) ? turn_item->valuestring : "";
    cJSON_Delete(doc);
    if (type == "pong") return true;
    if (requested.size() != 32 || requested.find_first_not_of("0123456789abcdef") != std::string::npos) return false;
    if (type == "begin") {
      if (!turn.empty()) return false;
      turn = requested; reply_bytes = 0; phone_hub_active = true;
      jarvis_speaker_beep();
      const uint32_t start = millis();
      while (jarvis_speaker_playing && millis()-start < 1500) vTaskDelay(pdMS_TO_TICKS(10));
      if (jarvis_speaker_playing) return false;
      vTaskDelay(pdMS_TO_TICKS(70));
      if (!player->begin()) return false;
      return send("beep_done");
    }
    if (requested != turn) return type == "cancel"; // harmless cancellation of a completed turn
    if (type == "end") {
      if (!player->finish()) return false;
      bool ok = send("played");
      turn.clear(); phone_hub_active = false;
      return ok;
    }
    if (type == "cancel") {
      if (!player->cancel()) return false;
      turn.clear(); phone_hub_active = false; jarvis_speaker_playing = false;
      return true;
    }
    return false;
  }
  bool audio(const uint8_t *bytes, size_t size) {
    if (turn.empty() || reply_bytes + size > 1024*1024) return false;
    reply_bytes += size;
    return player->enqueue(bytes, size);
  }
  void run() {
    char incoming[4096];
    std::string text;
    size_t frame_read = 0;
    auto message_type = WS_TRANSPORT_OPCODES_NONE;
    while (esphome::wifi::global_wifi_component->is_connected()) {
      if (player->failed) return;
      if (millis()-last_ping > 10000) {
        if (!send("ping", false)) return;
        last_ping = millis();
      }
      int available = esp_transport_poll_read(ws, 500);
      if (available < 0) return;
      if (!available) continue;
      int count = esp_transport_read(ws, incoming, sizeof(incoming), 5000);
      if (count < 0) return;
      // ESP-IDF consumes WebSocket ping/pong frames internally and returns zero.
      // Zero is not a disconnected socket: cloud keepalives must not reconnect us.
      if (count == 0) continue;
      auto opcode = esp_transport_ws_get_read_opcode(ws);
      if (frame_read == 0 && opcode != WS_TRANSPORT_OPCODES_CONT) message_type = opcode;
      frame_read += count;
      if (message_type == WS_TRANSPORT_OPCODES_BINARY) {
        if (!audio(reinterpret_cast<uint8_t *>(incoming), count)) return;
      } else if (message_type == WS_TRANSPORT_OPCODES_TEXT) {
        if (text.size()+count > 512) return;
        text.append(incoming, count);
      } else return;
      if (frame_read == static_cast<size_t>(esp_transport_ws_get_read_payload_len(ws))) {
        frame_read = 0;
        if (esp_transport_ws_get_fin_flag(ws)) {
          if (message_type == WS_TRANSPORT_OPCODES_TEXT && !control(text)) return;
          text.clear(); message_type = WS_TRANSPORT_OPCODES_NONE;
        }
      }
    }
  }
};

struct PhoneSpeakerArgs { std::string host, key; };
static void phone_speaker_task(void *value) {
  auto *args = static_cast<PhoneSpeakerArgs *>(value);
  while (true) {
    if (esphome::wifi::global_wifi_component->is_connected() && jarvis_speaker_init()) {
      PhoneSpeakerConnection connection;
      if (connection.connect(args->host, args->key)) {
        ESP_LOGI("phone_speaker", "Connected to microphone hub");
        connection.run();
      }
    }
    vTaskDelay(pdMS_TO_TICKS(3000));
  }
}
static void phone_speaker_start(const std::string &host, const std::string &key) {
  auto *args = new PhoneSpeakerArgs{host, key};
  if (xTaskCreate(phone_speaker_task, "phone_speaker", 12288, args, 5, nullptr) != pdPASS) {
    delete args;
    ESP_LOGE("phone_speaker", "Could not start speaker task");
  }
}
