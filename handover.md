# Handover — Hardware & Telemetry Copilot

**Date:** 2026-06-04
**Status:** Architecture complete, ready for live-data integration testing

---

## 1. Overall Project Summary

**Hardware & Telemetry Copilot** is a B2B SaaS post-mortem diagnostic engine for ArduPilot drones. It ingests a telemetry CSV log, runs it through a local Python parser that filters out all steady-state flight data leaving only mechanical anomaly windows, then sends those windows to the DeepSeek API (`deepseek-reasoner`, temperature 0.0) for structured root-cause analysis. The LLM returns a strict JSON diagnostic report containing cited evidence, root cause, actionable fix, and confidence score.

The pipeline is designed to be **drone-agnostic** — swapping flight controller, battery, and propulsion is a CLI flag change, not a code change.

**Key technical decisions:**
- Zero-assumption LLM prompting with anti-hallucination constraints enforced in the system prompt
- Threshold-based anomaly extraction (no ML model, fully deterministic pandas filters)
- Modular hardware profiles assembled at runtime via deep-merge, not static per-drone files
- Temperature 0.0 for reproducible, compiler-like LLM output

---

## 2. Completed Architecture (What Was Made)

### Directory Layout

```
D:\Just_hobby\
  main.py                          # Orchestrator — ties everything together
  parser.py                        # Pandas anomaly scanner
  template_builder.py              # Deep-merge engine for hardware profiles
  CLAUDE.md                        # Claude Code guidance for this repo
  handover.md                      # This file
  docs/
    ARCHITECTURE.md                # Pipeline overview + LLM scope constraints
    DATA_SCHEMA.md                 # ArduPilot variable dictionary (BAT, RCOU, VIBE, ATT)
    DIAGNOSTIC_RULES.md            # Anti-hallucination axioms (thrust, sag, vibe, imbalance, inconclusive)
  components/
    flight_controllers/
      pixhawk_6c.json              # Pixhawk 6C Mini: VCC 4.9–5.3V, vibe Z 45, dual IMU
      cube_orange.json             # Cube Orange+: VCC 4.8–5.4V, vibe Z 60, triple IMU, dual baro
    power_systems/
      6s_lipo.json                 # 6S: 22.2V nom, 25.2V max, 19.8V min, sag 0.95/1.5
      12s_lipo.json                # 12S: 44.4V nom, 50.4V max, 39.6V min, sag 0.93/1.6
    propulsion/
      pwm_standard.json            # Hexa-X 6-motor: PWM redline 1940, imbalance 1700/1200
      dshot600.json                # X8 coaxial 8-motor: PWM redline 2000, imbalance 1800/1200
```

### `template_builder.py` — The Component Registry Engine

- **`build_profile(fc, power, propulsion)`** — loads three component JSONs and deep-merges them. Merge order: propulsion (base) → flight_controller → power_system. Returns a unified dict matching the original static profile schema exactly, so downstream code (`parser.py`, `main.py`) didn't need schema changes.
- **`deep_merge(base, override)`** — recursive dict merge; nested dicts combine, scalars/lists replace.
- **`list_all_components()`** — auto-discovers `.json` files in the three component subdirectories (zero registration).
- **CLI:** `python template_builder.py --fc cube_orange --power 12s_lipo --propulsion dshot600` prints the assembled JSON. `--list` enumerates available components with defaults marked.
- **Defaults** at lines 21–23: `pixhawk_6c` / `6s_lipo` / `pwm_standard` (mirrors the old static profile).

### `parser.py` — Anomaly Extraction Engine

Function: `extract_anomalies(csv_path, profile=None, fc=None, power=None, propulsion=None)`

- Accepts either a pre-built `profile` dict OR component names (builds the profile internally via `template_builder`).
- All thresholds are read from the profile dict — **nothing is hardcoded**.
- Returns a list of anomaly payload dicts `[{type, timestamp, data}, ...]` where `data` is a CSV string of the surrounding context window.
- Standalone runner accepts `--fc`, `--power`, `--propulsion`, `--help`.

### `main.py` — Orchestrator

- Parses `--fc`, `--power`, `--propulsion`, `--list`, `--help` from CLI.
- Calls `template_builder.build_profile()` → passes the assembled dict to both:
  1. `format_profile_for_llm(profile)` — renders the profile into a structured text block (flight controller, airframe, power system, diagnostic thresholds, critical rules) injected into the LLM system prompt.
  2. `extract_anomalies(TARGET_LOG, profile=profile)` — uses the profile's thresholds for scanning.
- `build_system_prompt(hw_text)` — compiles `docs/*.md` + the hardware profile text block + anti-hallucination constraints into the master system prompt.
- `call_diagnostic_api()` — POSTs to DeepSeek with temperature 0.0, 120s timeout.
- `validate_diagnostic_report()` — parses LLM output, handles markdown code fences, ensures all 6 required fields exist.
- Saves each anomaly's report as `report_anomaly_{N}_{type}.json`.

### `docs/` — LLM Context Documents

Three markdown files bundled into every system prompt:
- **ARCHITECTURE.md** — tells the LLM its role (post-mortem diagnostic engine, not a flight reviewer), scope boundaries, and context-awareness constraints.
- **DATA_SCHEMA.md** — ArduPilot variable dictionary mapping `BAT.Volt`, `RCOU.C1-C8`, `VIBE.VibeX/Y/Z`, `ATT.DesRoll/Roll` to physical meanings.
- **DIAGNOSTIC_RULES.md** — five anti-hallucination axioms: max thrust evaluation rule, voltage sag vs. depletion distinction, vibration isolation correlation, motor imbalance detection pattern, and the inconclusive mandate (must output "DIAGNOSTIC INCONCLUSIVE" when data is insufficient).

---

## 3. The Architectural Pivot (The Problem Solved)

### Before (earlier this session)

Hardware limits were **hardcoded** in `parser.py` as module-level constants (`VCC_MIN = 4.9`, `PWM_MAX = 1950`). The first refactor moved them into a single static JSON file per drone under `profiles/` (e.g., `pixhawk_6c_mini_6s.json`, `cube_orange_12s.json`). This worked for two drones but didn't scale — every new combination of FC + battery + airframe required a new hand-authored file.

### After (this session's final architecture)

Hardware configuration is **decomposed into three independent component categories**, each owning a specific slice of the profile schema:

| Component | Owns |
|---|---|
| `flight_controllers/` | FCU metadata, VCC rail limits, vibration thresholds, IMU configuration, barometer, power module |
| `power_systems/` | Battery chemistry, cell count, voltage ranges, sag-detection ratios |
| `propulsion/` | Airframe type, motor count/layout/pairs, PWM redline, imbalance thresholds, context window |

These are **deep-merged at runtime** by `template_builder.py`. With 2 FC × 2 power × 2 propulsion = **8 combinations** from 6 JSON files (vs. 8 static files in the old model). Adding a 3rd battery type (e.g., 4S Li-Ion) instantly unlocks 6 new combinations with one 20-line JSON file.

The merged profile dict maintains the exact same schema as the old static profiles, so `parser.py` and `main.py` needed zero schema-level changes — they just receive the dict from `template_builder` instead of reading a file.

---

## 4. Current Session Improvements

### 7 Anomaly Detection Categories (parser.py)

| # | Label prefix | What it detects | Profile-driven threshold |
|---|---|---|---|
| A | `VCC_DROP` | FC board voltage below VCC min | `power_system.vcc.min_v` |
| A | `VCC_OVER_VOLT` | FC board voltage above VCC max | `power_system.vcc.max_v` |
| B | `VOLTAGE_SAG` | BAT.Volt drop correlated with BAT.Curr spike (median-ratio based) | `thresholds.voltage_sag.*` |
| C | `MAX_THRUST_*` | Any motor PWM exceeding the redline | `thresholds.pwm.redline` |
| D | `IMBALANCE_*_vs_*` | Opposing motor pair divergence (one > high, opposite < low) | `thresholds.motor_imbalance.*` + `airframe.motor_pairs` |
| E | `VIBE_X_SPIKE`, `VIBE_Y_SPIKE`, `VIBE_Z_SPIKE` | Vibration exceeding axis limits | `thresholds.vibration.*` |
| F | `IMU_CLIP_*` | Any IMU clipping count > 0 | `thresholds.imu_clipping.max_clip_count` |

### Additional improvements

- **Profile auto-stamping:** `template_builder` generates a composite `profile_id` (e.g., `cube_orange__12s_lipo__dshot600`) and human-readable name from the component combination.
- **Component source tracking:** The assembled profile records which components produced it (`profile.components` dict), and `format_profile_for_llm()` includes this in the LLM context.
- **Auto-pairing fallback:** If a propulsion profile omits `motor_pairs`, `parser.py` auto-computes them from `motor_count` (C1↔C(N/2+1), etc.).
- **Column-safe scanning:** Every anomaly check guards against missing CSV columns — if `VIBE.VibeZ` doesn't exist in the log, that check is silently skipped.
- **API timeout:** 120s timeout on DeepSeek calls prevents hangs.
- **Markdown fence handling:** `validate_diagnostic_report()` strips ```json fences if the LLM wraps its output.
- **`--list` flag** on both `main.py` and `template_builder.py` for runtime component discovery.
- **CLAUDE.md** kept in sync across all three architectural pivots.

---

## 5. Immediate Next Steps

### Critical path (first thing to do)

- **Create or obtain a mock telemetry CSV** (`flight_log_01.csv`) that contains ArduPilot columns: `TimeS`, `BAT.Volt`, `BAT.Curr`, `VCC`, `VIBE.VibeX`, `VIBE.VibeY`, `VIBE.VibeZ`, `VIBE.Clip0`, `RCOU.C1`–`RCOU.C6`, `ATT.DesRoll`, `ATT.Roll`, `ATT.DesPitch`, `ATT.Pitch`. At minimum, seed it with:
  - A few rows where `VCC` drops to 4.6V (should trigger `VCC_DROP`)
  - A few rows where `RCOU.C4` hits 1970 and `RCOU.C1` sits at 1150 (should trigger both `MAX_THRUST_RCOU.C4` and `IMBALANCE_RCOU.C1_vs_RCOU.C4`)
  - A few rows where `VIBE.VibeZ` exceeds 50 (should trigger `VIBE_Z_SPIKE`)
  - 40+ rows of normal flight data surrounding each anomaly window to verify context-window extraction

### Verification steps

1. **Run the parser in isolation** (no API key required):
   ```
   python parser.py
   python parser.py --fc cube_orange --power 12s_lipo --propulsion dshot600
   ```
   Verify it finds the expected anomalies and the context windows are correctly sized.

2. **Inspect a built profile:**
   ```
   python template_builder.py --fc cube_orange --power 12s_lipo --propulsion dshot600
   ```
   Confirm VCC = 4.8–5.4, PWM redline = 2000, motor_count = 8, battery = 12S.

3. **End-to-end with API** (requires `DEEPSEEK_API_KEY` env var):
   ```
   python main.py
   ```
   Verify `report_anomaly_*.json` files are generated with all 6 required fields populated.

### Nice-to-have expansions

- **Add more power components:** 4S LiPo, 6S Li-Ion, 14S LiPo for agricultural drones.
- **Add more FC components:** Pixhawk 6X, Cube Blue, Durandal.
- **Add more propulsion components:** Quad-X, Y6 coaxial, X8 flat octo.
- **Add ATT desync detection** (parser section G): compare `ATT.DesRoll` vs `ATT.Roll` divergence as a 7th anomaly category (the schema already defines ATT fields, but the parser doesn't check them yet).
- **Add `--csv` flag to main.py** instead of hardcoding `TARGET_LOG = "flight_log_01.csv"`.
- **Streaming or batch mode:** Process a directory of CSVs instead of a single file.
- **Web dashboard:** Expose the pipeline behind a FastAPI endpoint so drone operators can upload logs and receive diagnostic reports via browser.

---

## Project State: Ready for Shutdown

All Python files compile cleanly. The component registry has been smoke-tested with both default and cross-combination merges. The only external dependency needed for the full pipeline is a valid `DEEPSEEK_API_KEY` environment variable. The parser can run fully offline with any CSV that matches the ArduPilot column schema.
