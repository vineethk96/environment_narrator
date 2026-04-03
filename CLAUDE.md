# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**environment_narrator** is an IoT project that "gives the environment a voice." It streams sensor data over MQTT to a Raspberry Pi, which queries an LLM to generate a contextual, natural-language interpretation of its surroundings.

## Architecture

The system has three layers:

1. **Sensors** — Collect environmental data (temperature, humidity, etc.) and publish readings over MQTT.
2. **Raspberry Pi hub** — Subscribes to MQTT topics, batches or processes incoming sensor data, and invokes an LLM API to produce a narrative description.
3. **LLM integration** — Takes structured sensor readings as context and returns a human-readable interpretation.

The `.gitignore` is configured for C/C++ and PlatformIO.

## gstack

Use the `/browse` skill for all web browsing. Never use `mcp__claude-in-chrome__*` tools directly.

Available skills:
`/office-hours`, `/plan-ceo-review`, `/plan-eng-review`, `/plan-design-review`, `/design-consultation`, `/design-shotgun`, `/design-html`, `/review`, `/ship`, `/land-and-deploy`, `/canary`, `/benchmark`, `/browse`, `/connect-chrome`, `/qa`, `/qa-only`, `/design-review`, `/setup-browser-cookies`, `/setup-deploy`, `/retro`, `/investigate`, `/document-release`, `/codex`, `/cso`, `/autoplan`, `/careful`, `/freeze`, `/guard`, `/unfreeze`, `/gstack-upgrade`, `/learn`

## Skill routing

When the user's request matches an available skill, ALWAYS invoke it using the Skill
tool as your FIRST action. Do NOT answer directly, do NOT use other tools first.
The skill has specialized workflows that produce better results than ad-hoc answers.

Key routing rules:
- Product ideas, "is this worth building", brainstorming → invoke office-hours
- Bugs, errors, "why is this broken", 500 errors → invoke investigate
- Ship, deploy, push, create PR → invoke ship
- QA, test the site, find bugs → invoke qa
- Code review, check my diff → invoke review
- Update docs after shipping → invoke document-release
- Weekly retro → invoke retro
- Design system, brand → invoke design-consultation
- Visual audit, design polish → invoke design-review
- Architecture review → invoke plan-eng-review

## Repository Structure

```
mcu/                    ESP32 sensor node firmware (PlatformIO / Arduino framework)
├── platformio.ini      Board, libraries, build flags
├── include/
│   ├── config.h.example  Credential template — commit this
│   └── config.h          Your credentials — gitignored, never commit
└── src/
    └── main.cpp        Firmware: DHT11 + photoresistor → MQTT publish → deep sleep

pi/                     Raspberry Pi narrator (Python)
├── narrator.py         Main script: MQTT → LLM → TTS → audio
├── .env.example        Credential template — commit this
└── .env                Your credentials — gitignored, never commit

docs/
├── mqtt-schema.md      MQTT topic and payload contract between ESP32 and Pi
├── plans/              Implementation plans
└── solutions/          Documented solutions to past problems
```

## MCU Quickstart (ESP32 sensor node)

1. Install [PlatformIO VSCode extension](https://platformio.org/install/ide?install=vscode).
2. Copy `mcu/include/config.h.example` → `mcu/include/config.h` and fill in:
   - `WIFI_SSID` / `WIFI_PASS` — your home WiFi
   - `MQTT_BROKER_IP` — Raspberry Pi LAN IP (run `hostname -I` on the Pi)
   - `MQTT_CLIENT_ID` — unique per device (default `"esp32-sensor-01"`)
3. Open `mcu/` in VSCode → PlatformIO: Build → Upload.
4. For quick testing, set `SLEEP_INTERVAL_US 10000000ULL` (10 s) in `config.h`.

## Key Design Decisions (to be preserved as code is added)

- MQTT is the transport protocol between sensors and the Pi hub.
- The LLM query is triggered on the Pi side, not on the sensor nodes.
- The project targets a Raspberry Pi as the edge compute device, so resource constraints apply.
- ESP32 firmware uses deep sleep between readings; the entire lifecycle runs in `setup()`.
- GPIO 33 (ADC1) is used for the photoresistor — ADC2 pins are blocked by the WiFi driver at silicon level.
- `docs/mqtt-schema.md` is the authoritative payload contract between the ESP32 and `narrator.py`.

## Documented Solutions

`docs/solutions/` — documented solutions to past problems (runtime errors, best practices, configuration patterns), organized by category with YAML frontmatter (`module`, `tags`, `problem_type`). Relevant when implementing or debugging in documented areas.
