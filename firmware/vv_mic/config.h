// ===== VisionVoice microphone board — settings =====
// Copy this file's Wi-Fi lines and fill them in BEFORE uploading.
// You can list up to 3 networks; the board joins whichever is in range.
// (You can also change Wi-Fi later WITHOUT reflashing: see HARDWARE.md,
//  "Changing Wi-Fi without reflashing".)

#pragma once

#define WIFI_SSID_1 "oplus_co_apsayg"
#define WIFI_PASS_1 "hehe2006"
#define WIFI_SSID_2 ""
#define WIFI_PASS_2 ""
#define WIFI_SSID_3 ""
#define WIFI_PASS_3 ""

// ---- Pins (match HARDWARE.md wiring; change only if you wired differently) ----
#define PIN_I2S_SCK   32   // INMP441 SCK
#define PIN_I2S_WS    25   // INMP441 WS
#define PIN_I2S_SD    33   // INMP441 SD
#define PIN_BTN_BOOT   0   // on-board BOOT button (push-to-talk)
#define PIN_BTN_EXT   27   // optional external push button (other leg to GND)
#define PIN_LED        2   // on-board blue LED on most ESP32 DevKit V1 boards
#define LED_ACTIVE_HIGH 1  // 1 if the LED turns on with HIGH (normal for DevKit)

#define DEVICE_HOSTNAME "vv-mic"   // reachable as vv-mic.local
