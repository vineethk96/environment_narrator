# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**environment_narrator** is an IoT project that "gives the environment a voice." It streams sensor data over MQTT to a Raspberry Pi, which queries an LLM to generate a contextual, natural-language interpretation of its surroundings.

## Architecture

The system has three layers:

1. **Sensors** — Collect environmental data (temperature, humidity, etc.) and publish readings over MQTT.
2. **Raspberry Pi hub** — Subscribes to MQTT topics, batches or processes incoming sensor data, and invokes an LLM API to produce a narrative description.
3. **LLM integration** — Takes structured sensor readings as context and returns a human-readable interpretation.

The `.gitignore` is configured for C/C++, suggesting the implementation will be compiled C or C++.

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

## Key Design Decisions (to be preserved as code is added)

- MQTT is the transport protocol between sensors and the Pi hub.
- The LLM query is triggered on the Pi side, not on the sensor nodes.
- The project targets a Raspberry Pi as the edge compute device, so resource constraints apply.
