"""
test_narrator.py — manual end-to-end test script for environment narrator.

Subscribes to 'environment/sensors' and narrates on EVERY valid MQTT message
(no delta thresholds, no 10-minute timer). Exercises the full pipeline:
  ESP32 → MQTT → validation → Gemini LLM → Piper TTS → aplay

Run:
    python pi/test_narrator.py

Stop with Ctrl+C.

Environment variables (pi/.env):
    GEMINI_API_KEY    — required
    ALSA_DEVICE       — audio output device (default: "default")
    MQTT_BROKER_HOST  — broker address (default: "localhost")
    MQTT_PORT         — broker port (default: 1883)
"""

import json
import os
import signal
import sys
import threading
from datetime import datetime
from pathlib import Path

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

# narrator.py lives in the same directory; ensure it's importable when this
# script is invoked from the project root (e.g. python pi/test_narrator.py).
sys.path.insert(0, str(Path(__file__).parent))

from narrator import (  # noqa: E402
    get_last_narrations,
    init_db,
    narrate,
    save_narration,
    speak_async,
    time_description,
)

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MQTT_BROKER_HOST: str = os.environ.get("MQTT_BROKER_HOST", "localhost")
MQTT_PORT: int = int(os.environ.get("MQTT_PORT", 1883))
MQTT_TOPIC: str = "environment/sensors"

# Plausible physical ranges for payload validation (from docs/mqtt-schema.md).
VALID_RANGES: dict[str, tuple[float, float]] = {
    "temperature_c": (-40.0, 85.0),
    "humidity_pct": (0.0, 100.0),
    "light_lux": (0.0, 1500.0),
}

# ---------------------------------------------------------------------------
# MQTT callbacks
# ---------------------------------------------------------------------------


def on_connect(
    client: mqtt.Client,
    userdata: object,
    connect_flags: mqtt.ConnectFlags,
    reason_code: mqtt.ReasonCode,
    properties: mqtt.Properties,
) -> None:
    if not reason_code.is_failure:
        print(f"[test] connected to {MQTT_BROKER_HOST}:{MQTT_PORT}", flush=True)
        client.subscribe(MQTT_TOPIC)
        print(f"[test] subscribed to {MQTT_TOPIC}", flush=True)
        print("[test] waiting for messages — power on the ESP32 now.", flush=True)
    else:
        print(f"[test] connection failed: {reason_code}", flush=True)


def on_disconnect(
    client: mqtt.Client,
    userdata: object,
    disconnect_flags: mqtt.DisconnectFlags,
    reason_code: mqtt.ReasonCode,
    properties: mqtt.Properties,
) -> None:
    print(f"[test] disconnected ({reason_code})", flush=True)


def on_message(
    client: mqtt.Client,
    userdata: object,
    message: mqtt.MQTTMessage,
) -> None:
    """Validate incoming sensor payload and run the full narration pipeline."""
    timestamp = datetime.now().strftime("%H:%M:%S")

    try:
        payload = json.loads(message.payload.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"[test {timestamp}] malformed payload: {exc}", flush=True)
        return

    print(f"[test {timestamp}] received: {payload}", flush=True)

    # Validate all three required fields before proceeding.
    parsed: dict[str, float] = {}
    for key, (lo, hi) in VALID_RANGES.items():
        raw = payload.get(key)
        if raw is None:
            print(f"[test] missing field '{key}' — discarding message", flush=True)
            return
        try:
            value = float(raw)
        except (TypeError, ValueError):
            print(
                f"[test] field '{key}' is not numeric ({raw!r}) — discarding message",
                flush=True,
            )
            return
        if not lo <= value <= hi:
            print(
                f"[test] field '{key}'={value} outside [{lo}, {hi}] — discarding message",
                flush=True,
            )
            return
        parsed[key] = value

    now = datetime.now()
    time_desc = time_description(now.hour)
    last_narrations = get_last_narrations()

    print(f"[test] valid message — calling Gemini ({time_desc})...", flush=True)
    text = narrate(parsed, time_desc, now.hour, last_narrations)
    if text is None:
        print("[test] narration skipped (API error or safety block).", flush=True)
        return

    print(f"[test] narration: {text}", flush=True)
    save_narration(parsed, text, time_desc)
    print("[test] saved to memory.db", flush=True)

    speak_async(text)
    print("[test] speaking...", flush=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    init_db()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    print(
        f"[test] connecting to MQTT broker at {MQTT_BROKER_HOST}:{MQTT_PORT} ...",
        flush=True,
    )
    client.connect(MQTT_BROKER_HOST, MQTT_PORT)
    client.loop_start()

    stop_event = threading.Event()

    def _handle_signal(sig: int, frame: object) -> None:
        print(f"\n[test] shutting down (signal {sig}).", flush=True)
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    stop_event.wait()
    client.loop_stop()
    client.disconnect()
    print("[test] stopped.", flush=True)


if __name__ == "__main__":
    main()
