// VisionVoice camera board firmware v1.0.0  (AI-Thinker ESP32-CAM, OV2640)
// Protocol: PROTOCOL.md section 2. The board is stateless: the laptop sends
// size/quality/flash with every capture and its sensor settings on connect.

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiMulti.h>
#include <WiFiUdp.h>
#include <ESPmDNS.h>
#include <Preferences.h>
#include "esp_camera.h"
#include "esp_http_server.h"
#include "config.h"

#define FW_VERSION "1.0.0"
static const uint16_t DISCOVERY_PORT = 4210;

// ESP32-S3-WROOM CAM camera pins (Freenove-style board)
#define PWDN_GPIO_NUM -1
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM 15
#define SIOD_GPIO_NUM 4
#define SIOC_GPIO_NUM 5
#define Y9_GPIO_NUM 16
#define Y8_GPIO_NUM 17
#define Y7_GPIO_NUM 18
#define Y6_GPIO_NUM 12
#define Y5_GPIO_NUM 10
#define Y4_GPIO_NUM 8
#define Y3_GPIO_NUM 9
#define Y2_GPIO_NUM 11
#define VSYNC_GPIO_NUM 6
#define HREF_GPIO_NUM 7
#define PCLK_GPIO_NUM 13

static const uint8_t FLASH_LEDC_CHANNEL = 7;  // camera XCLK uses channel 0 / timer 0

WiFiMulti wifiMulti;
WiFiUDP udp;
Preferences prefs;
static httpd_handle_t httpServer = nullptr;
static httpd_handle_t streamServer = nullptr;
static SemaphoreHandle_t camMutex;

static String deviceId;
static bool camOk = false;
static esp_err_t camErr = ESP_OK;
static bool hasPsram = false;
static uint32_t captures = 0, failures = 0;
static uint32_t lastCaptureMs = 0;
static bool wifiWasConnected = false;
static uint32_t lastCamRetry = 0;

// ---------------- camera ----------------
static bool initCamera() {
  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0;
  c.ledc_timer = LEDC_TIMER_0;
  c.pin_d0 = Y2_GPIO_NUM; c.pin_d1 = Y3_GPIO_NUM; c.pin_d2 = Y4_GPIO_NUM; c.pin_d3 = Y5_GPIO_NUM;
  c.pin_d4 = Y6_GPIO_NUM; c.pin_d5 = Y7_GPIO_NUM; c.pin_d6 = Y8_GPIO_NUM; c.pin_d7 = Y9_GPIO_NUM;
  c.pin_xclk = XCLK_GPIO_NUM; c.pin_pclk = PCLK_GPIO_NUM; c.pin_vsync = VSYNC_GPIO_NUM; c.pin_href = HREF_GPIO_NUM;
  c.pin_sccb_sda = SIOD_GPIO_NUM; c.pin_sccb_scl = SIOC_GPIO_NUM;
  c.pin_pwdn = PWDN_GPIO_NUM; c.pin_reset = RESET_GPIO_NUM;
  c.xclk_freq_hz = 20000000;
  c.pixel_format = PIXFORMAT_JPEG;
  hasPsram = psramFound();
  if (hasPsram) {
    c.frame_size = FRAMESIZE_UXGA;
    c.jpeg_quality = 10;
    c.fb_count = 2;
    c.fb_location = CAMERA_FB_IN_PSRAM;
    c.grab_mode = CAMERA_GRAB_LATEST;
  } else {
    c.frame_size = FRAMESIZE_SVGA;
    c.jpeg_quality = 12;
    c.fb_count = 1;
    c.fb_location = CAMERA_FB_IN_DRAM;
    c.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  }
  camErr = esp_camera_init(&c);
  if (camErr != ESP_OK) {
    Serial.printf("[cam] INIT FAILED 0x%x - check the ribbon cable and that PSRAM is enabled\n", camErr);
    return false;
  }
  Serial.printf("[cam] ok, PSRAM %s, max size %s\n", hasPsram ? "yes" : "NO", hasPsram ? "UXGA" : "SVGA");
  return true;
}

static void setFlash(int duty) { ledcWrite(PIN_FLASH_LED, duty < 0 ? 0 : (duty > 255 ? 255 : duty)); }

struct SizeName { const char* name; framesize_t size; };
static const SizeName SIZES[] = {
  {"QVGA", FRAMESIZE_QVGA}, {"VGA", FRAMESIZE_VGA}, {"SVGA", FRAMESIZE_SVGA}, {"XGA", FRAMESIZE_XGA},
  {"HD", FRAMESIZE_HD}, {"SXGA", FRAMESIZE_SXGA}, {"UXGA", FRAMESIZE_UXGA},
};

static const char* sizeName(framesize_t f) {
  for (auto& s : SIZES) if (s.size == f) return s.name;
  return "OTHER";
}

// ---------------- http helpers ----------------
static bool queryValue(httpd_req_t* req, const char* key, char* out, size_t outLen) {
  size_t len = httpd_req_get_url_query_len(req) + 1;
  if (len <= 1) return false;
  char* q = (char*)malloc(len);
  if (!q) return false;
  bool ok = httpd_req_get_url_query_str(req, q, len) == ESP_OK && httpd_query_key_value(q, key, out, outLen) == ESP_OK;
  free(q);
  return ok;
}

static esp_err_t sendJson(httpd_req_t* req, const String& body, const char* status = "200 OK") {
  httpd_resp_set_status(req, status);
  httpd_resp_set_type(req, "application/json");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  return httpd_resp_send(req, body.c_str(), body.length());
}

// ---------------- handlers ----------------
static esp_err_t statusHandler(httpd_req_t* req) {
  sensor_t* s = camOk ? esp_camera_sensor_get() : nullptr;
  String j = String("{\"vv\":1,\"role\":\"cam\",\"id\":\"") + deviceId + "\",\"fw\":\"" FW_VERSION "\",\"ip\":\"" +
             WiFi.localIP().toString() + "\",\"rssi\":" + WiFi.RSSI() + ",\"uptime_s\":" + (millis() / 1000) +
             ",\"heap\":" + ESP.getFreeHeap() + ",\"psram\":" + (hasPsram ? ESP.getPsramSize() : 0) +
             ",\"camera_ok\":" + (camOk ? "true" : "false") + ",\"camera_err\":" + (int)camErr +
             ",\"size\":\"" + (s ? sizeName((framesize_t)s->status.framesize) : "") + "\",\"quality\":" +
             (s ? s->status.quality : 0) + ",\"captures\":" + captures + ",\"failures\":" + failures + "}";
  return sendJson(req, j);
}

static esp_err_t captureHandler(httpd_req_t* req) {
  if (!camOk) return sendJson(req, "{\"error\":\"camera not initialised - check ribbon cable\"}", "503 Service Unavailable");
  char v[16];
  framesize_t fs = hasPsram ? FRAMESIZE_UXGA : FRAMESIZE_SVGA;
  int quality = 10, flash = 0, skip = 1;
  if (queryValue(req, "size", v, sizeof(v))) {
    bool found = false;
    for (auto& s : SIZES) if (strcasecmp(v, s.name) == 0) { fs = s.size; found = true; }
    if (!found) return sendJson(req, "{\"error\":\"unknown size\"}", "400 Bad Request");
  }
  if (!hasPsram && fs > FRAMESIZE_SVGA) fs = FRAMESIZE_SVGA;
  if (queryValue(req, "quality", v, sizeof(v))) quality = constrain(atoi(v), 8, 63);
  if (queryValue(req, "flash", v, sizeof(v))) flash = constrain(atoi(v), 0, 255);
  if (queryValue(req, "skip", v, sizeof(v))) skip = constrain(atoi(v), 0, 5);

  if (xSemaphoreTake(camMutex, pdMS_TO_TICKS(8000)) != pdTRUE)
    return sendJson(req, "{\"error\":\"camera busy\"}", "503 Service Unavailable");

  sensor_t* s = esp_camera_sensor_get();
  int extra = 0;
  if (s->status.framesize != fs) { s->set_framesize(s, fs); extra += 2; }
  if (s->status.quality != quality) { s->set_quality(s, quality); extra += 1; }
  if (flash > 0) { setFlash(flash); delay(150); extra += 1; }
  for (int i = 0; i < skip + extra; i++) {
    camera_fb_t* old = esp_camera_fb_get();
    if (old) esp_camera_fb_return(old);
  }
  camera_fb_t* fb = esp_camera_fb_get();
  if (flash > 0) setFlash(0);
  if (!fb) {
    failures++;
    xSemaphoreGive(camMutex);
    return sendJson(req, "{\"error\":\"capture failed\"}", "500 Internal Server Error");
  }
  char res[24];
  snprintf(res, sizeof(res), "%ux%u", fb->width, fb->height);
  httpd_resp_set_type(req, "image/jpeg");
  httpd_resp_set_hdr(req, "Content-Disposition", "inline; filename=capture.jpg");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  httpd_resp_set_hdr(req, "X-VV-Resolution", res);
  esp_err_t r = httpd_resp_send(req, (const char*)fb->buf, fb->len);
  esp_camera_fb_return(fb);
  xSemaphoreGive(camMutex);
  captures++;
  lastCaptureMs = millis();
  return r;
}

typedef int (*SetFn)(sensor_t*, int);
struct Control { const char* name; int lo; int hi; };
static const Control CONTROLS[] = {
  {"brightness", -2, 2}, {"contrast", -2, 2}, {"saturation", -2, 2}, {"ae_level", -2, 2},
  {"vflip", 0, 1}, {"hmirror", 0, 1}, {"awb", 0, 1}, {"aec", 0, 1}, {"aec2", 0, 1}, {"agc", 0, 1},
  {"dcw", 0, 1}, {"lenc", 0, 1}, {"wb_mode", 0, 4}, {"aec_value", 0, 1200}, {"gainceiling", 0, 6},
};

static SetFn setterFor(sensor_t* s, const char* n) {
  if (!strcmp(n, "brightness")) return s->set_brightness;
  if (!strcmp(n, "contrast")) return s->set_contrast;
  if (!strcmp(n, "saturation")) return s->set_saturation;
  if (!strcmp(n, "ae_level")) return s->set_ae_level;
  if (!strcmp(n, "vflip")) return s->set_vflip;
  if (!strcmp(n, "hmirror")) return s->set_hmirror;
  if (!strcmp(n, "awb")) return s->set_whitebal;
  if (!strcmp(n, "aec")) return s->set_exposure_ctrl;
  if (!strcmp(n, "aec2")) return s->set_aec2;
  if (!strcmp(n, "agc")) return s->set_gain_ctrl;
  if (!strcmp(n, "dcw")) return s->set_dcw;
  if (!strcmp(n, "lenc")) return s->set_lenc;
  if (!strcmp(n, "wb_mode")) return s->set_wb_mode;
  if (!strcmp(n, "aec_value")) return s->set_aec_value;
  return nullptr;
}

static esp_err_t controlHandler(httpd_req_t* req) {
  if (!camOk) return sendJson(req, "{\"ok\":false,\"error\":\"camera not initialised\"}", "503 Service Unavailable");
  char var[24], val[16];
  if (!queryValue(req, "var", var, sizeof(var)) || !queryValue(req, "val", val, sizeof(val)))
    return sendJson(req, "{\"ok\":false,\"error\":\"need var and val\"}", "400 Bad Request");
  const Control* c = nullptr;
  for (auto& x : CONTROLS) if (!strcmp(var, x.name)) c = &x;
  if (!c) return sendJson(req, "{\"ok\":false,\"error\":\"unknown var\"}", "400 Bad Request");
  int value = atoi(val);
  if (value < c->lo || value > c->hi) return sendJson(req, "{\"ok\":false,\"error\":\"value out of range\"}", "400 Bad Request");
  sensor_t* s = esp_camera_sensor_get();
  int r;
  xSemaphoreTake(camMutex, portMAX_DELAY);
  if (!strcmp(var, "gainceiling")) r = s->set_gainceiling(s, (gainceiling_t)value);
  else { SetFn f = setterFor(s, var); r = f ? f(s, value) : -1; }
  xSemaphoreGive(camMutex);
  return sendJson(req, r == 0 ? "{\"ok\":true}" : "{\"ok\":false,\"error\":\"sensor rejected value\"}");
}

static esp_err_t rebootHandler(httpd_req_t* req) {
  sendJson(req, "{\"ok\":true}");
  delay(200);
  ESP.restart();
  return ESP_OK;
}

static const char INDEX_HTML[] PROGMEM = R"HTML(<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>VisionVoice camera</title><style>body{font-family:sans-serif;margin:16px;background:#111;color:#eee}img{max-width:100%;border:1px solid #444}
a,button{font-size:18px;margin:4px 8px 4px 0;padding:8px 12px}</style></head><body>
<h2>VisionVoice camera</h2><p>Use this live view to aim the camera at the page. Close this page before capturing from the app.</p>
<img id="v"><p><button onclick="flip('vflip')">Flip vertical</button><button onclick="flip('hmirror')">Mirror</button>
<a id="c" target="_blank">Full-resolution photo</a> <a href="/status" target="_blank">Status</a></p>
<script>const h=location.hostname;document.getElementById('v').src='http://'+h+':81/stream';
document.getElementById('c').href='/capture?size=UXGA&quality=10';const st={vflip:0,hmirror:0};
function flip(k){st[k]^=1;fetch('/control?var='+k+'&val='+st[k]);}</script></body></html>)HTML";

static esp_err_t indexHandler(httpd_req_t* req) {
  httpd_resp_set_type(req, "text/html");
  return httpd_resp_send(req, INDEX_HTML, strlen(INDEX_HTML));
}

#define PART_BOUNDARY "vvframe"
static esp_err_t streamHandler(httpd_req_t* req) {
  if (!camOk) return sendJson(req, "{\"error\":\"camera not initialised\"}", "503 Service Unavailable");
  httpd_resp_set_type(req, "multipart/x-mixed-replace;boundary=" PART_BOUNDARY);
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  char part[96];
  // live view uses VGA so it is smooth; the next capture switches size back
  xSemaphoreTake(camMutex, portMAX_DELAY);
  sensor_t* s = esp_camera_sensor_get();
  if (s->status.framesize != FRAMESIZE_VGA) s->set_framesize(s, FRAMESIZE_VGA);
  xSemaphoreGive(camMutex);
  esp_err_t r = ESP_OK;
  while (r == ESP_OK) {
    if (xSemaphoreTake(camMutex, pdMS_TO_TICKS(2000)) != pdTRUE) continue;
    camera_fb_t* fb = esp_camera_fb_get();
    if (!fb) { xSemaphoreGive(camMutex); r = ESP_FAIL; break; }
    size_t hl = snprintf(part, sizeof(part), "\r\n--" PART_BOUNDARY "\r\nContent-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n", fb->len);
    r = httpd_resp_send_chunk(req, part, hl);
    if (r == ESP_OK) r = httpd_resp_send_chunk(req, (const char*)fb->buf, fb->len);
    esp_camera_fb_return(fb);
    xSemaphoreGive(camMutex);
    delay(30);  // let captures interleave
  }
  return r;
}

static void startHttp() {
  httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
  cfg.server_port = 80;
  cfg.ctrl_port = 32768;
  cfg.max_uri_handlers = 8;
  cfg.stack_size = 8192;
  httpd_uri_t uris[] = {
    {"/", HTTP_GET, indexHandler, nullptr},
    {"/status", HTTP_GET, statusHandler, nullptr},
    {"/capture", HTTP_GET, captureHandler, nullptr},
    {"/control", HTTP_GET, controlHandler, nullptr},
    {"/reboot", HTTP_GET, rebootHandler, nullptr},
  };
  if (httpd_start(&httpServer, &cfg) == ESP_OK)
    for (auto& u : uris) httpd_register_uri_handler(httpServer, &u);
  httpd_config_t scfg = HTTPD_DEFAULT_CONFIG();
  scfg.server_port = 81;
  scfg.ctrl_port = 32769;
  scfg.stack_size = 8192;
  httpd_uri_t su = {"/stream", HTTP_GET, streamHandler, nullptr};
  if (httpd_start(&streamServer, &scfg) == ESP_OK) httpd_register_uri_handler(streamServer, &su);
}

// ---------------- Wi-Fi / discovery / serial ----------------
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
    Serial.printf("[wifi] connected to %s, IP %s, RSSI %d\n", WiFi.SSID().c_str(), WiFi.localIP().toString().c_str(), WiFi.RSSI());
    MDNS.end();
    if (MDNS.begin(DEVICE_HOSTNAME)) MDNS.addService("http", "tcp", 80);
    udp.stop();
    udp.begin(DISCOVERY_PORT);
    Serial.printf("[ready] open http://%s/ to aim the camera\n", WiFi.localIP().toString().c_str());
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
  String reply = String("{\"vv\":1,\"role\":\"cam\",\"id\":\"") + deviceId + "\",\"fw\":\"" FW_VERSION "\",\"ip\":\"" +
                 WiFi.localIP().toString() + "\",\"port\":80,\"stream_port\":81}";
  udp.beginPacket(udp.remoteIP(), udp.remotePort());
  udp.write((const uint8_t*)reply.c_str(), reply.length());
  udp.endPacket();
}

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
      prefs.begin("vv", false); prefs.clear(); prefs.end(); Serial.println("cleared, rebooting"); delay(200); ESP.restart();
    } else if (line == "info") {
      Serial.printf("id=%s fw=%s wifi=%s ip=%s rssi=%d camera_ok=%d psram=%d captures=%lu failures=%lu\n", deviceId.c_str(),
                    FW_VERSION, WiFi.status() == WL_CONNECTED ? WiFi.SSID().c_str() : "(none)",
                    WiFi.localIP().toString().c_str(), WiFi.RSSI(), camOk, hasPsram, (unsigned long)captures, (unsigned long)failures);
    } else if (line == "reboot") { ESP.restart(); }
    else if (line.length()) Serial.println("commands: ssid=NAME  pass=PASSWORD  save  clear  info  reboot");
    line = "";
  }
}

static void updateStatusLed() {
  // active LOW. fast blink = no Wi-Fi, short pulse every 3 s = OK, on for 0.2 s after a capture
  uint32_t t = millis();
  bool on;
  if (!camOk) on = (t % 400) < 200;
  else if (WiFi.status() != WL_CONNECTED) on = (t % 200) < 100;
  else if (t - lastCaptureMs < 200 && lastCaptureMs) on = true;
  else on = (t % 3000) < 60;
  digitalWrite(PIN_STATUS_LED, on ? LOW : HIGH);
}

void setup() {
  Serial.begin(115200);
  delay(200);
  uint64_t mac = ESP.getEfuseMac();
  char idbuf[24];
  snprintf(idbuf, sizeof(idbuf), "vv-cam-%02x%02x%02x", (uint8_t)(mac >> 24), (uint8_t)(mac >> 32), (uint8_t)(mac >> 40));
  deviceId = idbuf;
  Serial.printf("\n[boot] VisionVoice camera %s fw %s\n", deviceId.c_str(), FW_VERSION);
  pinMode(PIN_STATUS_LED, OUTPUT);
  digitalWrite(PIN_STATUS_LED, HIGH);
  ledcAttachChannel(PIN_FLASH_LED, 5000, 8, FLASH_LEDC_CHANNEL);
  setFlash(0);
  camMutex = xSemaphoreCreateMutex();
  camOk = initCamera();
  setupWifi();
  startHttp();
  Serial.println("[help] type 'info' in the Serial Monitor");
}

void loop() {
  serviceSerial();
  serviceWifi();
  if (WiFi.status() == WL_CONNECTED) serviceDiscovery();
  if (!camOk && millis() - lastCamRetry > 10000) {  // keep retrying so a reseated ribbon recovers
    lastCamRetry = millis();
    esp_camera_deinit();
    camOk = initCamera();
  }
  updateStatusLed();
  delay(5);
}
