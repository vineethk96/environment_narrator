# environment_narrator

An IoT project that gives a room its own voice. An ESP32 reads temperature, humidity, and light from the environment, publishes the data over MQTT to a Raspberry Pi, which then asks an LLM to describe what the room *feels* like — and speaks the answer aloud.

The output is not a weather report. It is something closer to poetry: "The room settles into the quiet weight of late afternoon, its warmth softened by the fading light that no longer insists on anything."

---

## How It Works

```
ESP32 (DHT11 + photoresistor)
  │  wakes from deep sleep every 60 seconds
  │  reads temperature, humidity, light
  ↓
MQTT publish → topic: "environment/sensors"
  ↓
Raspberry Pi (Mosquitto broker)
  │  mqtt_subscriber.py validates the JSON payload
  │  narrates every 10 minutes, or immediately on significant change
  ↓
narrator.py → Google Gemini 2.5 Flash
  │  prompt: 2-sentence sensory/emotional description, no numbers
  │  context: time of day, recent narration history (memory.db)
  ↓
Piper TTS → aplay → audio output
```

The Pi stores the last 200 narrations in a local SQLite database and passes the most recent 3 to the LLM as context, giving the narrator a sense of continuity — it remembers the arc of the day.

---

## Hardware

| Component | Part | Connection |
|---|---|---|
| Microcontroller | ESP32-WROOM-32UE DevKitC V4 | — |
| Temperature + humidity | DHT11 (3-pin module) | Data → GPIO 32 |
| Light | HW-486 photoresistor module | Analog out → GPIO 33 |
| Hub | Raspberry Pi (any model) | Ethernet or WiFi |
| Audio | 3.5mm speaker, HDMI display, or USB audio adapter | Pi audio out |

**Note on GPIO 33:** The light sensor must use GPIO 33 (or another ADC1 pin). ADC2 pins (GPIO 0, 2, 4, 12–15, 25–27) are blocked by the WiFi driver at the silicon level and cannot be read while WiFi is active.

---

## Prerequisites

### ESP32 (MCU)

- [PlatformIO](https://platformio.org/install/ide?install=vscode) — VSCode extension or CLI
- Libraries are installed automatically from `platformio.ini`:
  - `knolleary/PubSubClient` — MQTT client
  - `beegee-tokyo/DHT sensor library for ESPx` — DHT11 driver
  - `bblanchon/ArduinoJson` — JSON serialization

### Raspberry Pi

```bash
# MQTT broker
sudo apt install mosquitto mosquitto-clients

# ALSA audio utilities
sudo apt install alsa-utils

# Python dependencies
pip install -r pi/requirements.txt

# Piper TTS voice model (download once, ~200 MB)
mkdir -p pi/models
curl -L -o pi/models/en_US-lessac-medium.onnx \
  https://github.com/rhasspy/piper/releases/download/2023.11.14-2/en_US-lessac-medium.onnx
curl -L -o pi/models/en_US-lessac-medium.onnx.json \
  https://github.com/rhasspy/piper/releases/download/2023.11.14-2/en_US-lessac-medium.onnx.json
```

You also need a **Google Gemini API key** — get one at [aistudio.google.com](https://aistudio.google.com).

---

## Setup

### 1. ESP32

Copy the credential template and fill in your values:

```bash
cp mcu/include/config.h.example mcu/include/config.h
```

Edit `mcu/include/config.h`:

```c
#define WIFI_SSID      "YourWiFiName"
#define WIFI_PASS      "YourWiFiPassword"
#define MQTT_BROKER_IP "192.168.x.x"    // Pi's LAN IP — run: hostname -I on the Pi
#define MQTT_CLIENT_ID "esp32-sensor-01"
```

Open the `mcu/` directory in VSCode → **PlatformIO: Build** → **Upload**.

For quicker testing, reduce the sleep interval in `config.h`:

```c
#define SLEEP_INTERVAL_US 10000000ULL   // 10 seconds
```

### 2. Raspberry Pi

Copy the credential template and fill in your values:

```bash
cp pi/.env.example pi/.env
```

Edit `pi/.env`:

```bash
GEMINI_API_KEY=your_key_here

# Find your audio device: aplay -l
# Common values:
#   default                          (system default)
#   plughw:CARD=vc4hdmi,DEV=0       (HDMI)
#   plughw:CARD=Headphones,DEV=0    (Pi 3.5mm jack)
#   plughw:CARD=Device,DEV=0        (USB audio)
ALSA_DEVICE=default

MQTT_BROKER_HOST=localhost
MQTT_PORT=1883
```

Start the Mosquitto broker (if not already running as a service):

```bash
mosquitto -d
```

---

## Running

### Test mode (development)

`test_narrator.py` narrates on every valid MQTT message — no timer, no delta thresholds. Good for verifying the full pipeline after first setup.

```bash
cd pi
python test_narrator.py
```

Power on the ESP32. Within 60 seconds you should hear the first narration.

### Production mode

`mqtt_subscriber.py` is the production daemon. It narrates every 10 minutes or immediately when a sensor crosses a significant threshold (see [Narration Behavior](#narration-behavior)).

```bash
cd pi
python mqtt_subscriber.py
```

### systemd service (run on boot)

To run the narrator automatically as a system service:

```bash
# Store the API key outside the repo
sudo mkdir -p /etc/narrator
echo "GEMINI_API_KEY=your_key_here" | sudo tee /etc/narrator/env
sudo chmod 600 /etc/narrator/env

# Install and enable the service
sudo cp pi/narrator.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable narrator
sudo systemctl start narrator

# Check status / logs
sudo systemctl status narrator
journalctl -u narrator -f
```

The service file expects the project cloned to `/home/pi/environment_narrator`. Adjust `WorkingDirectory` in `narrator.service` if your path differs.

---

## Narration Behavior

The narrator speaks in two situations:

1. **Scheduled** — every 10 minutes, regardless of sensor changes
2. **On significant change** — immediately when any reading crosses a threshold since the last narration:

   | Sensor | Threshold |
   |---|---|
   | Temperature | ±2 °C |
   | Humidity | ±10 % RH |
   | Light | ±20 lux |

**Style constraints built into the prompt:**
- Exactly 2 sentences
- No numbers, measurements, degrees, percentages, or lux values
- Sensory and emotional language only — what the space *feels* like, not what it *measures*
- Circadian tone shifts with time of day (dawn is different from midnight)

**Memory:** The last 3 narrations are retrieved from `memory.db` and passed as context, instructing the model not to repeat itself and to let prior narrations inform its voice.

---

## Configuration Reference

### `mcu/include/config.h`

| Constant | Default | Description |
|---|---|---|
| `WIFI_SSID` | — | WiFi network name |
| `WIFI_PASS` | — | WiFi password |
| `WIFI_TIMEOUT_MS` | `10000` | WiFi connect timeout in milliseconds |
| `MQTT_BROKER_IP` | — | Raspberry Pi LAN IP address |
| `MQTT_PORT` | `1883` | MQTT broker port |
| `MQTT_CLIENT_ID` | `"esp32-sensor-01"` | Unique device ID (change if running multiple nodes) |
| `SLEEP_INTERVAL_US` | `60000000` | Deep sleep duration in microseconds (60 s) |
| `LIGHT_LUX_SCALE` | `1500.0` | ADC-to-lux multiplier (empirical, not calibrated) |

### `pi/.env`

| Variable | Default | Description |
|---|---|---|
| `GEMINI_API_KEY` | — | Google Gemini API key (required) |
| `ALSA_DEVICE` | `"default"` | ALSA device string for audio output |
| `MQTT_BROKER_HOST` | `"localhost"` | MQTT broker hostname |
| `MQTT_PORT` | `1883` | MQTT broker port |

---

## Troubleshooting

**No audio / `aplay: Unknown PCM Headphones`**

ALSA requires the full device specifier, not just the card name. Run `aplay -l` to list devices, then use the format `plughw:CARD=<name>,DEV=<num>`:

```bash
aplay -l
# Look for your output device, e.g.:
#   card 0: Headphones [bcm2835 Headphones], device 0: bcm2835 Headphones [...]

# Set in pi/.env:
ALSA_DEVICE=plughw:CARD=Headphones,DEV=0
```

See also: `docs/solutions/runtime-errors/alsa-unknown-pcm-headphones-error-2026-04-01.md`

**DHT11 read errors in serial monitor**

The DHT11 has a minimum sampling period of ~2 seconds. The firmware enforces this delay before reading. If errors persist, check:
- Data pin is connected to GPIO 32 (not an ADC2 pin)
- Pull-up resistor on the data line (some modules include this, some don't)
- Power supply — DHT11 requires 3.3–5 V; insufficient current causes spurious read failures

When the DHT11 fails, the firmware skips the MQTT publish and goes directly to deep sleep. No invalid data is ever sent.

**ADC reading always 0 or noisy**

The photoresistor must be on GPIO 33 (ADC1). Any ADC2 pin (GPIO 0, 2, 4, 12–15, 25–27) will return 0 or garbage while WiFi is active. This is a hardware constraint of the ESP32 silicon.

**Narration never fires after MQTT messages arrive**

`mqtt_subscriber.py` requires all three sensor fields to be present and within valid ranges before narrating. Check the subscriber logs for validation errors:

```bash
journalctl -u narrator -f
# Look for: [mqtt] missing field '...' or [mqtt] malformed payload
```

---

## Project Layout

```
mcu/                         ESP32 sensor node firmware (PlatformIO)
├── platformio.ini           Board config, libraries, build flags
├── include/
│   ├── config.h.example     Credential template — commit this
│   └── config.h             Your credentials — gitignored, never commit
└── src/
    └── main.cpp             Firmware: sensors → JSON → MQTT → deep sleep

pi/                          Raspberry Pi narrator (Python)
├── narrator.py              Core engine: LLM prompting, TTS, SQLite memory
├── mqtt_subscriber.py       Production daemon: MQTT → validate → narrate
├── test_narrator.py         Development harness: narrate on every message
├── narrator.service         systemd service definition
├── requirements.txt         Python dependencies
├── .env.example             Credential template — commit this
└── models/                  Piper TTS voice model (download separately)

docs/
├── mqtt-schema.md           MQTT topic and payload contract (authoritative)
├── plans/                   Implementation planning documents
└── solutions/               Documented solutions to past problems
```

---

## License

MIT
