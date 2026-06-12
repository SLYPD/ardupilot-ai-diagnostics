# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

An AI-powered ArduPilot/MAVLink post-mortem telemetry diagnostic engine. The pipeline scans flight logs for hardware anomalies (voltage sags, vibration spikes, max-thrust events, motor imbalance, IMU clipping, ATT desync, VCC fluctuation, stuck motors) against thresholds loaded from a modular component registry, then feeds localized anomaly windows to the DeepSeek API for structured root-cause analysis.

**Input ingestion** natively targets binary MAVLink message frames (.bin Dataflash and .tlog telemetry) via pymavlink, in addition to CSV. The ingestion layer (`parser.parse_binary_log()`) extracts BAT, POWR, VIBE, ATT, and RCOU messages from the raw log, maps them to the internal column schema, and merges independent message streams onto a unified timeline using forward-fill before anomaly detection.

**Scope:** This system is a threshold-based scanner for ArduPilot Copter
multirotors (4–8 motors, LiPo, PWM/DShot ESCs).  It does NOT support
fixed-wing, helicopter, Betaflight, PX4, or any non-ArduPilot firmware.
See [`PROJECT_SCOPE.md`](PROJECT_SCOPE.md) for the full scope boundary —
what aircraft types, battery chemistries, ESC protocols, and flight styles
are in and out of scope.

## Architecture

**Three layers, assembled at runtime:**

1. **Component Registry** (`components/`) — each JSON file owns a slice of the hardware profile schema. Three categories:
   - `flight_controllers/` — FCU metadata, VCC rail limits, vibration thresholds, IMU config (5 profiles: pixhawk_6c, pixhawk_6x, cube_orange, cube_blue, durandal)
   - `power_systems/` — battery chemistry, cell count, voltage ranges, sag-detection parameters (12 profiles: 1S–12S LiPo)
   - `propulsion/` — airframe type, motor layout, motor pairs, PWM redline, imbalance thresholds, ATT desync config, context window (6 profiles: pwm_standard, dshot300, dshot600, quad_x, y6_coaxial, x8_flat_octo)
2. **Template Builder** (`template_builder.py`) — loads component JSONs by name, deep-merges them (propulsion → FC → power overlay order), stamps the combined `profile_id`, and returns the unified Active Hardware Profile dict.
3. **Pipeline**:
   - `parser.py` — receives the assembled profile dict, scans a telemetry log against the profile's thresholds, extracts context windows around each anomaly. Accepts .csv, .bin (Dataflash), and .tlog (Telemetry) formats — .bin/.tlog files are routed through `parse_binary_log()` which uses pymavlink to extract BAT/POWR/VIBE/ATT/RCOU message frames and merge them into a unified timeline.
   - `main.py` — calls template_builder, feeds the profile dict to both the parser and `format_profile_for_llm()`, bundles the formatted text with `docs/*.md` into the system prompt, then calls the DeepSeek API. Anomaly windows are diagnosed in **parallel** via `diagnose_anomalies_parallel()` (`ThreadPoolExecutor`, configurable concurrency via `DEEPSEEK_MAX_CONCURRENCY` env var, default 5). Retry logic (3 attempts, exponential backoff) is built into the shared utility — individual failures never crash the batch.
   - Auto-detection: when no explicit `--fc`/`--power`/`--propulsion` flags are given, `auto_detect_profile()` reads frame type, battery voltage, and motor count from binary logs to suggest matching components. Wired into CLI, Streamlit, and FastAPI entry points.

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
    pixhawk_6x.json          # Pixhawk 6X: VCC 4.9–5.3V, vibe Z 50, triple IMU, dual ICP-20100
    cube_orange.json          # Cube Orange+: VCC 4.8–5.4V, vibe Z 60, triple IMU, dual baro
    cube_blue.json            # Cube Blue: VCC 4.7–5.3V, vibe Z 55, triple IMU (2x ICM-20689 + BMX055)
    durandal.json             # Holybro Durandal: VCC 4.8–5.3V, vibe Z 45, dual IMU
  power_systems/
    1s_lipo.json .. 12s_lipo.json  # 1S–12S LiPo (12 profiles)
  propulsion/
    pwm_standard.json         # Hexa-X 6-motor: PWM redline 1940, imbalance 1600/1300
    dshot300.json             # Hexa-X 6-motor: DShot300, redline 1950, imbalance 1700/1300
    dshot600.json             # X8 coaxial 8-motor: DShot600, redline 1950, imbalance 1700/1300
    quad_x.json               # Quad-X 4-motor: PWM redline 1900, imbalance 1600/1300
    y6_coaxial.json           # Y6 coaxial 6-motor: DShot600, redline 1950
    x8_flat_octo.json         # Octa-X flat 8-motor: DShot600, redline 1950
template_builder.py           # Deep-merge engine + component name validation
parser.py                     # Anomaly scanner (9 detector categories)
main.py                       # CLI orchestrator + LLM integration
app.py                        # Streamlit UI
api.py                        # FastAPI REST dashboard
audit_codebase.py             # Self-audit: ruff + vulture + bandit + pip-audit
generate_mock_log.py          # Mock telemetry CSV generator for testing
PROJECT_SCOPE.md              # Human-readable scope boundary — what the system can/can't diagnose
docs/
  ARCHITECTURE.md             # Pipeline overview, LLM scope constraints
  DATA_SCHEMA.md              # ArduPilot variable dictionary (BAT, RCOU, VIBE, ATT)
  DIAGNOSTIC_RULES.md         # Anti-hallucination axioms + diagnostic boundaries
tests/
  test_template_builder.py    # 14 tests: component loading, deep merge, path traversal safety
  test_parser.py              # 22 tests: all 9 anomaly detectors + input validation
  test_main.py                # 8 tests: parallel diagnostic utility, retry logic, error isolation
.github/workflows/
  ci.yml                      # CI: install → mock log → pytest → audit
  dependency-audit.yml        # Weekly pip-audit (Monday 6:37am)
```

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run with defaults (pixhawk_6c + 6s_lipo + pwm_standard)
python main.py

# Run with specific components
python main.py --fc cube_orange --power 12s_lipo --propulsion dshot600

# Run with a specific log file
python main.py --csv path/to/log.bin
python parser.py --csv path/to/log.tlog

# Batch process a directory
python main.py --dir logs/
python parser.py --dir logs/

# List all available components and their defaults
python main.py --list
python template_builder.py --list

# Run parser standalone (offline, no API calls)
python parser.py
python parser.py --fc cube_orange --power 6s_lipo --propulsion pwm_standard

# Inspect the assembled profile JSON directly
python template_builder.py
python template_builder.py --fc cube_blue --power 3s_lipo --propulsion quad_x

# Run the FastAPI dashboard
uvicorn api:app --reload --port 8000

# Run tests
python -m pytest tests/ -v

# Run codebase audit (ruff + vulture + bandit + pip-audit)
python audit_codebase.py
```

## 9 Anomaly Detection Categories

| # | Label prefix | What it detects | Profile-driven threshold |
|---|---|---|---|
| A | `VCC_DROP` / `VCC_OVER_VOLT` | FC board voltage outside safe range | `power_system.vcc.min_v` / `max_v` |
| B | `VOLTAGE_SAG` | BAT.Volt drop (rolling median ratio OR below absolute min) | `thresholds.voltage_sag.*` |
| C | `MAX_THRUST_*` | Any motor PWM ≥ redline | `thresholds.pwm.redline` |
| D | `IMBALANCE_*_vs_*` | Opposing motor pair divergence (3+ consecutive rows) | `thresholds.motor_imbalance.*` |
| E | `VIBE_X/Y/Z_SPIKE` | Vibration exceeding axis limits | `thresholds.vibration.*` |
| F | `IMU_CLIP_*` | Any IMU clipping count > 0 | `thresholds.imu_clipping.max_clip_count` |
| G | `ATT_DESYNC_ROLL/PITCH` | Desired vs actual attitude divergence | `thresholds.att_desync.max_divergence_deg` (default 15°) |
| H | `VCC_FLUCTUATION` | VCC rail noise (rolling 5-row max-min) | `power_system.vcc.max_fluctuation_v` |
| I | `MIN_THRUST_*` | Any motor PWM ≤ minimum (stuck/dead motor) | `thresholds.pwm.min` |

## Component Schema

Each component JSON owns a subset of the final profile. `template_builder.deep_merge()` recursively combines them — overlapping keys are resolved by merge order (propulsion base → FC overlay → power overlay).

| Component category | Keys it owns |
|---|---|
| `flight_controllers/` | `flight_controller.*`, `power_system.vcc`, `thresholds.vibration`, `thresholds.imu_clipping` |
| `power_systems/` | `power_system.battery`, `thresholds.voltage_sag` |
| `propulsion/` | `airframe.*` (including TWR, hover_pwm_typical, motor_kv, all_up_weight_g, propeller_size_inches), `thresholds.pwm`, `thresholds.motor_imbalance`, `thresholds.att_desync`, `context_window`, `profile` (base) |

**To add a new component:** drop a JSON file into the matching subdirectory with the relevant subset of keys. The template_builder automatically discovers it (no registration needed). For example, adding a `power_systems/4s_lion.json` makes it immediately available via `--power 4s_lion`.

## Configuration

- **API Key:** Set `DEEPSEEK_API_KEY` environment variable before running `main.py` or the Streamlit app
- **Parallelism:** Set `DEEPSEEK_MAX_CONCURRENCY` to control max concurrent LLM API calls (default 5)
- **Target log:** Use `--csv <file>` flag (default: `flight_log_01.csv`)
- **Batch mode:** Use `--dir <directory>` to process all logs in a directory (per-file auto-detection)
- **Auto-detection:** Omit `--fc`/`--power`/`--propulsion` and the CLI auto-detects from binary logs; explicit flags always override
- **Defaults:** `template_builder.py` lines 23–25 (`DEFAULT_FC`, `DEFAULT_POWER`, `DEFAULT_PROPULSION`); change these to set different factory defaults
- **Streamlit upload limit:** 500 MB (configured in `.streamlit/config.toml`)

## Agentic Self-Review

After completing any non-trivial refactor, addition, or deletion across the
pipeline (parser, template_builder, main, app, or component JSONs), you MUST
autonomously run the codebase audit before declaring the task complete:

```bash
python audit_codebase.py
```

**What it checks:** `ruff` lints for style, bug-prone patterns, and complexity;
`vulture` scans for dead (unreachable / unused) code;
`bandit` scans for security vulnerabilities;
`pip-audit` scans dependencies for known CVEs.

**Follow-through rule:** If `audit_codebase.py` reports any issue that your
change introduced, fix it in the same turn. Only skip pre-existing, unrelated
findings — and when you do, call them out explicitly in your response so the
user knows what you chose not to touch.
