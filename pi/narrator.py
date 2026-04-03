"""
narrator.py — standalone environment narration script.

Reads hardcoded sensor values, queries Gemini for a two-sentence poetic
description of the room, synthesizes speech with Piper TTS, and plays it
via aplay. Stores narrations in memory.db and injects recent history for
continuity of voice.

Run:
    # Create pi/.env with your API key:
    #   GEMINI_API_KEY=AIza...
    python pi/narrator.py

Dependencies:
    pip install -r pi/requirements.txt
    # Piper TTS binary: pip install piper-tts  OR  download from
    # https://github.com/rhasspy/piper/releases

Model files:
    pi/models/en_US-lessac-medium.onnx
    pi/models/en_US-lessac-medium.onnx.json
"""

import json
import os
import sqlite3
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from google import genai

load_dotenv()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MODEL_PATH = str(Path(__file__).parent / "models" / "en_US-lessac-medium.onnx")
_GEMINI_MODEL = "gemini-2.5-flash"  # verify at ai.google.dev/gemini-api/docs/models
_ALSA_DEVICE = os.environ.get("ALSA_DEVICE", "default")
_DB_PATH = str(Path(__file__).parent / "memory.db")

# ---------------------------------------------------------------------------
# Hardcoded sensor values (replace with MQTT-sourced values in step 5)
# ---------------------------------------------------------------------------

HARDCODED_SENSORS = {
    "temperature_c": 22.4,
    "humidity_pct": 55.0,
    "light_lux": 300.0,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def time_description(hour: int) -> str:
    if 5 <= hour < 8:
        return "early morning"
    if 8 <= hour < 12:
        return "morning"
    if 12 <= hour < 14:
        return "midday"
    if 14 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 20:
        return "evening"
    if 20 <= hour < 23:
        return "night"
    return "deep night"


def temperature_feel(temp_c: float) -> str:
    if temp_c < 16:
        return "cold"
    if temp_c < 20:
        return "cool"
    if temp_c < 24:
        return "comfortable"
    if temp_c < 28:
        return "warm"
    return "hot"


def light_feel(lux: float) -> str:
    if lux < 10:
        return "dark"
    if lux < 50:
        return "dim"
    if lux < 200:
        return "soft"
    if lux < 500:
        return "bright"
    if lux < 1000:
        return "vivid"
    return "glaring"


def circadian_tone(hour: int) -> str:
    """Return a mood phrase for the LLM prompt based on time of day."""
    if 5 <= hour < 8:
        return "alert and full of potential — the world is just beginning"
    if 8 <= hour < 12:
        return "purposeful — the day is gathering itself"
    if 12 <= hour < 14:
        return "unhurried — suspended in the fullness of midday"
    if 14 <= hour < 17:
        return "warm and slightly drowsy — the afternoon settling"
    if 17 <= hour < 20:
        return "tender and wistful — the light drawing back"
    if 20 <= hour < 23:
        return "melancholic and intimate — the world contracting inward"
    return "contemplative and still — the deep hours of the night"


# ---------------------------------------------------------------------------
# Memory (SQLite)
# ---------------------------------------------------------------------------


def init_db() -> None:
    """Create the narrations table if it doesn't exist. Enable WAL mode."""
    with sqlite3.connect(_DB_PATH) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS narrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_at TEXT NOT NULL,
                sensor_json TEXT NOT NULL,
                narration_text TEXT NOT NULL,
                time_description TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_narrations_recorded_at "
            "ON narrations(recorded_at DESC)"
        )
        conn.commit()


def save_narration(sensor_readings: dict, text: str, time_desc: str) -> None:
    """Insert a narration and prune records beyond the most recent 200."""
    recorded_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sensor_json = json.dumps(sensor_readings)
    with sqlite3.connect(_DB_PATH) as conn:
        conn.execute(
            "INSERT INTO narrations "
            "(recorded_at, sensor_json, narration_text, time_description) "
            "VALUES (?, ?, ?, ?)",
            (recorded_at, sensor_json, text, time_desc),
        )
        conn.execute(
            "DELETE FROM narrations WHERE id NOT IN "
            "(SELECT id FROM narrations ORDER BY recorded_at DESC LIMIT 200)"
        )
        conn.commit()


def get_last_narrations(n: int = 3) -> str:
    """Return the last n narrations as a plain-text block, oldest first.

    Format: [YYYY-MM-DD HH:MM] <narration text>
    Returns an empty string if the table is empty.
    """
    with sqlite3.connect(_DB_PATH) as conn:
        rows = conn.execute(
            "SELECT recorded_at, narration_text FROM narrations "
            "ORDER BY recorded_at DESC LIMIT ?",
            (n,),
        ).fetchall()
    if not rows:
        return ""
    lines = []
    for recorded_at, narration_text in reversed(rows):  # oldest first
        try:
            dt = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
            label = dt.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            label = recorded_at
        lines.append(f"[{label}] {narration_text}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# LLM narration
# ---------------------------------------------------------------------------


def build_sensor_context(readings: dict) -> dict:
    """Enrich raw sensor readings with qualitative labels for the LLM."""
    return {
        "temperature_c": readings["temperature_c"],
        "humidity_pct": readings["humidity_pct"],
        "light_lux": readings["light_lux"],
        "temperature_feel": temperature_feel(readings["temperature_c"]),
        "light_feel": light_feel(readings["light_lux"]),
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def narrate(
    readings: dict,
    time_desc: str,
    hour: int,
    last_narrations: str = "",
) -> str | None:
    """
    Call Gemini and return a two-sentence room narration, or None on failure.

    Args:
        readings: Raw sensor dict with temperature_c, humidity_pct, light_lux.
        time_desc: Human time-of-day string from time_description().
        hour: Current hour (0-23), used to shape circadian tone.
        last_narrations: Newline-separated recent narration history (may be empty).

    Returns:
        Narration string, or None if the API call fails or is safety-blocked.
    """
    sensor_context = build_sensor_context(readings)
    sensor_json = json.dumps(sensor_context, indent=2)

    history_block = (
        f"\nRecent narrations (do not repeat, let them inform your voice):\n{last_narrations}"
        if last_narrations.strip()
        else ""
    )

    prompt = (
        "You are the voice of a room. Speak in exactly two sentences.\n"
        "Never mention numbers, measurements, degrees, percentages, or lux.\n"
        "Use only sensory and emotional language — what the space feels like, not what it measures.\n"
        f"Current conditions: {sensor_json}\n"
        f"Time of day: {time_desc} — {circadian_tone(hour)}\n"
        f"{history_block}"
    )

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY environment variable not set.", flush=True)
        return None

    client = genai.Client(api_key=api_key)

    try:
        response = client.models.generate_content(
            model=_GEMINI_MODEL,
            contents=prompt,
        )
        return response.text.strip()
    except ValueError as exc:
        # Gemini raises ValueError when safety filters block the response.
        print(f"Gemini safety block or empty response: {exc}", flush=True)
        return None
    except Exception as exc:  # noqa: BLE001
        print(f"Gemini API error: {exc}", flush=True)
        return None


# ---------------------------------------------------------------------------
# Text-to-speech
# ---------------------------------------------------------------------------


def speak(text: str, model_path: str = _MODEL_PATH) -> None:
    """Synthesize text with Piper TTS and play via aplay (blocking)."""
    result = subprocess.run(
        ["piper", "--model", model_path, "--output_file", "/tmp/narration.wav"],
        input=text.encode(),
        capture_output=True,
    )
    if result.returncode != 0:
        print(f"Piper error: {result.stderr.decode()}", flush=True)
        return

    # Use `aplay -l` to find the correct card name for your USB sound card.
    result_play = subprocess.run(
        ["aplay", "-D", _ALSA_DEVICE, "/tmp/narration.wav"],
        capture_output=True,
    )
    if result_play.returncode != 0:
        print(f"aplay error: {result_play.stderr.decode()}", flush=True)


_speaking = threading.Event()


def speak_async(text: str) -> None:
    """Fire-and-forget TTS. Drops narration if one is already playing."""
    if _speaking.is_set():
        # Ambient silence is better than a backlog of stale narrations.
        return

    def _run() -> None:
        _speaking.set()
        try:
            speak(text)
        finally:
            _speaking.clear()

    threading.Thread(target=_run, daemon=True).start()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _run_once() -> None:
    """Run a single narration cycle synchronously (for standalone testing)."""
    init_db()
    now = datetime.now()
    time_desc = time_description(now.hour)
    last_narrations = get_last_narrations()

    print(f"[narrator] time={time_desc}  sensors={HARDCODED_SENSORS}", flush=True)

    text = narrate(HARDCODED_SENSORS, time_desc, now.hour, last_narrations)
    if text is None:
        print("[narrator] Narration skipped (API error or safety block).", flush=True)
        return

    print(f"[narrator] {text}", flush=True)
    save_narration(HARDCODED_SENSORS, text, time_desc)
    speak(text)


if __name__ == "__main__":
    _run_once()
