---
title: ALSA "Unknown PCM Headphones" Error in aplay Audio Playback
date: 2026-04-01
category: runtime-errors
module: narrator
problem_type: runtime_error
component: tooling
severity: high
symptoms:
  - "aplay exits with error: Unknown PCM Headphones"
  - "Audio playback fails silently or with stderr output from aplay"
  - "Error appears despite the card being visible in aplay -l output"
root_cause: config_error
resolution_type: config_change
tags:
  - alsa
  - audio-playback
  - raspberry-pi
  - aplay
  - environment-variables
  - pcm-specifier
related_components:
  - environment_setup
---

# ALSA "Unknown PCM Headphones" Error in aplay Audio Playback

## Problem

The `narrator.py` script fails to play Piper TTS audio on Raspberry Pi because `aplay` cannot locate the ALSA device when `ALSA_DEVICE` is set to just the card name (e.g., `Headphones`) instead of the full PCM specifier format ALSA requires.

## Symptoms

- `aplay` exits with: `Unknown PCM Headphones`
- Audio narration produces no output — script continues but no sound plays
- Error appears in stderr even though the card is listed in `aplay -l`
- Error is logged by narrator.py's subprocess error handling

## What Didn't Work

- **Setting `ALSA_DEVICE=Headphones`** — ALSA interprets this as a PCM device name, not a card identifier. Results in "Unknown PCM Headphones" because there is no PCM named "Headphones" in the ALSA config.
- **Setting `ALSA_DEVICE=default`** — Works generically but routes to the system default device, which may not be the intended output on multi-audio-device systems.

## Solution

Set `ALSA_DEVICE` to the full `plughw` PCM specifier that includes both the card name and device number.

**Step 1: Identify your card name and device number**

```bash
aplay -l
```

Example output:
```
**** List of PLAYBACK Hardware Devices ****
card 0: Headphones [bcm2835 Headphones], device 0: bcm2835 Headphones [bcm2835 Headphones]
  Subdevices: 1/1
  Subdevice #0: subdevice #0
```

The card name is the value in the first set of brackets: `Headphones`. The device number is `0`.

**Step 2: Form the PCM specifier**

```
plughw:CARD=<card-name>,DEV=<device-number>
```

**Step 3: Set in `.env`**

```bash
# Before (broken):
ALSA_DEVICE=Headphones

# After (working):
ALSA_DEVICE=plughw:CARD=Headphones,DEV=0
```

The narrator reads this value at line 41 of `pi/narrator.py`:

```python
_ALSA_DEVICE = os.environ.get("ALSA_DEVICE", "default")
```

And passes it to `aplay` at the playback call:

```python
result_play = subprocess.run(
    ["aplay", "-D", _ALSA_DEVICE, "/tmp/narration.wav"],
    capture_output=True,
)
```

## Why This Works

ALSA distinguishes between two device naming formats:

- **Card name only** (`Headphones`): Interpreted as a PCM device name in the ALSA software configuration layer. No such PCM exists unless explicitly defined in `~/.asoundrc` or `/etc/asound.conf`.
- **Full PCM specifier** (`plughw:CARD=Headphones,DEV=0`): Directly addresses the hardware card and device, bypassing the PCM name lookup.

The `plughw` prefix (vs bare `hw`) adds automatic format conversion via ALSA plugins — it handles sample rate, bit depth, and channel count mismatches between the WAV file and the hardware, making playback more robust.

## Prevention

- Always use the full `plughw:CARD=<name>,DEV=<num>` format in ALSA-based audio applications, not just the card name.
- The `pi/.env.example` file documents this format with the Raspberry Pi bcm2835 example — keep it updated if the audio setup changes.
- When `aplay` fails, check stderr output (narrator.py logs this) — the ALSA error message will indicate whether the issue is a device format error vs a missing device.
- Run `aplay -l` on a new system before configuring `ALSA_DEVICE` to confirm the exact card name in brackets.

## Related Issues

- `pi/.env.example` — inline configuration documentation with correct format example
- `pi/narrator.py` — `_ALSA_DEVICE` constant and `aplay` subprocess call
