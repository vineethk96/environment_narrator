"""
mqtt_subscriber.py — MQTT-driven environment narrator.

Subscribes to the 'environment/sensors' topic on the local Mosquitto broker,
validates and aggregates incoming sensor readings, and triggers narration via
narrator.py when the 10-minute schedule fires or any reading crosses its delta
threshold since the last narration.

This is the production entry point. Run:
    python pi/mqtt_subscriber.py

For standalone testing (no MQTT required), use narrator.py directly.

Mosquitto setup (one-time, on the Pi):
    sudo apt install mosquitto mosquitto-clients
    sudo systemctl enable --now mosquitto

Environment variables (pi/.env or system):
    GEMINI_API_KEY    — required (see pi/.env.example)
    ALSA_DEVICE       — audio output device (default: "default")
    MQTT_BROKER_HOST  — broker address (default: "localhost")
    MQTT_PORT         — broker port (default: 1883)
"""

import json
import os
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

# narrator.py lives in the same directory; ensure it's importable when this
# script is invoked from the project root (e.g. python pi/mqtt_subscriber.py).
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

NARRATION_INTERVAL: int = 600  # seconds between scheduled narrations (10 min)
STALE_THRESHOLD_S: int = 60    # seconds before a sensor reading is considered stale

# Delta thresholds — unscheduled narration fires if any sensor crosses these
# since the last narration.
TEMP_DELTA: float = 2.0      # °C
HUMIDITY_DELTA: float = 10.0  # % RH
LIGHT_DELTA: float = 20.0    # lux

# Plausible physical ranges for payload validation (from docs/mqtt-schema.md).
VALID_RANGES: dict[str, tuple[float, float]] = {
    "temperature_c": (-40.0, 85.0),
    "humidity_pct": (0.0, 100.0),
    "light_lux": (0.0, 1500.0),
}

# ---------------------------------------------------------------------------
# Shared state  (all access must be under _lock)
# ---------------------------------------------------------------------------

_lock = threading.Lock()

readings: dict[str, float | None] = {
    "temperature_c": None,
    "humidity_pct": None,
    "light_lux": None,
}

# Snapshot of readings at the time of the last narration, used for delta checks.
last_narration_readings: dict[str, float | None] = {
    "temperature_c": None,
    "humidity_pct": None,
    "light_lux": None,
}

# Epoch-seconds of the most recent valid update for each sensor.
sensor_timestamps: dict[str, float] = {
    "temperature_c": 0.0,
    "humidity_pct": 0.0,
    "light_lux": 0.0,
}

# ---------------------------------------------------------------------------
# State helpers  (call with _lock held)
# ---------------------------------------------------------------------------


def _all_readings_present() -> bool:
    return all(readings[k] is not None for k in readings)


def _any_stale() -> bool:
    return any(
        time.time() - sensor_timestamps[k] > STALE_THRESHOLD_S
        for k in sensor_timestamps
    )


def _delta_exceeded() -> bool:
    """Return True if any sensor crossed its threshold since the last narration.

    Also returns True when there has been no prior narration — the first
    complete reading set triggers immediately rather than waiting for the
    10-minute timer.
    """
    if any(last_narration_readings[k] is None for k in last_narration_readings):
        return True  # no previous narration — treat first reading as threshold crossed
    checks = (
        (readings["temperature_c"], last_narration_readings["temperature_c"], TEMP_DELTA),
        (readings["humidity_pct"], last_narration_readings["humidity_pct"], HUMIDITY_DELTA),
        (readings["light_lux"], last_narration_readings["light_lux"], LIGHT_DELTA),
    )
    return any(
        current is not None and prev is not None and abs(current - prev) >= threshold
        for current, prev, threshold in checks
    )


# ---------------------------------------------------------------------------
# Narration trigger  (call with _lock held)
# ---------------------------------------------------------------------------


def maybe_narrate() -> None:
    """Run one narration cycle if readings are ready.

    Must be called with _lock held.
    """
    if not _all_readings_present():
        print(
            "[subscriber] waiting for all sensors before narrating.",
            flush=True,
        )
        return

    stale = _any_stale()
    current: dict[str, float] = {k: readings[k] for k in readings}  # type: ignore[assignment]

    now = datetime.now()
    time_desc = time_description(now.hour)
    last_narrations = get_last_narrations()

    stale_note = "  [sensors partially available — some readings stale]" if stale else ""
    print(
        f"[subscriber] narrating  time={time_desc}  sensors={current}{stale_note}",
        flush=True,
    )

    text = narrate(current, time_desc, now.hour, last_narrations)
    if text is None:
        print("[subscriber] narration skipped (API error or safety block).", flush=True)
        return

    print(f"[subscriber] {text}", flush=True)
    save_narration(current, text, time_desc)

    # Update delta baseline for future threshold comparisons.
    last_narration_readings.update(current)

    speak_async(text)


# ---------------------------------------------------------------------------
# Scheduled narration (self-rescheduling timer, runs every NARRATION_INTERVAL)
# ---------------------------------------------------------------------------


def _schedule_narration() -> None:
    with _lock:
        maybe_narrate()
    threading.Timer(NARRATION_INTERVAL, _schedule_narration).start()


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
        print(
            f"[mqtt] connected to {MQTT_BROKER_HOST}:{MQTT_PORT}",
            flush=True,
        )
        client.subscribe(MQTT_TOPIC)
        print(f"[mqtt] subscribed to {MQTT_TOPIC}", flush=True)
    else:
        print(f"[mqtt] connection failed: {reason_code}", flush=True)


def on_disconnect(
    client: mqtt.Client,
    userdata: object,
    disconnect_flags: mqtt.DisconnectFlags,
    reason_code: mqtt.ReasonCode,
    properties: mqtt.Properties,
) -> None:
    print(
        f"[mqtt] disconnected ({reason_code}) — paho will auto-reconnect",
        flush=True,
    )


def on_message(
    client: mqtt.Client,
    userdata: object,
    message: mqtt.MQTTMessage,
) -> None:
    """Parse incoming sensor JSON, update shared state, and trigger if warranted."""
    try:
        payload = json.loads(message.payload.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"[mqtt] malformed payload: {exc}", flush=True)
        return

    # Validate all three required fields before touching shared state.
    parsed: dict[str, float] = {}
    for key, (lo, hi) in VALID_RANGES.items():
        raw = payload.get(key)
        if raw is None:
            print(
                f"[mqtt] missing field '{key}' — discarding message",
                flush=True,
            )
            return
        try:
            value = float(raw)
        except (TypeError, ValueError):
            print(
                f"[mqtt] field '{key}' is not numeric ({raw!r}) — discarding message",
                flush=True,
            )
            return
        if not lo <= value <= hi:
            print(
                f"[mqtt] field '{key}'={value} outside [{lo}, {hi}] — discarding message",
                flush=True,
            )
            return
        parsed[key] = value

    now_ts = time.time()
    should_narrate = False

    with _lock:
        readings.update(parsed)
        for key in parsed:
            sensor_timestamps[key] = now_ts

        if _all_readings_present() and _delta_exceeded():
            should_narrate = True

    if should_narrate:
        print("[subscriber] delta threshold crossed — triggering narration.", flush=True)
        with _lock:
            maybe_narrate()


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
        f"[subscriber] connecting to MQTT broker at {MQTT_BROKER_HOST}:{MQTT_PORT}",
        flush=True,
    )
    client.connect(MQTT_BROKER_HOST, MQTT_PORT)
    client.loop_start()  # MQTT I/O runs in a background thread

    # First scheduled narration fires after NARRATION_INTERVAL; subsequent
    # ones are triggered by _schedule_narration rescheduling itself.
    threading.Timer(NARRATION_INTERVAL, _schedule_narration).start()

    # Block the main thread until SIGINT or SIGTERM.
    stop_event = threading.Event()

    def _handle_signal(sig: int, frame: object) -> None:
        print(f"\n[subscriber] received signal {sig} — shutting down.", flush=True)
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    stop_event.wait()
    client.loop_stop()
    client.disconnect()
    print("[subscriber] stopped.", flush=True)


if __name__ == "__main__":
    main()
