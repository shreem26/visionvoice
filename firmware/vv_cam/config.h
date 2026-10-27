// ===== VisionVoice camera board — settings =====
// Fill in the Wi-Fi lines BEFORE uploading. Up to 3 networks.
// (You can also change Wi-Fi later WITHOUT reflashing: see HARDWARE.md.)

#pragma once

#define WIFI_SSID_1 "oplus_co_apsayg"

#define WIFI_PASS_1 "hehe2006"
#define WIFI_SSID_2 ""
#define WIFI_PASS_2 ""
#define WIFI_SSID_3 ""
#define WIFI_PASS_3 ""

#define DEVICE_HOSTNAME "vv-cam"   // reachable as vv-cam.local

// ESP32-S3-WROOM CAM: these board has no flash LED, so these are free GPIOs.
// Wire an optional LED (with a resistor) to each, or ignore them.
#define PIN_FLASH_LED  14    // optional white LED
#define PIN_STATUS_LED  2    // optional status LED, active LOW
