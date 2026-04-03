---
title: "feat: ESP32 Sensor Firmware — DHT11 + Photoresistor over MQTT"
type: feat
status: completed
date: 2026-04-02
deepened: 2026-04-02
---

# feat: ESP32 Sensor Firmware — DHT11 + Photoresistor over MQTT

## Overview

Add a PlatformIO C++ firmware project for the ESP32-WROOM-32UE DevKitC V4 that reads temperature and humidity from a DHT11 sensor and relative light level from an HW-486 photoresistor, then publishes a JSON payload to an MQTT broker (running on the Raspberry Pi) every 5 minutes. The Pi's `narrator.py` will later subscribe to this topic to replace its hardcoded sensor values.

## Problem Frame

`narrator.py` currently uses hardcoded sensor values (`temperature_c: 22.4`, `humidity_pct: 55.0`, `light_lux: 300.0`) as a placeholder. Step 5 of the project (marked explicitly in the code at line 47) is to replace these with real MQTT-sourced readings from a hardware sensor node. This firmware is that sensor node.

## Requirements Trace

- R1. Read temperature (°C) and humidity (%) from DHT11 on GPIO 32 every 5 minutes.
- R2. Read relative light level from HW-486 photoresistor on GPIO 33 every 5 minutes.
- R3. Publish a JSON payload to MQTT topic `environment/sensors` on the Pi's local broker.
- R4. JSON payload field names must match what `narrator.py` expects: `temperature_c`, `humidity_pct`, `light_lux`.
- R5. Print all sensor readings and MQTT status to the serial console.
- R6. The 5-minute interval must be a named macro, easily changed for testing.
- R7. WiFi SSID/password and MQTT broker IP must NOT be committed to version control.

## Scope Boundaries

- No MQTT subscribe logic — this node is publish-only.
- No OTA update support.
- No battery management or sleep optimization (deep sleep is included for power reasons but is not a primary requirement).
- The Pi-side MQTT subscription changes to `narrator.py` are **out of scope** for this plan — that is a separate step.
- No true radiometric lux conversion — photoresistor hardware cannot produce calibrated lux values.

## Context & Research

### Relevant Code and Patterns

- `pi/narrator.py` lines 50-54: hardcoded sensor dict — the ESP32 JSON payload must match these exact field names.
- `pi/narrator.py` lines 195-204 (`build_sensor_context`): consumes `temperature_c`, `humidity_pct`, `light_lux` as floats.
- `pi/narrator.py` line 47: explicit TODO marking this as "step 5" — MQTT integration.
- `pi/.env.example`: the pattern for credential isolation the Pi side already follows — replicate with `config.h` on the MCU side.
- `pi/requirements.txt` line 16: `paho-mqtt>=2.0.0` — broker already expected to be mosquitto on the Pi.
- `.gitignore`: currently C/C++ configured — extend with PlatformIO-specific ignores.

### Institutional Learnings

No prior ESP32 or MQTT institutional learnings exist in `docs/solutions/`. This plan captures the first set of decisions for this layer.

### External References

- [PlatformIO ESP32 board reference — `esp32dev`](https://docs.platformio.org/en/latest/boards/espressif32/esp32dev.html)
- [beegee-tokyo/DHTesp — ESP32-optimized DHT library](https://github.com/beegee-tokyo/DHTesp)
- [knolleary/PubSubClient — Arduino MQTT client](https://github.com/knolleary/pubsubclient)
- [bblanchon/ArduinoJson v7](https://arduinojson.org)
- [ESP32 ADC pin safety with WiFi — Random Nerd Tutorials](https://randomnerdtutorials.com/esp32-adc-analog-read-arduino-ide/)

## Key Technical Decisions

- **Board identifier `esp32dev`**: The ESP32-WROOM-32UE DevKitC V4 has no unique PlatformIO board ID. `esp32dev` targets the standard ESP32 at 240 MHz / 4 MB flash and is the correct generic identifier. The `az-delivery-devkit-v4` alias is redundant and adds no benefit.

- **DHTesp over Adafruit DHT**: The ESP32 runs FreeRTOS on dual cores. The DHT11 protocol is bit-banged at microsecond timing. The Adafruit DHT library does not reliably disable interrupts during reads on ESP32, causing occasional corrupt sensor reads under OS task switching. `beegee-tokyo/DHTesp` explicitly guards reads with `noInterrupts()`. At a 5-minute sample interval this difference is observable. DHTesp is unmaintained as of v1.19 (Apr 2023) but stable; Adafruit DHT is the documented fallback if a blocking bug surfaces.

- **PubSubClient over AsyncMqttClient**: Async MQTT's non-blocking I/O is irrelevant for a firmware that reads, publishes, and sleeps. PubSubClient's synchronous API maps cleanly to the `setup()` → publish → sleep execution model. `MQTT_MAX_PACKET_SIZE` is overridden to 512 bytes via build flag as a defensive measure — the target 3-field JSON payload (~81 bytes including topic and MQTT headers) fits within the default 256-byte limit, but 512 bytes provides headroom for future field additions without requiring a revisit of this setting.

- **GPIO 33 for photoresistor (ADC1 bank)**: The ESP32 WiFi driver blocks the ADC2 peripheral at the silicon level. Any `analogRead()` on an ADC2 pin while WiFi is active returns garbage or crashes. GPIO 33 is on ADC1 — safe to use with WiFi active. GPIO 32 (DHT11) is a digital GPIO and is unaffected by this constraint.

- **Deep sleep between readings**: With a 5-minute interval and ~3 seconds of active time per cycle, staying WiFi-connected wastes 20–60 mA continuously. Deep sleep reduces average draw by ~50×. The entire firmware lifecycle runs in `setup()`; `loop()` is empty.

- **Light value reported as scaled approximation, not true lux**: The HW-486 is a photoresistor — a relative resistance-based sensor. It cannot produce calibrated lux values without an empirical calibration curve specific to the physical resistor and enclosure. The raw 12-bit ADC reading (0–4095) is linearly scaled to a 0–1500 range to approximate coverage of the Pi's qualitative light thresholds (dark < 10, dim 10–50, soft 50–200, bright 200–500, vivid 500–1000, glaring 1000+). The field is named `light_lux` to match the Pi's expected field name. This approximation is adequate for generating meaningful qualitative labels; it is not a radiometric measurement.

- **`config.h` credential isolation**: WiFi SSID, password, MQTT broker IP, and MQTT port are defined in `mcu/include/config.h`, which is gitignored. A committed `mcu/include/config.h.example` provides the template. This mirrors the `pi/.env` / `pi/.env.example` pattern already established in the project.

- **MQTT QoS 0**: Fire-and-forget. Sensor readings are low-stakes and repeated every 5 minutes — a dropped packet is not a data integrity issue. QoS 1 would require the broker to ACK each message and add complexity for no meaningful benefit.

- **`client.disconnect()` is the delivery fence, not `publish()`**: PubSubClient's `publish()` is synchronous at the write level but does not guarantee the TCP stack has flushed before returning. Calling `client.disconnect()` before deep sleep triggers a TCP FIN and drains the send buffer — this is the actual delivery boundary. The teardown sequence `disconnect() → WiFi.disconnect(true) → WiFi.mode(WIFI_OFF) → deep_sleep` must be kept in this order.

- **Sensor field-name contract requires a durable schema document**: `narrator.py` consumes `temperature_c`, `humidity_pct`, and `light_lux` at three distinct call sites. A rename on either side is a silent runtime break with no compile-time detection. Unit 3 should produce a minimal schema table in `docs/` (e.g., `docs/mqtt-schema.md`) documenting the topic, payload format, and field names. The Pi-side MQTT step must also include a contract validation point — asserting expected keys are present on first message receipt before passing to `build_sensor_context`.

- **JSON payload via ArduinoJson v7**: Manual `sprintf` formatting is viable for 3 static fields but fails silently on NaN (from a failed DHT read) and requires careful escaping. ArduinoJson handles these edge cases automatically. `StaticJsonDocument` is removed in v7 — use `JsonDocument` instead.

## Open Questions

### Resolved During Planning

- **Which ADC pin for lux sensor?** GPIO 33 is on ADC1, safe with WiFi. Confirmed safe by hardware research.
- **Which MQTT library?** PubSubClient — synchronous API fits publish-only deep-sleep firmware. Async adds complexity for no benefit.
- **Where does MCU code live in the repo?** New `mcu/` directory at the project root, parallel to `pi/`.
- **Should readings be averaged for noise reduction?** Yes — average 8 ADC samples for the photoresistor (takes microseconds, negligible cost). DHT11 inherently returns one reading per call.
- **How to handle a failed DHT read?** Skip the MQTT publish for that cycle and log the error to serial. Do not publish stale or zero values.

### Deferred to Implementation

- **Optimal ADC attenuation setting**: `analogSetAttenuation(ADC_11db)` is the recommended starting point for full 0–3.3 V range, but the actual voltage swing on the HW-486 module depends on its built-in resistor divider. Verify at runtime.
- **WiFi association time**: Expected 1–3 seconds. If connections are consistently slow, saving BSSID + channel in RTC memory can reduce this to ~400 ms.
- **Light scaling calibration**: The 0–1500 linear mapping is an initial approximation. Adjust the scale factor empirically by observing readings in actual lighting conditions.
- **MQTT broker port**: Defaults to 1883 (mosquitto standard). Defined in `config.h` — no code change needed to adjust.

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

```
setup() lifecycle (runs once per deep-sleep wake):
┌─────────────────────────────────────────────────────────┐
│  1. Serial begin (115200)                               │
│  2. WiFi.begin(WIFI_SSID, WIFI_PASS)                   │
│     └─ Wait up to WIFI_TIMEOUT_MS — bail if exceeded   │
│  3. mqttClient.setServer(MQTT_BROKER_IP, MQTT_PORT)    │
│     mqttClient.connect(CLIENT_ID)                       │
│     └─ Bail on failure                                  │
│  4. Read DHT11 → temperature_c, humidity_pct           │
│     └─ If read fails → log error, skip publish         │
│  5. Read ADC (8-sample avg) → raw → scale to light_lux │
│  6. Serialize JSON: {temperature_c, humidity_pct,       │
│                       light_lux}                        │
│  7. mqttClient.publish("environment/sensors", payload)  │
│  8. Serial.println(payload)                             │
│  9. mqttClient.disconnect() / WiFi.disconnect()         │
│ 10. esp_sleep_enable_timer_wakeup(SLEEP_INTERVAL_US)   │
│     esp_deep_sleep_start()                              │
└─────────────────────────────────────────────────────────┘

loop() — intentionally empty
```

**JSON payload shape (target):**
```json
{"temperature_c": 22.5, "humidity_pct": 61.0, "light_lux": 342.7}
```

## Implementation Units

```
mcu/
├── platformio.ini
├── include/
│   ├── config.h           (gitignored — credentials)
│   └── config.h.example   (committed — template)
└── src/
    └── main.cpp
```

---

- [x] **Unit 1: PlatformIO Project Scaffold**

**Goal:** Create the `mcu/` project directory with a complete `platformio.ini`, credential template, and updated `.gitignore`. No firmware logic yet.

**Requirements:** R7

**Dependencies:** None

**Files:**
- Create: `mcu/platformio.ini`
- Create: `mcu/include/config.h.example`
- Modify: `.gitignore` (add PlatformIO ignores and `mcu/include/config.h`)

**Approach:**
- `platformio.ini` must declare `platform = espressif32`, `board = esp32dev`, `framework = arduino`, all four `lib_deps`, `build_flags = -DMQTT_MAX_PACKET_SIZE=512`, and `monitor_speed = 115200`.
- `config.h.example` declares all credential macros with placeholder values: `WIFI_SSID`, `WIFI_PASS`, `MQTT_BROKER_IP`, `MQTT_PORT` (default 1883), `SLEEP_INTERVAL_US` (default 5 minutes = `300000000ULL` — must be a `uint64_t` literal; values over ~35 minutes without the `ULL` suffix silently overflow to a 32-bit int), `WIFI_TIMEOUT_MS` (default 10000), and `MQTT_CLIENT_ID` (default `"esp32-sensor-01"` — must be unique per physical device).
- `.gitignore` additions: `.pio/`, `mcu/include/config.h` (never commit real credentials), and standard PlatformIO build artifacts.

**Patterns to follow:**
- `pi/.env` / `pi/.env.example` credential isolation pattern.

**Test scenarios:**
- Test expectation: none — this unit creates scaffolding and configuration only. No behavioral logic to test. Verification is by inspection.

**Verification:**
- `.gitignore` is updated **before** running any `pio` command — the `.pio/` build directory can reach hundreds of MB and must not appear in `git status` after a first build.
- `mcu/include/config.h` is absent from the repo (gitignored).
- `mcu/include/config.h.example` is present and contains all required macro names including `MQTT_CLIENT_ID`.
- PlatformIO can parse `platformio.ini` without errors (`pio project metadata` succeeds after copying `config.h.example` to `config.h` and filling in values).

---

- [x] **Unit 2: Sensor Reading — DHT11 and Photoresistor**

**Goal:** Implement and verify the sensor reading logic for both sensors in `main.cpp`. At this stage, output readings to serial only — no WiFi or MQTT yet.

**Requirements:** R1, R2, R5, R6

**Dependencies:** Unit 1

**Files:**
- Create: `mcu/src/main.cpp`

**Approach:**
- Define `DHTPIN 32`, `DHTTYPE DHT11`, `LUX_PIN 33`, and `SLEEP_INTERVAL_US` (sourced from `config.h`).
- Initialize DHTesp with `dht.setup(DHTPIN, DHTesp::DHT11)`.
- Read with `TempAndHumidity reading = dht.getTempAndHumidity()`. Check `dht.getStatus() != DHTesp::ERROR_NONE` — if error, log to serial and skip the cycle.
- For ADC: call `analogSetAttenuation(ADC_11db)` once in `setup()`. Average 8 `analogRead(LUX_PIN)` calls with a 1 ms delay between each sample (`delay(1)`) to allow the ADC sample-and-hold capacitor to settle — without inter-sample delay, successive reads on the ESP32 can return correlated samples, defeating the noise-reduction purpose of averaging. Scale result: `light_lux = (avg / 4095.0f) * 1500.0f`.
- Print all three values to serial in a clearly labeled format.
- At end of `setup()`, call `esp_deep_sleep_start()` with `SLEEP_INTERVAL_US`.
- `loop()` is empty.

**Patterns to follow:**
- DHTesp API: `dht.setup()`, `dht.getTempAndHumidity()`, `dht.getStatus()`.

**Test scenarios:**
- Happy path: DHT11 returns valid reading → temperature, humidity, and scaled light values printed to serial with correct labels and units.
- Edge case — DHT read failure: `dht.getStatus() != ERROR_NONE` → error message logged to serial; no garbage values printed; device proceeds to deep sleep.
- Edge case — full dark (LDR at maximum resistance): ADC reads near 0 → `light_lux` near 0.0; no divide-by-zero or negative value.
- Edge case — full bright (LDR at minimum resistance): ADC reads near 4095 → `light_lux` near 1500.0; no overflow or clamp needed.
- Happy path — deep sleep: after printing readings, device enters deep sleep; serial output stops (confirms `esp_deep_sleep_start()` was reached).

**Verification:**
- Serial monitor shows three readings every wake cycle.
- Sensor values are within physically plausible ranges (temperature 0–50°C, humidity 0–100%, light 0–1500).
- DHT failure path produces an error message without crashing or printing NaN.

---

- [x] **Unit 3: WiFi, MQTT Publish, and Deep Sleep Integration**

**Goal:** Extend `main.cpp` with WiFi connection, MQTT publish, and graceful error paths. The device connects, publishes the JSON payload to `environment/sensors`, logs the result to serial, disconnects, and enters deep sleep.

**Requirements:** R3, R4, R5, R6, R7

**Dependencies:** Unit 2

**Files:**
- Modify: `mcu/src/main.cpp`
- Create: `docs/mqtt-schema.md` (topic, payload format, field name contract table)

**Approach:**
- WiFi: call `WiFi.begin(WIFI_SSID, WIFI_PASS)` and poll `WiFi.status()` in a loop with a counter bounded by `WIFI_TIMEOUT_MS`. If timeout is reached, log to serial and go directly to deep sleep — do not hang.
- MQTT: `PubSubClient client(wifiClient)`. Call `client.setServer(MQTT_BROKER_IP, MQTT_PORT)` and `client.connect(MQTT_CLIENT_ID)` (sourced from `config.h`). On connection failure, log to serial and go to deep sleep. After a successful `connect()`, call `client.loop()` once to process any pending CONNACK or broker control packets before publishing — in a `setup()`-only lifecycle the main `loop()` never runs, so the internal PubSubClient state machine must be ticked manually at least once between connect and publish.
- JSON: use `JsonDocument doc; doc["temperature_c"] = reading.temperature; doc["humidity_pct"] = reading.humidity; doc["light_lux"] = light_lux;` then `serializeJson(doc, payload_buf, sizeof(payload_buf))`.
- Publish: `client.publish("environment/sensors", payload_buf)`. Log the full JSON string to serial.
- Teardown: `client.disconnect(); WiFi.disconnect(true); WiFi.mode(WIFI_OFF);` before deep sleep.
- Field names in the JSON document must exactly match: `temperature_c`, `humidity_pct`, `light_lux`.

**Patterns to follow:**
- `pi/.env` / `pi/.env.example` — all broker/network config sourced from `config.h` macros, never hardcoded in `main.cpp`.

**Test scenarios:**
- Happy path — full cycle: WiFi connects, MQTT connects, sensor values are valid → JSON published to `environment/sensors`, serial prints the payload, device sleeps.
- Error path — WiFi timeout: WiFi does not associate within `WIFI_TIMEOUT_MS` → error logged to serial, device enters deep sleep without hanging.
- Error path — MQTT broker unreachable: WiFi connects but broker is down → `client.connect()` returns false → error logged, device enters deep sleep.
- Error path — DHT failure + WiFi ok: DHT read fails → skip publish entirely, log error, sleep. No partial JSON published.
- Integration — Pi broker receives payload: use `mosquitto_sub -t environment/sensors` on the Pi to confirm the broker receives well-formed JSON with all three fields at the correct names.
- Happy path — sleep cycle: after one full successful publish, deep sleep is entered; device wakes again after `SLEEP_INTERVAL_US` and repeats the cycle.

**Verification:**
- `mosquitto_sub -t environment/sensors` on the Pi shows valid JSON every 5 minutes.
- JSON payload contains exactly `temperature_c`, `humidity_pct`, `light_lux` — names match what `narrator.py` expects.
- Serial monitor shows WiFi status, MQTT status, full JSON payload, and "going to sleep" message each cycle.
- Disconnecting the Pi (broker down) does not cause the ESP32 to hang indefinitely.

---

## System-Wide Impact

- **Interaction graph:** `narrator.py` is unmodified by this plan. Its `HARDCODED_SENSORS` dict will continue to be used until a separate step wires up MQTT subscription on the Pi side. These two changes are deliberately decoupled.
- **Error propagation:** Sensor or network failures are contained to the ESP32. A failed publish cycle is followed by deep sleep and a retry on the next wake — no crash, no hang, no impact on the Pi.
- **State lifecycle risks:** Deep sleep resets all RAM. Each wake is a cold start. WiFi credentials are read from flash (persistent). No global mutable state accumulates across cycles.
- **API surface parity:** The JSON payload field names (`temperature_c`, `humidity_pct`, `light_lux`) form an implicit contract with `narrator.py`. Any rename on either side is a silent runtime break — no compile-time detection exists. A `docs/mqtt-schema.md` schema table is the durable artifact that pins this contract (added in Unit 3).
- **Integration coverage:** The integration test is manual at this stage — `mosquitto_sub` on the Pi confirms broker receipt. End-to-end integration with `narrator.py` is deferred to the Pi-side MQTT step, which must validate expected keys are present before passing values to `build_sensor_context`.
- **Light scaling data quality risk:** The 0–1500 `light_lux` scaling is an uncalibrated linear approximation. A systematic ADC offset or photoresistor nonlinearity could cause a permanently dark room to read as "soft" or "bright" against the Pi's qualitative thresholds. The Pi has no way to detect this — empirical calibration of the scale factor (deferred to implementation) is required before the end-to-end system produces consistently meaningful qualitative labels.
- **Broker trust boundary:** Mosquitto on the Pi has no authentication configured by default. Any device on the local network can publish to `environment/sensors` and inject arbitrary sensor values. This is acceptable for a private home network but creates a prompt-injection risk: if payload field values are embedded in the Gemini LLM prompt without validation, a malicious device could inject arbitrary text. The Pi-side MQTT step must cast each numeric field to `float` with strict parsing (rejecting non-numeric or out-of-range values) before passing to `build_sensor_context` and the LLM prompt. Plausible physical ranges: temperature −40–85 °C, humidity 0–100%, light_lux 0–1500.
- **Unchanged invariants:** `narrator.py` is not touched. Its hardcoded sensor values remain active until MQTT subscription is added in a future step.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| ADC2 blocked by WiFi driver (GPIO 25–27, 0, 2, etc.) | GPIO 33 is ADC1 — confirmed safe. Documented as a permanent constraint. |
| DHTesp unmaintained (last release Apr 2023) | API is stable; Adafruit DHT is the documented fallback if a blocking issue appears. |
| `MQTT_MAX_PACKET_SIZE` default 256 bytes silently truncates payload | Override to 512 via `build_flags = -DMQTT_MAX_PACKET_SIZE=512` in `platformio.ini`. |
| Light value reported as "lux" but is actually a scaled ADC approximation | Documented in Key Technical Decisions. Field name matches Pi contract; qualitative labels remain meaningful. |
| WiFi credentials in `config.h` accidentally committed | `mcu/include/config.h` added to `.gitignore` in Unit 1. `config.h.example` is the only committed file. |
| Mosquitto not yet installed or configured on Pi | Prerequisite: `sudo apt install mosquitto` on the Pi and confirm it is running before testing Unit 3. Not a firmware concern but must be satisfied before end-to-end testing. |
| DHT11 read timing corrupted by FreeRTOS task switch | DHTesp disables interrupts during reads. If reads are still erratic, add a 2-second stabilization delay after `dht.setup()`. |
| `client.publish()` returns false silently (payload too large or mid-send disconnect) | `MQTT_MAX_PACKET_SIZE=512` covers the size case. Mid-send disconnect returns false — check the return value and log the failure before proceeding to teardown. Do not deep sleep without logging a failed publish. |
| Uncalibrated `light_lux` scaling produces misleading qualitative labels | Deferred to implementation — adjust the scale factor empirically in first real-world deployment. Initial 0–1500 range provides coverage of all Pi thresholds but requires field validation. |

## Documentation / Operational Notes

- After Unit 1, copy `mcu/include/config.h.example` to `mcu/include/config.h` and fill in WiFi credentials and the Pi's local IP before building.
- Mosquitto must be running on the Pi (`sudo systemctl enable --now mosquitto`) before Unit 3 can be end-to-end tested.
- The 5-minute interval macro `SLEEP_INTERVAL_US` can be shortened to `10000000` (10 seconds) for rapid testing — change it in `config.h.example` with a note to revert before deployment.
- `CLAUDE.md` should be updated to note the new `mcu/` directory, PlatformIO as the build tool, and the `config.h` credential pattern.

## Sources & References

- Related code: `pi/narrator.py` lines 47–54, 195–204
- Related code: `pi/.env.example` (credential isolation pattern)
- External docs: [PlatformIO `esp32dev` board](https://docs.platformio.org/en/latest/boards/espressif32/esp32dev.html)
- External docs: [beegee-tokyo/DHTesp](https://github.com/beegee-tokyo/DHTesp)
- External docs: [knolleary/PubSubClient](https://github.com/knolleary/pubsubclient)
- External docs: [bblanchon/ArduinoJson v7](https://arduinojson.org)
- External docs: [ESP32 ADC safety with WiFi](https://randomnerdtutorials.com/esp32-adc-analog-read-arduino-ide/)
