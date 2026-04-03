/*
 * environment_narrator — ESP32 sensor node
 *
 * Reads temperature + humidity (DHT11 on GPIO 32) and relative light level
 * (HW-486 photoresistor on GPIO 33, ADC1) every SLEEP_INTERVAL_US microseconds.
 * Publishes a JSON payload to the MQTT topic "environment/sensors" on the
 * Raspberry Pi's local mosquitto broker, then enters deep sleep.
 *
 * Hardware:
 *   MCU:      ESP32-WROOM-32UE DevKitC V4
 *   Temp/RH:  DHT11 (3-pin) — data pin → GPIO 32
 *   Light:    HW-486 photoresistor module — AO pin → GPIO 33 (ADC1, WiFi-safe)
 *
 * Prerequisites:
 *   1. Copy mcu/include/config.h.example to mcu/include/config.h and fill in
 *      your WiFi credentials and Pi IP address.
 *   2. Ensure mosquitto is running on the Pi:
 *        sudo systemctl enable --now mosquitto
 *   3. Build and upload via PlatformIO (VSCode extension or `pio run -t upload`).
 *
 * See docs/mqtt-schema.md for the MQTT topic and payload contract.
 */

#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <DHTesp.h>
#include <ArduinoJson.h>

#include "config.h"

// ── Pin definitions ──────────────────────────────────────────────────────────
#define DHTPIN    32   // DHT11 data pin — digital GPIO, not ADC-constrained
#define LUX_PIN   33   // Photoresistor AO pin — ADC1 channel, safe with WiFi active

// ── MQTT topic ───────────────────────────────────────────────────────────────
// Must match what narrator.py will subscribe to (Pi-side MQTT step).
// Field names are a versioned contract — see docs/mqtt-schema.md.
static const char* MQTT_TOPIC = "environment/sensors";

// ── Globals ──────────────────────────────────────────────────────────────────
DHTesp        dht;
WiFiClient    wifiClient;
PubSubClient  mqttClient(wifiClient);

// ── Helper: enter deep sleep ─────────────────────────────────────────────────
static void goToSleep() {
    Serial.println("[sleep] entering deep sleep for " +
                   String((unsigned long)(SLEEP_INTERVAL_US / 1000000ULL)) + "s");
    Serial.flush();
    mqttClient.disconnect();
    WiFi.disconnect(true);
    WiFi.mode(WIFI_OFF);
    esp_sleep_enable_timer_wakeup(SLEEP_INTERVAL_US);
    esp_deep_sleep_start();
}

// ── Helper: connect WiFi ─────────────────────────────────────────────────────
// Returns true on success, false on timeout. Does not hang indefinitely.
static bool connectWiFi() {
    Serial.print("[wifi] connecting to " + String(WIFI_SSID));
    WiFi.begin(WIFI_SSID, WIFI_PASS);

    unsigned long start = millis();
    while (WiFi.status() != WL_CONNECTED) {
        if (millis() - start >= WIFI_TIMEOUT_MS) {
            Serial.println("\n[wifi] timeout — broker unreachable this cycle");
            return false;
        }
        delay(250);
        Serial.print(".");
    }
    Serial.println("\n[wifi] connected, IP: " + WiFi.localIP().toString());
    return true;
}

// ── Helper: connect MQTT ─────────────────────────────────────────────────────
// Returns true on success, false on failure.
static bool connectMQTT() {
    mqttClient.setServer(MQTT_BROKER_IP, MQTT_PORT);
    Serial.print("[mqtt] connecting to " + String(MQTT_BROKER_IP) + ":" +
                 String(MQTT_PORT));
    if (!mqttClient.connect(MQTT_CLIENT_ID)) {
        Serial.println("\n[mqtt] connection failed (state=" +
                       String(mqttClient.state()) + ")");
        return false;
    }
    Serial.println("\n[mqtt] connected as " + String(MQTT_CLIENT_ID));
    // Tick the PubSubClient state machine once to process any pending CONNACK
    // or broker control packets. In a setup()-only lifecycle loop() never runs,
    // so we must tick manually before publishing.
    mqttClient.loop();
    return true;
}

// ── Helper: read light sensor ────────────────────────────────────────────────
// Averages ADC_SAMPLES reads with a settling delay between each to prevent
// correlated samples from the ADC sample-and-hold capacitor.
static float readLightLux() {
    const int ADC_SAMPLES = 8;
    long sum = 0;
    for (int i = 0; i < ADC_SAMPLES; i++) {
        sum += analogRead(LUX_PIN);
        if (i < ADC_SAMPLES - 1) {
            delay(1); // allow ADC capacitor to settle between samples
        }
    }
    float avg = (float)sum / ADC_SAMPLES;
    // Linear mapping: ADC 0–4095 → 0–LIGHT_LUX_SCALE
    // LIGHT_LUX_SCALE is an empirical approximation calibrated to cover the
    // Pi's qualitative light bands (dark<10 … glaring≥1000). Adjust in config.h
    // after testing in real lighting conditions.
    return (avg / 4095.0f) * LIGHT_LUX_SCALE;
}

// ── setup() ──────────────────────────────────────────────────────────────────
// Entire firmware lifecycle runs here. loop() is intentionally empty.
// Each deep-sleep wake is a cold start — all RAM is cleared.
void setup() {
    Serial.begin(115200);
    delay(100); // brief settle for serial buffer

    Serial.println("\n[boot] environment_narrator sensor node");

    // Configure ADC attenuation for full 0–3.3 V input range (ADC_11db).
    // Deferred: if the HW-486 module's voltage swing is narrower, adjust to
    // ADC_6db or ADC_2_5db for better resolution in the actual operating range.
    analogSetAttenuation(ADC_11db);

    // ── Step 1: Initialize DHT11 ─────────────────────────────────────────────
    dht.setup(DHTPIN, DHTesp::DHT11);

    // ── Step 2: Read DHT11 ───────────────────────────────────────────────────
    // DHTesp needs a minimum inter-read delay after setup(). The library
    // enforces this internally, but a short explicit delay avoids the first-read
    // ERROR_TOO_FAST error that DHT11 produces on cold start.
    delay(dht.getMinimumSamplingPeriod());

    TempAndHumidity reading = dht.getTempAndHumidity();

    bool dhtOk = (dht.getStatus() == DHTesp::ERROR_NONE);
    if (!dhtOk) {
        Serial.println("[dht] read error: " + String(dht.getStatusString()));
        Serial.println("[dht] skipping publish this cycle");
    } else {
        Serial.printf("[dht] temperature: %.1f °C\n", reading.temperature);
        Serial.printf("[dht] humidity:    %.1f %%\n", reading.humidity);
    }

    // ── Step 3: Read light sensor ────────────────────────────────────────────
    // ADC read is unconditional — light data is valid even if DHT fails.
    // We still log it to serial for diagnostics.
    float lightLux = readLightLux();
    Serial.printf("[lux] light (scaled): %.1f\n", lightLux);

    // ── Step 4: Connect WiFi ─────────────────────────────────────────────────
    if (!connectWiFi()) {
        goToSleep();
        return; // unreachable — deep sleep never returns
    }

    // ── Step 5: Connect MQTT ─────────────────────────────────────────────────
    if (!connectMQTT()) {
        goToSleep();
        return;
    }

    // ── Step 6: Publish (only if DHT read was valid) ─────────────────────────
    if (!dhtOk) {
        Serial.println("[mqtt] DHT error — skipping publish, going to sleep");
        goToSleep();
        return;
    }

    // Build JSON payload. Field names are a contract with narrator.py —
    // see docs/mqtt-schema.md. Do not rename without updating the Pi side.
    JsonDocument doc;
    doc["temperature_c"] = reading.temperature;
    doc["humidity_pct"]  = reading.humidity;
    doc["light_lux"]     = lightLux;

    char payload[256];
    size_t len = serializeJson(doc, payload, sizeof(payload));

    Serial.println("[mqtt] publishing: " + String(payload));

    bool published = mqttClient.publish(MQTT_TOPIC, payload);
    if (!published) {
        // publish() returns false if the connection dropped mid-send or the
        // payload exceeded MQTT_MAX_PACKET_SIZE. Log and continue to sleep.
        Serial.println("[mqtt] publish failed (state=" +
                       String(mqttClient.state()) + ")");
    } else {
        Serial.println("[mqtt] publish OK (" + String(len) + " bytes)");
    }

    // ── Step 7: Teardown and sleep ───────────────────────────────────────────
    // goToSleep() calls client.disconnect() → WiFi.disconnect() → deep_sleep.
    // client.disconnect() sends a TCP FIN; the brief WiFi.disconnect() delay
    // allows lwIP to process the FIN before the radio is powered off.
    goToSleep();
}

// ── loop() ───────────────────────────────────────────────────────────────────
// Intentionally empty. The entire lifecycle runs in setup(); deep sleep
// resets all RAM on each wake so loop() is never reached.
void loop() {}
