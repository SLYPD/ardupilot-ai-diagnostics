# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

An AI-powered ArduPilot/MAVLink post-mortem telemetry diagnostic engine. The pipeline scans flight logs for hardware anomalies (voltage sags, vibration spikes, max-thrust events, motor imbalance, IMU clipping) against thresholds loaded from a modular component registry, then feeds localized anomaly windows to the DeepSeek API for structured root-cause analysis.

**Input ingestion** natively targets binary MAVLink message frames (.bin Dataflash and .tlog telemetry) via pymavlink, in addition to CSV. The ingestion layer (`parser.parse_binary_log()`) extracts BAT, POWR, VIBE, ATT, and RCOU messages from the raw log, maps them to the internal column schema, and merges independent message streams onto a unified timeline using forward-fill before anomaly detection.

## Architecture

**Three layers, assembled at runtime:**

1. **Component Registry** (`components/`) — each JSON file owns a slice of the hardware profile schema. Three categories:
   - `flight_controllers/` — FCU metadata, VCC rail limits, vibration thresholds, IMU config
   - `power_systems/` — battery chemistry, cell count, voltage ranges, sag-detection parameters
   - `propulsion/` — airframe type, motor layout, motor pairs, PWM redline, imbalance thresholds, context window
2. **Template Builder** (`template_builder.py`) — loads component JSONs by name, deep-merges them (propulsion → FC → power overlay order), stamps the combined `profile_id`, and returns the unified Active Hardware Profile dict.
3. **Two-phase pipeline** (unchanged):
   - `parser.py` — receives the assembled profile dict, scans a telemetry log against the profile's thresholds, extracts context windows around each anomaly. Accepts .csv, .bin (Dataflash), and .tlog (Telemetry) formats — .bin/.tlog files are routed through `parse_binary_log()` which uses pymavlink to extract BAT/POWR/VIBE/ATT/RCOU message frames and merge them into a unified timeline.
   - `main.py` — calls template_builder, feeds the profile dict to both the parser and `format_profile_for_llm()`, bundles the formatted text with `docs/*.md` into the system prompt, then calls the DeepSeek API for each anomaly window.

**Data flow:**
```
--fc pixhawk_6c --power 6s_lipo --propulsion pwm_standard
         │                    │                    │
         ▼                    ▼                    ▼
  components/           components/          components/
  flight_controllers/   power_systems/       propulsion/
         │                    │                    │
         └────────────────────┼────────────────────┘
                              ▼
                    template_builder.py  (deep-merge)
                              │
                    ┌─────────┴─────────┐
                    ▼                   ▼
              parser.py            main.py
          (thresholds)       (format_profile_for_llm → LLM system prompt)
```

## Directory Structure

```
components/
  flight_controllers/
    pixhawk_6c.json          # Pixhawk 6C Mini: VCC 4.9–5.3V, vibe Z 45, BMI088+ICM-42688-P
    cube_orange.json          # Cube Orange+: VCC 4.8–5.4V, vibe Z 60, triple IMU, dual baro
  power_systems/
    6s_lipo.json              # 6S LiPo: 22.2V nom, 25.2V max, 19.8V min, sag 0.95/1.5
    12s_lipo.json             # 12S LiPo: 44.4V nom, 50.4V max, 39.6V min, sag 0.93/1.6
  propulsion/
    pwm_standard.json         # Hexa-X 6-motor: PWM redline 1940, imbalance 1700/1200
    dshot600.json             # X8 coaxial 8-motor: PWM redline 2000, imbalance 1800/1200
template_builder.py           # Deep-merge engine + --list / --fc / --power / --propulsion CLI
docs/
  ARCHITECTURE.md             # Pipeline overview, LLM scope constraints
  DATA_SCHEMA.md              # ArduPilot variable dictionary (BAT, RCOU, VIBE, ATT)
  DIAGNOSTIC_RULES.md         # Anti-hallucination axioms + diagnostic boundaries
```

## Commands

```bash
# Install dependencies
pip install pandas requests pymavlink streamlit

# Run with defaults (pixhawk_6c + 6s_lipo + pwm_standard)
python main.py

# Run with specific components
python main.py --fc cube_orange --power 12s_lipo --propulsion dshot600

# List all available components and their defaults
python main.py --list
python template_builder.py --list

# Run parser standalone (offline, no API calls)
python parser.py
python parser.py --fc cube_orange --power 6s_lipo --propulsion pwm_standard

# Inspect the assembled profile JSON directly
python template_builder.py
python template_builder.py --fc cube_orange --power 12s_lipo --propulsion dshot600
```

## Component Schema

Each component JSON owns a subset of the final profile. `template_builder.deep_merge()` recursively combines them — overlapping keys are resolved by merge order (propulsion base → FC overlay → power overlay).

| Component category | Keys it owns |
|---|---|
| `flight_controllers/` | `flight_controller.*`, `power_system.vcc`, `thresholds.vibration`, `thresholds.imu_clipping` |
| `power_systems/` | `power_system.battery`, `thresholds.voltage_sag` |
| `propulsion/` | `airframe.*`, `thresholds.pwm`, `thresholds.motor_imbalance`, `context_window`, `profile` (base) |

**To add a new component:** drop a JSON file into the matching subdirectory with the relevant subset of keys. The template_builder automatically discovers it (no registration needed). For example, adding a `power_systems/4s_lion.json` makes it immediately available via `--power 4s_lion`.

## Configuration

- **API Key:** Set `DEEPSEEK_API_KEY` environment variable before running `main.py`
- **Target log:** Edit `TARGET_LOG` in `main.py` (default: `flight_log_01.csv`)
- **API endpoint:** `main.py` line 15 — swap the commented local DeepClaude URL for offline testing
- **Defaults:** `template_builder.py` lines 23–25 (`DEFAULT_FC`, `DEFAULT_POWER`, `DEFAULT_PROPULSION`); change these to set different factory defaults

## Agentic Self-Review

After completing any non-trivial refactor, addition, or deletion across the
pipeline (parser, template_builder, main, app, or component JSONs), you MUST
autonomously run the codebase audit before declaring the task complete:

```bash
python audit_codebase.py
```

**What it checks:** `ruff` lints for style, bug-prone patterns, and complexity;
`vulture` scans for dead (unreachable / unused) code.

**Follow-through rule:** If `audit_codebase.py` reports any issue that your
change introduced, fix it in the same turn. Only skip pre-existing, unrelated
findings — and when you do, call them out explicitly in your response so the
user knows what you chose not to touch.
