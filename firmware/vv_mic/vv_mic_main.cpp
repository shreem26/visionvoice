#include <Arduino.h>
// VisionVoice microphone board firmware v1.0.0
// Board: ESP32 DevKit (Arduino: "ESP32 Dev Module"), arduino-esp32 core 3.x
// Mic:   INMP441 (I2S).  Protocol: see PROTOCOL.md section 3.
//
// This firmware only moves data. All decisions happen on the laptop, so this
// file should never need to change once it works.

#include <WiFi.h>
#include <WiFiMulti.h>
#include <WiFiUdp.h>
#include <ESPmDNS.h>
#include <Preferences.h>
#include <ESP_I2S.h>
#include "config.h"

#define FW_VERSION "1.0.0"
static const uint16_t TCP_PORT = 5001;
static const uint16_t DISCOVERY_PORT = 4210;
static const uint32_t SAMPLE_RATE = 16000;
static const size_t RING_SAMPLES = 32768;          // ~2 s of audio, power of two
static const size_t CHUNK_SAMPLES = 512;           // 32 ms per audio frame
static const uint32_t CLIENT_TIMEOUT_MS = 10000;
static const uint32_t MAX_PAYLOAD_IN = 512;

// ---------------- shared state ----------------
static int16_t ring[RING_SAMPLES];
static volatile uint32_t ringWrite = 0;
static volatile int32_t peakL = 0, peakR = 0;

static volatile int cfgGainShift = 14;
static volatile int cfgChannel = 0;   // 0 = left, 1 = right
static volatile bool cfgHpf = true;
static uint32_t cfgMaxMs = 15000;
static uint32_t cfgPrerollMs = 250;

I2SClass i2s;
WiFiMulti wifiMulti;
WiFiServer server(TCP_PORT);
WiFiClient client;
WiFiUDP udp;
Preferences prefs;

static String deviceId;
static bool wifiWasConnected = false;
static uint32_t lastClientRx = 0;
static uint32_t lastStatus = 0;
static uint8_t hostLedMode = 4;

// streaming
static bool streaming = false;
static const char* streamReason = "button";
static uint32_t streamReadIdx = 0;
static uint32_t streamStartMs = 0;
static uint32_t streamSamples = 0;
static uint32_t streamDrops = 0;
static uint32_t totalDrops = 0;

// buttons
static bool btnStable = false, btnLastRaw = false;
static uint32_t btnChangedAt = 0, btnPressedAt = 0;
static const char* btnSrc = "boot";

// ---------------- audio task ----------------
static void audioTask(void*) {
  static int32_t buf[256 * 2];  // 256 stereo frames, 32-bit slots
  float hpIn = 0, hpOut = 0;
  const float a = 0.995f;       // ~13 Hz DC-blocking high-pass
  for (;;) {
    size_t n = i2s.readBytes((char*)buf, sizeof(buf));
    size_t frames = n / 8;
    if (frames == 0) { vTaskDelay(1); continue; }
    int32_t pl = peakL, pr = peakR;
    int shift = cfgGainShift;
    int ch = cfgChannel;
    bool hpf = cfgHpf;
    for (size_t i = 0; i < frames; i++) {
      int32_t l = buf[2 * i], r = buf[2 * i + 1];
      int32_t al = abs(l >> 16), ar = abs(r >> 16);
      if (al > pl) pl = al;
      if (ar > pr) pr = ar;
      float x = (float)((ch ? r : l) >> 8);           // 24-bit sample
      float y = x;
      if (hpf) { y = a * (hpOut + x - hpIn); hpIn = x; hpOut = y; }
      float scaled = y * 256.0f / (float)(1UL << shift);
      int32_t v = (int32_t)scaled;
      if (v > 32767) v = 32767;
      if (v < -32768) v = -32768;
      ring[ringWrite & (RING_SAMPLES - 1)] = (int16_t)v;
      ringWrite = ringWrite + 1;
    }
    peakL = pl; peakR = pr;
  }
}

// ---------------- framing ----------------
static uint8_t txbuf[6 + CHUNK_SAMPLES * 2];

static bool sendFrame(char type, const uint8_t* data, uint32_t len) {
  if (!client || !client.connected()) return false;
  txbuf[0] = 'V'; txbuf[1] = (uint8_t)type;
  txbuf[2] = len & 0xFF; txbuf[3] = (len >> 8) & 0xFF;
  txbuf[4] = (len >> 16) & 0xFF; txbuf[5] = (len >> 24) & 0xFF;
  size_t sent;
  if (len + 6 <= sizeof(txbuf)) {
    if (len) memcpy(txbuf + 6, data, len);
    sent = client.write(txbuf, len + 6);
    if (sent != len + 6) { client.stop(); return false; }
  } else {
    if (client.write(txbuf, 6) != 6) { client.stop(); return false; }
    if (client.write(data, len) != len) { client.stop(); return false; }
  }
  return true;
}

static bool sendText(char type, const String& s) {
  return sendFrame(type, (const uint8_t*)s.c_str(), s.length());
}

static void logLine(const String& s) {
  Serial.println(s);
  sendText('L', s);
}

// ---------------- LED ----------------
static void ledWrite(bool on) { digitalWrite(PIN_LED, (on == (LED_ACTIVE_HIGH == 1)) ? HIGH : LOW); }

static void updateLed() {
  uint32_t t = millis();
  uint8_t mode;
  if (streaming) mode = 1;
  else if (WiFi.status() != WL_CONNECTED) mode = 3;
  else if (!client || !client.connected()) mode = 2;
  else mode = hostLedMode;
  bool on = false;
  switch (mode) {
    case 0: on = false; break;
    case 1: on = true; break;
    case 2: on = (t % 1000) < 500; break;
    case 3: on = (t % 200) < 100; break;
    case 4: default: on = (t % 3000) < 60; break;
  }
  ledWrite(on);
}

// ---------------- status / hello ----------------
static String cfgJson() {
  return String("{\"gain_shift\":") + cfgGainShift + ",\"channel\":\"" + (cfgChannel ? "R" : "L") +
         "\",\"max_ms\":" + cfgMaxMs + ",\"preroll_ms\":" + cfgPrerollMs + ",\"hpf\":" + (cfgHpf ? 1 : 0) + "}";
}

static void sendHello() {
  String s = String("{\"vv\":1,\"role\":\"mic\",\"id\":\"") + deviceId + "\",\"fw\":\"" FW_VERSION
             "\",\"rate\":16000,\"bits\":16,\"channels\":1}";
  sendText('H', s);
}

static void sendStatus() {
  int32_t pl = peakL, pr = peakR;
  peakL = 0; peakR = 0;
  String s = String("{\"up\":") + (millis() / 1000) + ",\"rssi\":" + WiFi.RSSI() + ",\"heap\":" + ESP.getFreeHeap() +
             ",\"btn\":" + (btnStable ? 1 : 0) + ",\"stream\":" + (streaming ? 1 : 0) + ",\"lvl\":[" + pl + "," + pr +
             "],\"drops\":" + totalDrops + ",\"cfg\":" + cfgJson() + "}";
  sendText('S', s);
  lastStatus = millis();
}

// ---------------- streaming ----------------
static void startStream(const char* reason) {
  if (streaming) return;
  uint32_t w = ringWrite;
  uint32_t pre = cfgPrerollMs * (SAMPLE_RATE / 1000);
  if (pre > RING_SAMPLES / 2) pre = RING_SAMPLES / 2;
  streamReadIdx = w - pre;
  streaming = true;
  streamReason = reason;
  streamStartMs = millis();
  streamSamples = 0;
  streamDrops = 0;
  sendText('B', String("{\"reason\":\"") + reason + "\",\"preroll_ms\":" + cfgPrerollMs + "}");
}

static void pumpStream(bool flushAll) {
  if (!streaming) return;
  static int16_t out[CHUNK_SAMPLES];
  uint32_t w = ringWrite;
  uint32_t avail = w - streamReadIdx;
  if (avail > RING_SAMPLES - 2048) {  // laptop/network too slow: skip ahead
    uint32_t skip = avail - RING_SAMPLES / 2;
    streamReadIdx += skip;
    streamDrops += skip;
    totalDrops += skip;
    avail = w - streamReadIdx;
  }
  while (avail >= CHUNK_SAMPLES || (flushAll && avail > 0)) {
    uint32_t n = avail >= CHUNK_SAMPLES ? CHUNK_SAMPLES : avail;
    for (uint32_t i = 0; i < n; i++) out[i] = ring[(streamReadIdx + i) & (RING_SAMPLES - 1)];
    if (!sendFrame('A', (const uint8_t*)out, n * 2)) { streaming = false; return; }
    streamReadIdx += n;
    streamSamples += n;
    avail -= n;
  }
}

static void stopStream(const char* reason) {
  if (!streaming) return;
  pumpStream(true);
  streaming = false;
  sendText('E', String("{\"reason\":\"") + reason + "\",\"samples\":" + streamSamples + ",\"drops\":" + streamDrops + "}");
}

// ---------------- incoming frames ----------------
static uint8_t rxHdr[6];
static uint8_t rxPayload[MAX_PAYLOAD_IN + 1];
static uint32_t rxHave = 0, rxNeed = 0;
static bool rxInPayload = false;

static void applyConfig(const char* text) {
  String s(text);
  int start = 0;
  while (start < (int)s.length()) {
    int end = s.indexOf(';', start);
    if (end < 0) end = s.length();
    String kv = s.substring(start, end);
    int eq = kv.indexOf('=');
    if (eq > 0) {
      String k = kv.substring(0, eq); k.trim();
      String v = kv.substring(eq + 1); v.trim();
      if (k == "gain_shift") { int x = v.toInt(); if (x >= 8 && x <= 20) cfgGainShift = x; }
      else if (k == "channel") { cfgChannel = (v == "R" || v == "r") ? 1 : 0; }
      else if (k == "max_ms") { long x = v.toInt(); if (x >= 500 && x <= 30000) cfgMaxMs = x; }
      else if (k == "preroll_ms") { long x = v.toInt(); if (x >= 0 && x <= 500) cfgPrerollMs = x; }
      else if (k == "hpf") { cfgHpf = v.toInt() != 0; }
    }
    start = end + 1;
  }
}

static void handleFrame(char type, uint8_t* p, uint32_t len) {
  p[len] = 0;
  switch (type) {
    case 'P': sendStatus(); break;
    case 'C': applyConfig((const char*)p); sendStatus(); break;
    case 'R': if (len && p[0] == '1') startStream("host"); else stopStream("host"); break;
    case 'I': if (len) hostLedMode = p[0] <= 4 ? p[0] : 4; break;
    case 'X': client.stop(); delay(100); ESP.restart(); break;
    default: break;  // unknown types ignored (forward compatible)
  }
}

static void readClient() {
  while (client && client.connected() && client.available()) {
    lastClientRx = millis();
    if (!rxInPayload) {
      rxHdr[rxHave++] = client.read();
      if (rxHave == 1 && rxHdr[0] != 'V') { rxHave = 0; continue; }  // resync
      if (rxHave == 6) {
        rxNeed = rxHdr[2] | (rxHdr[3] << 8) | (rxHdr[4] << 16) | ((uint32_t)rxHdr[5] << 24);
        if (rxNeed > MAX_PAYLOAD_IN) { client.stop(); rxHave = 0; return; }
        rxHave = 0;
        if (rxNeed == 0) handleFrame((char)rxHdr[1], rxPayload, 0);
        else rxInPayload = true;
      }
    } else {
      int n = client.read(rxPayload + rxHave, rxNeed - rxHave);
      if (n > 0) rxHave += n;
      if (rxHave == rxNeed) {
        rxInPayload = false;
        rxHave = 0;
        handleFrame((char)rxHdr[1], rxPayload, rxNeed);
      }
    }
  }
}

// ---------------- buttons ----------------
static void pollButtons() {
  bool bootDown = digitalRead(PIN_BTN_BOOT) == LOW;
  bool extDown = digitalRead(PIN_BTN_EXT) == LOW;
  bool raw = bootDown || extDown;
  uint32_t t = millis();
  if (raw != btnLastRaw) { btnLastRaw = raw; btnChangedAt = t; }
  if (raw != btnStable && t - btnChangedAt > 25) {
    btnStable = raw;
    if (btnStable) {
      btnPressedAt = t;
      btnSrc = extDown ? "ext" : "boot";
      sendText('D', String("{\"src\":\"") + btnSrc + "\",\"ms\":" + t + "}");
      if (client && client.connected()) startStream("button");
      Serial.println("[btn] down");
    } else {
      sendText('U', String("{\"ms\":") + t + ",\"held_ms\":" + (t - btnPressedAt) + "}");
      if (streaming && strcmp(streamReason, "button") == 0) stopStream("button");
      Serial.println("[btn] up");
    }
  }
}

// ---------------- Wi-Fi / discovery ----------------
static void setupWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.setHostname(DEVICE_HOSTNAME);
  WiFi.setSleep(false);
  prefs.begin("vv", true);
  String s = prefs.getString("ssid", ""), p = prefs.getString("pass", "");
  prefs.end();
  int count = 0;
  if (s.length()) { wifiMulti.addAP(s.c_str(), p.c_str()); count++; Serial.println("[wifi] saved network: " + s); }
  if (strlen(WIFI_SSID_1)) { wifiMulti.addAP(WIFI_SSID_1, WIFI_PASS_1); count++; }
  if (strlen(WIFI_SSID_2)) { wifiMulti.addAP(WIFI_SSID_2, WIFI_PASS_2); count++; }
  if (strlen(WIFI_SSID_3)) { wifiMulti.addAP(WIFI_SSID_3, WIFI_PASS_3); count++; }
  if (!count) Serial.println("[wifi] NO NETWORK CONFIGURED - edit config.h or type ssid=/pass=/save");
}

static void serviceWifi() {
  static uint32_t lastTry = 0;
  bool up = WiFi.status() == WL_CONNECTED;
  if (!up && millis() - lastTry > 3000) {
    lastTry = millis();
    wifiMulti.run(2500);
    up = WiFi.status() == WL_CONNECTED;
  }
  if (up && !wifiWasConnected) {
    Serial.printf("[wifi] connected to %s, IP %s, RSSI %d\n", WiFi.SSID().c_str(),
                  WiFi.localIP().toString().c_str(), WiFi.RSSI());
    MDNS.end();
    if (MDNS.begin(DEVICE_HOSTNAME)) MDNS.addService("visionvoice", "tcp", TCP_PORT);
    udp.stop();
    udp.begin(DISCOVERY_PORT);
    server.begin();
    server.setNoDelay(true);
    Serial.printf("[ready] mic server on %s:%u\n", WiFi.localIP().toString().c_str(), TCP_PORT);
  }
  if (!up && wifiWasConnected) Serial.println("[wifi] disconnected, retrying...");
  wifiWasConnected = up;
}

static void serviceDiscovery() {
  int n = udp.parsePacket();
  if (n <= 0) return;
  char buf[64];
  int len = udp.read(buf, sizeof(buf) - 1);
  if (len < 0) return;
  buf[len] = 0;
  if (strncmp(buf, "VV?DISCOVER", 11) != 0) return;
  String reply = String("{\"vv\":1,\"role\":\"mic\",\"id\":\"") + deviceId + "\",\"fw\":\"" FW_VERSION "\",\"ip\":\"" +
                 WiFi.localIP().toString() + "\",\"port\":" + TCP_PORT + "}";
  udp.beginPacket(udp.remoteIP(), udp.remotePort());
  udp.write((const uint8_t*)reply.c_str(), reply.length());
  udp.endPacket();
}

static void serviceClient() {
  WiFiClient incoming = server.accept();
  if (incoming) {
    if (client && client.connected()) { if (streaming) streaming = false; client.stop(); }
    client = incoming;
    client.setNoDelay(true);
    lastClientRx = millis();
    rxHave = 0; rxInPayload = false;
    Serial.printf("[tcp] laptop connected from %s\n", client.remoteIP().toString().c_str());
    sendHello();
    sendStatus();
  }
  if (client && client.connected()) {
    readClient();
    if (millis() - lastClientRx > CLIENT_TIMEOUT_MS) {
      Serial.println("[tcp] laptop silent for 10 s, dropping");
      client.stop();
      streaming = false;
    }
  } else if (streaming) {
    streaming = false;
  }
}

// ---------------- serial console (change Wi-Fi without reflashing) ----------------
static String pendingSsid, pendingPass;

static void serviceSerial() {
  static String line;
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\r') continue;
    if (c != '\n') { if (line.length() < 120) line += c; continue; }
    line.trim();
    if (line.startsWith("ssid=")) { pendingSsid = line.substring(5); Serial.println("ok ssid"); }
    else if (line.startsWith("pass=")) { pendingPass = line.substring(5); Serial.println("ok pass"); }
    else if (line == "save") {
      prefs.begin("vv", false); prefs.putString("ssid", pendingSsid); prefs.putString("pass", pendingPass); prefs.end();
      Serial.println("saved, rebooting"); delay(200); ESP.restart();
    } else if (line == "clear") {
      prefs.begin("vv", false); prefs.clear(); prefs.end(); Serial.println("cleared saved network, rebooting"); delay(200); ESP.restart();
    } else if (line == "info") {
      Serial.printf("id=%s fw=%s wifi=%s ip=%s rssi=%d client=%d cfg=%s\n", deviceId.c_str(), FW_VERSION,
                    WiFi.status() == WL_CONNECTED ? WiFi.SSID().c_str() : "(none)", WiFi.localIP().toString().c_str(),
                    WiFi.RSSI(), (client && client.connected()) ? 1 : 0, cfgJson().c_str());
    } else if (line == "level") {
      Serial.printf("peakL=%ld peakR=%ld\n", (long)peakL, (long)peakR);
    } else if (line == "reboot") { ESP.restart(); }
    else if (line.length()) Serial.println("commands: ssid=NAME  pass=PASSWORD  save  clear  info  level  reboot");
    line = "";
  }
}

// ---------------- setup / loop ----------------
void setup() {
  Serial.begin(115200);
  delay(200);
  uint64_t mac = ESP.getEfuseMac();
  char idbuf[24];
  snprintf(idbuf, sizeof(idbuf), "vv-mic-%02x%02x%02x", (uint8_t)(mac >> 24), (uint8_t)(mac >> 32), (uint8_t)(mac >> 40));
  deviceId = idbuf;
  Serial.printf("\n[boot] VisionVoice mic %s fw %s\n", deviceId.c_str(), FW_VERSION);

  pinMode(PIN_LED, OUTPUT);
  pinMode(PIN_BTN_BOOT, INPUT_PULLUP);
  pinMode(PIN_BTN_EXT, INPUT_PULLUP);

  i2s.setPins(PIN_I2S_SCK, PIN_I2S_WS, -1, PIN_I2S_SD);
  if (!i2s.begin(I2S_MODE_STD, SAMPLE_RATE, I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO)) {
    Serial.println("[i2s] INIT FAILED");
  } else {
    Serial.println("[i2s] ok (16 kHz, 32-bit stereo slots)");
  }
  xTaskCreatePinnedToCore(audioTask, "audio", 4096, nullptr, 5, nullptr, 1);

  setupWifi();
  Serial.println("[help] type 'info' or 'level' in the Serial Monitor");
}

void loop() {
  serviceSerial();
  serviceWifi();
  if (WiFi.status() == WL_CONNECTED) {
    serviceDiscovery();
    serviceClient();
  }
  pollButtons();
  if (streaming) {
    if (millis() - streamStartMs > cfgMaxMs) stopStream("max");
    else pumpStream(false);
  }
  if (client && client.connected() && millis() - lastStatus > 1000) sendStatus();
  updateLed();
  delay(streaming ? 1 : 5);
}
