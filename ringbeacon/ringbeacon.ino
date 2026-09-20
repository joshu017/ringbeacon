// BLE advertising name for this sketch. See AGENTS.md "BLE device names".
#define BLE_DEVICE_NAME "RingBeacon"



/*
 * RingBeacon — LED Ring Controller
 *
 * Firmware for ESP32-C3 driving a WS2812B LED ring via BLE.
 * LED ring data is connected to GPIO 8.
 * Exposes two BLE characteristics:
 *   - ID (read/write): persistent ring identifier stored in NVS
 *   - Control (write):  JSON animation commands
 *
 * Dependencies:
 *   - NimBLE-Arduino v2.x (h2zero)
 *   - NeoPixelBus (Makuna)
 *   - ArduinoJson v7.x (bblanchon)
 *   - ESP32 Arduino core v3.x (Espressif)
 */

#include <NimBLEDevice.h>
#include "esp32-hal-bt-mem.h"  // prevents BT memory release crash on core 3.x
#include <NeoPixelBus.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <esp_mac.h>

// ── Hardware ────────────────────────────────────────────────────────────────
#if !defined(CONFIG_IDF_TARGET_ESP32C3)
#error "RingBeacon supports ESP32-C3 only"
#endif
#define LED_PIN 8
#define LED_COUNT 8

// ── BLE ─────────────────────────────────────────────────────────────────────
#define SERVICE_UUID   "c0de1234-beef-cafe-1234-000000000001"
#define CHAR_ID_UUID   "c0de1234-beef-cafe-1234-000000000002"
#define CHAR_CTRL_UUID "c0de1234-beef-cafe-1234-000000000003"

#define NVS_NAMESPACE "ringbeacon"
#define NVS_KEY_ID    "id"

#define JSON_BUF_MAX    1024
#define JSON_TIMEOUT_MS 5000

// ── Debug ───────────────────────────────────────────────────────────────────
#define DEBUG
#ifdef DEBUG
  #define dbg(fmt, ...) do { Serial.printf(fmt, ##__VA_ARGS__); Serial.flush(); } while(0)
#else
  #define dbg(fmt, ...) Serial.printf(fmt, ##__VA_ARGS__)
#endif

// ── Strip ───────────────────────────────────────────────────────────────────
// NeoPixelBus 2.8.x RMT methods (both legacy Rmt0/1 and "N" channel) still
// call the legacy rmt_driver_install() which conflicts with Arduino core 3.x
// (IDF 5.x driver_ng). BitBang avoids RMT entirely — for 16 pixels the
// ~0.5ms interrupt-disabled window per frame is negligible.
NeoPixelBus<NeoGrbFeature, NeoEsp32BitBangWs2812xMethod> strip(LED_COUNT, LED_PIN);

// ── Animation Commands ──────────────────────────────────────────────────────
enum AnimCmd : uint8_t {
    CMD_OFF = 0,
    CMD_SOLID,
    CMD_BLINK,
    CMD_BREATHE,
    CMD_ROTATE,
    CMD_CHASE,
    CMD_RAINBOW,
    CMD_WIPE,
    CMD_SPARKLE,
    CMD_COMET,
    CMD_BOUNCE,
    CMD_FIRE,
    CMD_GRADIENT,
    CMD_STROBE,
    CMD_PULSE,
    CMD_COUNT
};

struct AnimState {
    AnimCmd  cmd;
    RgbColor color;
    RgbColor color2;
    uint32_t speed_ms;
    uint32_t timeout_ms;     // total time limit; 0 = infinite
    uint32_t start_ms;
    uint16_t cycles;         // 0 = infinite, N = stop after N complete cycles
    uint8_t  brightness;     // 0-255
    AnimEaseFunction easeFunc;
    bool     reverse;

    // blink
    float    duty;           // 0.0-1.0 on-time ratio

    // breathe
    uint8_t  min_bright;
    uint8_t  max_bright;

    // rotate / bounce / chase
    uint8_t  width;
    uint8_t  count;          // rotate: number of beams
    uint8_t  trail;          // trail fade length
    uint8_t  trail_dir;     // 0=back, 1=front, 2=both

    // chase
    uint8_t  spacing;

    // rainbow
    uint8_t  saturation;
    uint8_t  spread;         // how many full rainbows

    // sparkle
    float    density;        // 0.0-1.0
    uint8_t  fade_speed;

    // comet
    uint8_t  tail_length;
    float    trail_decay;

    // fire
    uint8_t  cooling;
    uint8_t  sparking;

    // pulse
    uint8_t  origin;

    // strobe
    uint8_t  flashes;        // per burst
    uint16_t pause_ms;
};

// ── Globals ─────────────────────────────────────────────────────────────────
String g_deviceId;
NimBLECharacteristic* g_pIdChar = nullptr;

// BLE -> loop() handoff
volatile bool g_stateReady  = false;
AnimState     g_pendingState;
AnimState     g_activeState;

// JSON accumulation buffer
char          g_jsonBuf[JSON_BUF_MAX];
uint16_t      g_jsonLen       = 0;
unsigned long g_lastWriteMs   = 0;

// Animation expiry tracking
bool g_animExpired = false;

// Per-frame state for stateful animations
float   g_sparkleBright[LED_COUNT];
uint8_t g_fireHeat[LED_COUNT];
AnimCmd g_lastRenderedCmd = CMD_OFF;

// ── Helpers ─────────────────────────────────────────────────────────────────

// duration is canonical; speed is only a fallback for older clients.
inline uint32_t cycleDuration(uint32_t duration, uint32_t speed, bool hasDuration) {
    uint32_t value = hasDuration ? duration : speed;
    return value > 0 ? value : 1;
}

inline bool animationExpired(uint32_t elapsed, uint32_t cycleMs,
                             uint16_t cycles, uint32_t timeoutMs) {
    return (timeoutMs > 0 && elapsed >= timeoutMs) ||
           (cycles > 0 && elapsed / cycleMs >= cycles);
}

RgbColor scaleColor(RgbColor c, uint8_t brightness) {
    return RgbColor(
        (uint8_t)((uint16_t)c.R * brightness / 255),
        (uint8_t)((uint16_t)c.G * brightness / 255),
        (uint8_t)((uint16_t)c.B * brightness / 255)
    );
}

RgbColor blendColors(RgbColor a, RgbColor b, float ratio) {
    return RgbColor(
        (uint8_t)(a.R + (b.R - a.R) * ratio),
        (uint8_t)(a.G + (b.G - a.G) * ratio),
        (uint8_t)(a.B + (b.B - a.B) * ratio)
    );
}

// ── Easing Lookup ───────────────────────────────────────────────────────────

AnimEaseFunction parseEaseName(const char* name) {
    if (!name) return NeoEase::Linear;
    if (strcmp(name, "quadraticIn")      == 0) return NeoEase::QuadraticIn;
    if (strcmp(name, "quadraticOut")     == 0) return NeoEase::QuadraticOut;
    if (strcmp(name, "quadraticInOut")   == 0) return NeoEase::QuadraticInOut;
    if (strcmp(name, "quadraticCenter")  == 0) return NeoEase::QuadraticCenter;
    if (strcmp(name, "cubicIn")         == 0) return NeoEase::CubicIn;
    if (strcmp(name, "cubicOut")        == 0) return NeoEase::CubicOut;
    if (strcmp(name, "cubicInOut")      == 0) return NeoEase::CubicInOut;
    if (strcmp(name, "cubicCenter")     == 0) return NeoEase::CubicCenter;
    if (strcmp(name, "quarticIn")       == 0) return NeoEase::QuarticIn;
    if (strcmp(name, "quarticOut")      == 0) return NeoEase::QuarticOut;
    if (strcmp(name, "quarticInOut")    == 0) return NeoEase::QuarticInOut;
    if (strcmp(name, "quinticIn")       == 0) return NeoEase::QuinticIn;
    if (strcmp(name, "quinticOut")      == 0) return NeoEase::QuinticOut;
    if (strcmp(name, "quinticInOut")    == 0) return NeoEase::QuinticInOut;
    if (strcmp(name, "sinusoidalIn")    == 0) return NeoEase::SinusoidalIn;
    if (strcmp(name, "sinusoidalOut")   == 0) return NeoEase::SinusoidalOut;
    if (strcmp(name, "sinusoidalInOut") == 0) return NeoEase::SinusoidalInOut;
    if (strcmp(name, "exponentialIn")   == 0) return NeoEase::ExponentialIn;
    if (strcmp(name, "exponentialOut")  == 0) return NeoEase::ExponentialOut;
    if (strcmp(name, "exponentialInOut")== 0) return NeoEase::ExponentialInOut;
    if (strcmp(name, "circularIn")      == 0) return NeoEase::CircularIn;
    if (strcmp(name, "circularOut")     == 0) return NeoEase::CircularOut;
    if (strcmp(name, "circularInOut")   == 0) return NeoEase::CircularInOut;
    if (strcmp(name, "gamma")           == 0) return NeoEase::Gamma;
    return NeoEase::Linear;
}

// ── Command Name Lookup ─────────────────────────────────────────────────────

AnimCmd parseCmdName(const char* name) {
    if (!name) return CMD_OFF;
    if (strcmp(name, "off")      == 0) return CMD_OFF;
    if (strcmp(name, "solid")    == 0) return CMD_SOLID;
    if (strcmp(name, "blink")    == 0) return CMD_BLINK;
    if (strcmp(name, "breathe")  == 0) return CMD_BREATHE;
    if (strcmp(name, "rotate")   == 0) return CMD_ROTATE;
    if (strcmp(name, "chase")    == 0) return CMD_CHASE;
    if (strcmp(name, "rainbow")  == 0) return CMD_RAINBOW;
    if (strcmp(name, "wipe")     == 0) return CMD_WIPE;
    if (strcmp(name, "sparkle")  == 0) return CMD_SPARKLE;
    if (strcmp(name, "comet")    == 0) return CMD_COMET;
    if (strcmp(name, "bounce")   == 0) return CMD_BOUNCE;
    if (strcmp(name, "fire")     == 0) return CMD_FIRE;
    if (strcmp(name, "gradient") == 0) return CMD_GRADIENT;
    if (strcmp(name, "strobe")   == 0) return CMD_STROBE;
    if (strcmp(name, "pulse")    == 0) return CMD_PULSE;
    return CMD_OFF;
}

// ── JSON Parser ─────────────────────────────────────────────────────────────

void parseCommand(JsonDocument& doc, AnimState& s) {
    // Command
    s.cmd = parseCmdName(doc["cmd"] | "off");

    // Primary color: RGB array or HSV array
    if (doc["color"].is<JsonArray>()) {
        JsonArray arr = doc["color"].as<JsonArray>();
        s.color = RgbColor(arr[0] | 255, arr[1] | 255, arr[2] | 255);
    } else if (doc["hsv"].is<JsonArray>()) {
        JsonArray arr = doc["hsv"].as<JsonArray>();
        float h = (arr[0] | 0) / 360.0f;
        float sv = (arr[1] | 100) / 100.0f;
        float v = (arr[2] | 100) / 100.0f;
        s.color = RgbColor(HsbColor(h, sv, v));
    } else {
        s.color = RgbColor(255, 255, 255);
    }

    // Secondary color: RGB array or HSV array (color2 takes precedence over hsv2)
    if (doc["color2"].is<JsonArray>()) {
        JsonArray arr = doc["color2"].as<JsonArray>();
        s.color2 = RgbColor(arr[0] | 0, arr[1] | 0, arr[2] | 0);
    } else if (doc["hsv2"].is<JsonArray>()) {
        JsonArray arr = doc["hsv2"].as<JsonArray>();
        float h = (arr[0] | 0) / 360.0f;
        float sv = (arr[1] | 100) / 100.0f;
        float v = (arr[2] | 100) / 100.0f;
        s.color2 = RgbColor(HsbColor(h, sv, v));
    } else {
        s.color2 = RgbColor(0, 0, 0);
    }

    // Common params
    // duration is per-cycle time; speed remains a legacy alias.
    s.speed_ms    = cycleDuration(doc["duration"] | 1000U, doc["speed"] | 1000U,
                                  !doc["duration"].isNull());
    s.timeout_ms  = doc["timeout"] | 0U;
    s.cycles      = doc["cycles"]     | 0;   // 0 = infinite
    s.brightness  = doc["brightness"] | 255;
    s.reverse     = doc["reverse"]    | false;

    // Easing
    s.easeFunc = parseEaseName(doc["easing"] | "linear");

    // Per-command params with sensible defaults
    s.duty         = doc["duty"]         | 0.5f;
    s.min_bright   = doc["min_bright"]   | 0;
    s.max_bright   = doc["max_bright"]   | 255;
    s.width        = doc["width"]        | 3;
    s.count        = doc["count"]        | 1;
    s.trail        = doc["trail"]        | 4;
    const char* td = doc["trail_dir"]    | "back";
    if      (strcmp(td, "front") == 0) s.trail_dir = 1;
    else if (strcmp(td, "both")  == 0) s.trail_dir = 2;
    else                               s.trail_dir = 0;  // "back" (default)
    s.spacing      = doc["spacing"]      | 3;
    s.saturation   = doc["saturation"]   | 255;
    s.spread       = doc["spread"]       | 1;
    s.density      = doc["density"]      | 0.3f;
    s.fade_speed   = doc["fade_speed"]   | 20;
    s.tail_length  = doc["tail_length"]  | 6;
    s.trail_decay  = doc["trail_decay"]  | 0.65f;
    s.cooling      = doc["cooling"]      | 55;
    s.sparking     = doc["sparking"]     | 120;
    s.origin       = doc["origin"]       | 0;
    s.flashes      = doc["flashes"]      | 3;
    s.pause_ms     = doc["pause"]        | 200;
}

// ── NVS ID Persistence ──────────────────────────────────────────────────────

void loadId() {
    uint8_t mac[6];
    esp_read_mac(mac, ESP_MAC_BT);
    char def[16];
    snprintf(def, sizeof(def), "ring-%02x%02x", mac[4], mac[5]);

    Preferences prefs;
    prefs.begin(NVS_NAMESPACE, true);
    g_deviceId = prefs.getString(NVS_KEY_ID, def);
    prefs.end();
    dbg("ID: %s\n", g_deviceId.c_str());
}

void saveId(const String& newId) {
    Preferences prefs;
    prefs.begin(NVS_NAMESPACE, false);
    prefs.putString(NVS_KEY_ID, newId);
    prefs.end();
    g_deviceId = newId;
    dbg("ID saved: %s\n", g_deviceId.c_str());
}

// ── BLE Callbacks ───────────────────────────────────────────────────────────

class ServerCallbacks : public NimBLEServerCallbacks {
    void onConnect(NimBLEServer* pServer, NimBLEConnInfo& connInfo) override {
        dbg("Client connected\n");
    }
    void onDisconnect(NimBLEServer* pServer, NimBLEConnInfo& connInfo, int reason) override {
        dbg("Client disconnected (reason=%d)\n", reason);
        NimBLEDevice::startAdvertising();
    }
};

class IdCallbacks : public NimBLECharacteristicCallbacks {
    void onWrite(NimBLECharacteristic* pChar, NimBLEConnInfo& connInfo) override {
        NimBLEAttValue val = pChar->getValue();
        if (val.size() == 0 || val.size() > 32) return;

        String newId = String((const char*)val.data(), val.size());
        saveId(newId);

        // Update readable value
        if (g_pIdChar) g_pIdChar->setValue(newId.c_str());

        dbg("ID changed to: %s\n", newId.c_str());
    }
};

class ControlCallbacks : public NimBLECharacteristicCallbacks {
    void onWrite(NimBLECharacteristic* pChar, NimBLEConnInfo& connInfo) override {
        NimBLEAttValue val = pChar->getValue();
        uint16_t len = val.size();
        if (len == 0) return;

        unsigned long now = millis();

        // Timeout: stale partial payload
        if (g_jsonLen > 0 && (now - g_lastWriteMs) > JSON_TIMEOUT_MS) {
            g_jsonLen = 0;
        }
        g_lastWriteMs = now;

        // Accumulate, guard overflow
        if (g_jsonLen + len >= JSON_BUF_MAX) {
            dbg("JSON buffer overflow, reset\n");
            g_jsonLen = 0;
            return;
        }
        memcpy(g_jsonBuf + g_jsonLen, val.data(), len);
        g_jsonLen += len;

        // Try to parse
        JsonDocument doc;
        DeserializationError err = deserializeJson(doc, g_jsonBuf, g_jsonLen);
        if (err == DeserializationError::Ok) {
            parseCommand(doc, g_pendingState);
            g_stateReady = true;
            g_jsonLen = 0;
            dbg("CMD: %s  cycle_ms=%lu  timeout=%lu  cycles=%u  brightness=%u\n",
                doc["cmd"] | "off",
                (unsigned long)g_pendingState.speed_ms,
                (unsigned long)g_pendingState.timeout_ms,
                g_pendingState.cycles,
                g_pendingState.brightness);
        } else if (err != DeserializationError::IncompleteInput) {
            // Corrupt — discard
            dbg("JSON error: %s\n", err.c_str());
            g_jsonLen = 0;
        }
        // IncompleteInput: keep accumulating
    }
};

// ── Animation Renderers ─────────────────────────────────────────────────────

void resetFrameState(AnimCmd newCmd) {
    if (g_lastRenderedCmd != newCmd) {
        memset(g_sparkleBright, 0, sizeof(g_sparkleBright));
        memset(g_fireHeat, 0, sizeof(g_fireHeat));
        g_lastRenderedCmd = newCmd;
    }
}

void anim_off() {
    strip.ClearTo(RgbColor(0));
}

void anim_solid(const AnimState& s) {
    strip.ClearTo(scaleColor(s.color, s.brightness));
}

void anim_blink(const AnimState& s, float t) {
    if (t < s.duty) {
        strip.ClearTo(scaleColor(s.color, s.brightness));
    } else {
        if (s.color2.R || s.color2.G || s.color2.B) {
            strip.ClearTo(scaleColor(s.color2, s.brightness));
        } else {
            strip.ClearTo(RgbColor(0));
        }
    }
}

void anim_breathe(const AnimState& s, float t) {
    float breath = sin(t * PI);  // 0 -> 1 -> 0 over one cycle
    bool hasColor2 = (s.color2.R || s.color2.G || s.color2.B);
    if (hasColor2) {
        // Blend between color2 (at rest) and color (at peak)
        RgbColor c = blendColors(s.color2, s.color, breath);
        strip.ClearTo(scaleColor(c, s.brightness));
    } else {
        // Original behavior: pulse brightness
        uint8_t b = s.min_bright + (uint8_t)((s.max_bright - s.min_bright) * breath);
        strip.ClearTo(scaleColor(s.color, (uint8_t)((uint16_t)b * s.brightness / 255)));
    }
}

// Helper: additive pixel set (blends with existing color)
void addPixel(int idx, RgbColor color, uint8_t brightness) {
    RgbColor existing = strip.GetPixelColor(idx);
    RgbColor added = scaleColor(color, brightness);
    strip.SetPixelColor(idx, RgbColor(
        min(255, existing.R + added.R),
        min(255, existing.G + added.G),
        min(255, existing.B + added.B)
    ));
}

float trailIntensity(uint8_t j, uint8_t trail) {
    float intensity = 1.0f - (float)(j + 1) / (trail + 1);
    return intensity * intensity;  // quadratic falloff
}

void anim_rotate(const AnimState& s, float t) {
    strip.ClearTo(RgbColor(0));
    for (uint8_t beam = 0; beam < s.count; beam++) {
        float beamOffset = (float)beam / s.count;
        int headIdx = (int)(fmod(t + beamOffset, 1.0f) * LED_COUNT);

        // Draw head pixels at full intensity
        for (uint8_t j = 0; j < s.width; j++) {
            int idx = (headIdx - j + LED_COUNT) % LED_COUNT;
            addPixel(idx, s.color, s.brightness);
        }

        // Trail behind the beam (back of head, opposite to direction of travel)
        if (s.trail_dir == 0 || s.trail_dir == 2) {
            for (uint8_t j = 0; j < s.trail; j++) {
                int idx = (headIdx - s.width - j + LED_COUNT * 2) % LED_COUNT;
                uint8_t b = (uint8_t)(trailIntensity(j, s.trail) * s.brightness);
                addPixel(idx, s.color, b);
            }
        }

        // Trail in front of the beam (ahead of head, direction of travel)
        if (s.trail_dir == 1 || s.trail_dir == 2) {
            for (uint8_t j = 0; j < s.trail; j++) {
                int idx = (headIdx + 1 + j) % LED_COUNT;
                uint8_t b = (uint8_t)(trailIntensity(j, s.trail) * s.brightness);
                addPixel(idx, s.color, b);
            }
        }
    }
}

void anim_chase(const AnimState& s, float t) {
    int offset = (int)(t * s.spacing);
    for (int i = 0; i < LED_COUNT; i++) {
        int pos = (i + offset) % s.spacing;
        if (pos < s.width) {
            strip.SetPixelColor(i, scaleColor(s.color, s.brightness));
        } else {
            if (s.color2.R || s.color2.G || s.color2.B) {
                strip.SetPixelColor(i, scaleColor(s.color2, s.brightness));
            } else {
                strip.SetPixelColor(i, RgbColor(0));
            }
        }
    }
}

void anim_rainbow(const AnimState& s, float t) {
    for (int i = 0; i < LED_COUNT; i++) {
        float hue = fmod((float)i / LED_COUNT * s.spread + t, 1.0f);
        HsbColor hsb(hue, s.saturation / 255.0f, s.brightness / 255.0f);
        strip.SetPixelColor(i, RgbColor(hsb));
    }
}

void anim_wipe(const AnimState& s, float t) {
    int filled = (int)(t * LED_COUNT);
    for (int i = 0; i < LED_COUNT; i++) {
        if (i <= filled) {
            strip.SetPixelColor(i, scaleColor(s.color, s.brightness));
        } else {
            if (s.color2.R || s.color2.G || s.color2.B) {
                strip.SetPixelColor(i, scaleColor(s.color2, s.brightness));
            } else {
                strip.SetPixelColor(i, RgbColor(0));
            }
        }
    }
}

void anim_sparkle(const AnimState& s, unsigned long now) {
    static unsigned long lastFrame = 0;
    float dt = (now - lastFrame) / 1000.0f;
    lastFrame = now;
    if (dt > 0.1f) dt = 0.1f;  // clamp after cmd change

    // Decay existing sparkles
    for (int i = 0; i < LED_COUNT; i++) {
        g_sparkleBright[i] -= s.fade_speed * dt * 10.0f;
        if (g_sparkleBright[i] < 0) g_sparkleBright[i] = 0;
    }

    // Ignite new sparkles
    for (int i = 0; i < LED_COUNT; i++) {
        if ((random(1000) / 1000.0f) < s.density * dt * 10.0f) {
            g_sparkleBright[i] = 255.0f;
        }
    }

    for (int i = 0; i < LED_COUNT; i++) {
        uint8_t b = (uint8_t)((g_sparkleBright[i] / 255.0f) * s.brightness);
        strip.SetPixelColor(i, scaleColor(s.color, b));
    }
}

void anim_comet(const AnimState& s, float t) {
    strip.ClearTo(RgbColor(0));
    float headPos = t * LED_COUNT;
    for (int j = 0; j < s.tail_length + 1; j++) {
        int idx = ((int)headPos - j + LED_COUNT * 2) % LED_COUNT;
        float intensity = (j == 0) ? 1.0f : pow(s.trail_decay, j);
        uint8_t b = (uint8_t)(intensity * s.brightness);
        strip.SetPixelColor(idx, scaleColor(s.color, b));
    }
}

void anim_bounce(const AnimState& s, float t) {
    strip.ClearTo(RgbColor(0));
    // t=0..0.5 moves forward, t=0.5..1 moves back
    float pos;
    if (t < 0.5f) {
        pos = t * 2.0f * (LED_COUNT - s.width);
    } else {
        pos = (1.0f - (t - 0.5f) * 2.0f) * (LED_COUNT - s.width);
    }
    int head = (int)pos;
    for (int j = 0; j < s.width + s.trail; j++) {
        int idx = head - j;
        if (idx < 0 || idx >= LED_COUNT) continue;
        float intensity;
        if (j < s.width) {
            intensity = 1.0f;
        } else {
            intensity = 1.0f - (float)(j - s.width + 1) / (s.trail + 1);
            intensity *= intensity;
        }
        uint8_t b = (uint8_t)(intensity * s.brightness);
        strip.SetPixelColor(idx, scaleColor(s.color, b));
    }
    // Also draw trail ahead of head for bounce-back
    for (int j = 0; j < s.trail; j++) {
        int idx = head + s.width + j;
        if (idx < 0 || idx >= LED_COUNT) continue;
        float intensity = 1.0f - (float)(j + 1) / (s.trail + 1);
        intensity *= intensity;
        uint8_t b = (uint8_t)(intensity * s.brightness);
        RgbColor existing = strip.GetPixelColor(idx);
        RgbColor added = scaleColor(s.color, b);
        strip.SetPixelColor(idx, RgbColor(
            min(255, existing.R + added.R),
            min(255, existing.G + added.G),
            min(255, existing.B + added.B)
        ));
    }
}

void anim_fire(const AnimState& s) {
    // Step 1: cool down every cell
    for (int i = 0; i < LED_COUNT; i++) {
        int cool = random(0, ((s.cooling * 10) / LED_COUNT) + 2);
        g_fireHeat[i] = (cool >= g_fireHeat[i]) ? 0 : g_fireHeat[i] - cool;
    }

    // Step 2: heat rises (drift upward)
    for (int i = LED_COUNT - 1; i >= 2; i--) {
        g_fireHeat[i] = (g_fireHeat[i - 1] + g_fireHeat[i - 2] + g_fireHeat[i - 2]) / 3;
    }

    // Step 3: random sparking at bottom
    if (random(255) < s.sparking) {
        int y = random(3);
        g_fireHeat[y] = (uint8_t)min(255L, (long)g_fireHeat[y] + random(160, 255));
    }

    // Step 4: map heat to color
    for (int i = 0; i < LED_COUNT; i++) {
        uint8_t h = g_fireHeat[i];
        RgbColor c;
        if (h < 85) {
            c = RgbColor(h * 3, 0, 0);                           // black -> red
        } else if (h < 170) {
            c = RgbColor(255, (h - 85) * 3, 0);                  // red -> yellow
        } else {
            c = RgbColor(255, 255, (h - 170) * 3);               // yellow -> white
        }
        strip.SetPixelColor(i, scaleColor(c, s.brightness));
    }
}

void anim_gradient(const AnimState& s, float t) {
    for (int i = 0; i < LED_COUNT; i++) {
        float ratio = fmod((float)i / LED_COUNT + t, 1.0f);
        // Ping-pong for smooth wrapping
        if (ratio > 0.5f) ratio = 1.0f - ratio;
        ratio *= 2.0f;
        RgbColor c = blendColors(s.color, s.color2, ratio);
        strip.SetPixelColor(i, scaleColor(c, s.brightness));
    }
}

void anim_strobe(const AnimState& s, float t) {
    // Divide cycle into flash phase and pause phase
    float flashPhase = (float)s.flashes / (s.flashes + 1);
    if (t < flashPhase) {
        // Within flash phase, alternate on/off per sub-flash
        float subT = fmod(t / flashPhase * s.flashes, 1.0f);
        if (subT < 0.5f) {
            strip.ClearTo(scaleColor(s.color, s.brightness));
        } else {
            strip.ClearTo(RgbColor(0));
        }
    } else {
        strip.ClearTo(RgbColor(0));
    }
}

void anim_pulse(const AnimState& s, float t) {
    strip.ClearTo(RgbColor(0));
    float radius = t * LED_COUNT / 2.0f;
    for (int i = 0; i < LED_COUNT; i++) {
        // Distance on ring (shortest path)
        int rawDist = abs(i - s.origin);
        int dist = min(rawDist, LED_COUNT - rawDist);
        if (dist <= radius) {
            float intensity = 1.0f - (dist / max(radius, 0.01f));
            intensity *= (1.0f - t);  // fade out as pulse expands
            uint8_t b = (uint8_t)(intensity * s.brightness);
            strip.SetPixelColor(i, scaleColor(s.color, b));
        }
    }
}

// ── Main Render Dispatch ────────────────────────────────────────────────────

void renderAnimation(const AnimState& s, float t, unsigned long now) {
    resetFrameState(s.cmd);

    switch (s.cmd) {
        case CMD_OFF:      anim_off();            break;
        case CMD_SOLID:    anim_solid(s);         break;
        case CMD_BLINK:    anim_blink(s, t);      break;
        case CMD_BREATHE:  anim_breathe(s, t);    break;
        case CMD_ROTATE:   anim_rotate(s, t);     break;
        case CMD_CHASE:    anim_chase(s, t);      break;
        case CMD_RAINBOW:  anim_rainbow(s, t);    break;
        case CMD_WIPE:     anim_wipe(s, t);       break;
        case CMD_SPARKLE:  anim_sparkle(s, now);  break;
        case CMD_COMET:    anim_comet(s, t);      break;
        case CMD_BOUNCE:   anim_bounce(s, t);     break;
        case CMD_FIRE:     anim_fire(s);          break;
        case CMD_GRADIENT: anim_gradient(s, t);   break;
        case CMD_STROBE:   anim_strobe(s, t);     break;
        case CMD_PULSE:    anim_pulse(s, t);      break;
        default:           anim_off();            break;
    }
}

// ── Arduino Setup ───────────────────────────────────────────────────────────

void setup() {
    Serial.begin(115200);
    dbg("\nRingBeacon Controller\n");

    // Load persistent ID
    loadId();

    // Initialize LED strip
    strip.Begin();
    strip.ClearTo(RgbColor(0));
    strip.Show();

    // Default animation state
    memset(&g_activeState, 0, sizeof(g_activeState));
    g_activeState.cmd       = CMD_OFF;
    g_activeState.easeFunc  = NeoEase::Linear;
    g_activeState.speed_ms  = 1000;
    g_activeState.brightness = 255;

    // BLE init
    NimBLEDevice::init(BLE_DEVICE_NAME);
    NimBLEDevice::setMTU(512);

    NimBLEServer* pServer = NimBLEDevice::createServer();
    pServer->setCallbacks(new ServerCallbacks());

    NimBLEService* pService = pServer->createService(SERVICE_UUID);

    // ID characteristic: read + write
    g_pIdChar = pService->createCharacteristic( CHAR_ID_UUID, NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::WRITE );
    g_pIdChar->setValue(g_deviceId.c_str());
    g_pIdChar->setCallbacks(new IdCallbacks());

    // Control characteristic: write
    NimBLECharacteristic* pCtrlChar = pService->createCharacteristic( CHAR_CTRL_UUID, NIMBLE_PROPERTY::WRITE );
    pCtrlChar->setCallbacks(new ControlCallbacks());

    pService->start();

    NimBLEAdvertising* pAdv = NimBLEDevice::getAdvertising();
    pAdv->addServiceUUID(SERVICE_UUID);
    pAdv->enableScanResponse(true);  // name goes in scan response (doesn't fit with 128-bit UUID in 31-byte adv packet)
    pAdv->setName(BLE_DEVICE_NAME);
    pAdv->start();

    // Boot indicator: rainbow for 2 cycles then off
    JsonDocument bootCmd;
    bootCmd["cmd"] = "rainbow";
    bootCmd["duration"] = 2500;
    bootCmd["cycles"] = 2;
    bootCmd["brightness"] = 51;
    parseCommand(bootCmd, g_activeState);
    g_activeState.start_ms = millis();
    dbg("BLE: %s ready\n", g_deviceId.c_str());
}

// ── Arduino Main Loop ───────────────────────────────────────────────────────

void loop() {
    delay(10);
    unsigned long now = millis();

    // Consume pending command from BLE task
    if (g_stateReady) {
        g_activeState = g_pendingState;
        g_activeState.start_ms = now;
        g_animExpired = false;
        g_stateReady = false;
        dbg("Active: start_ms=%lu timeout=%lu cycles=%u cycle_ms=%lu\n",
            (unsigned long)g_activeState.start_ms,
            (unsigned long)g_activeState.timeout_ms,
            g_activeState.cycles,
            (unsigned long)g_activeState.speed_ms);
    }

    // Timeout stale JSON buffer
    if (g_jsonLen > 0 && (now - g_lastWriteMs) > JSON_TIMEOUT_MS) {
        g_jsonLen = 0;
    }

    // Already expired — keep trying to push the black frame until CanShow is true
    if (g_animExpired) {
        if (strip.CanShow()) {
            strip.ClearTo(RgbColor(0));
            strip.Show();
        }
        return;
    }

    // Compute cycle progress
    uint32_t elapsed = now - g_activeState.start_ms;
    float cycleMs = (float)max((uint32_t)1, g_activeState.speed_ms);

    // timeout and cycles are independent upper bounds; the first wins.
    if (animationExpired(elapsed, g_activeState.speed_ms,
                         g_activeState.cycles, g_activeState.timeout_ms)) {
        dbg("Expired: elapsed=%lu\n", (unsigned long)elapsed);
        g_animExpired = true;
        strip.ClearTo(RgbColor(0));
        if (strip.CanShow()) strip.Show();
        return;
    }

    float raw = fmod((float)elapsed, cycleMs) / cycleMs;  // 0.0-1.0
    float t = g_activeState.easeFunc ? g_activeState.easeFunc(raw) : raw;
    if (g_activeState.reverse) t = 1.0f - t;

    renderAnimation(g_activeState, t, now);

    if (strip.CanShow()) {
        strip.Show();
    }

}
