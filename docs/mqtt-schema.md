# MQTT Schema — environment/sensors

This document pins the payload contract between the ESP32 sensor node (`mcu/`) and
the Raspberry Pi narrator (`pi/narrator.py`). **Any rename or type change on either
side is a silent runtime break** — update both sides together and increment this
document.

## Broker

| Property | Value |
|----------|-------|
| Host     | Raspberry Pi local IP (configured in `mcu/include/config.h` → `MQTT_BROKER_IP`) |
| Port     | 1883 (default mosquitto; configured via `MQTT_PORT`) |
| Auth     | None (private home network) |
| TLS      | None |

## Topic

| Topic | Direction | Publisher | Subscriber |
|-------|-----------|-----------|------------|
| `environment/sensors` | ESP32 → Pi | ESP32 sensor node | Pi `narrator.py` (future step) |

## Payload

Format: JSON, UTF-8, no trailing newline.

| Field | Type | Unit | Range | Notes |
|-------|------|------|-------|-------|
| `temperature_c` | `float` | °C | −40–85 | DHT11 measurement; skipped (no publish) on read error |
| `humidity_pct` | `float` | % RH | 0–100 | DHT11 measurement |
| `light_lux` | `float` | approx. lux-equivalent | 0–`LIGHT_LUX_SCALE` | Scaled ADC reading; **not a calibrated radiometric value**. Default scale maps ADC 0–4095 → 0–1500. Adjust `LIGHT_LUX_SCALE` in `config.h` after field calibration. |

### Example payload

```json
{"temperature_c": 22.5, "humidity_pct": 61.0, "light_lux": 342.7}
```

## Quality of Service

| Property | Value | Rationale |
|----------|-------|-----------|
| QoS | 0 (fire-and-forget) | Sensor readings are low-stakes; a dropped packet is recovered on the next 5-minute cycle |
| Retain | false | Pi reads live data; a retained stale reading would mislead the LLM on startup |

## Consumer notes (`narrator.py`)

- Consumes `temperature_c`, `humidity_pct`, `light_lux` as floats in `build_sensor_context()`.
- The Pi-side MQTT integration step **must** validate that all three keys are present and are
  numeric (cast to `float` with `try/except ValueError`) before passing to `build_sensor_context`.
  Missing or non-numeric values should be logged and the message discarded.
- Plausible physical ranges for validation: temperature −40–85 °C, humidity 0–100%, light_lux 0–1500.

## Versioning

This schema is currently unversioned. If a field is added, removed, or renamed:
1. Update this document.
2. Update `mcu/src/main.cpp` (publisher).
3. Update `pi/narrator.py` (consumer) in the same PR.
4. Consider adding a `schema_version` field to the payload if consumer diversity grows.
