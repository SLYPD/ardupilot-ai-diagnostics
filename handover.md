# Handover — Hardware & Telemetry Copilot

**Date:** 2026-06-12
**Status:** Auto-detection integrated across all entry points. LLM diagnostics parallelized. 44/44 tests pass. Production-ready.

---

## What changed this session (2026-06-12)

### 1. Auto-detection integration — COMPLETE

`auto_detect_profile()` in `template_builder.py` reads frame type, battery voltage,
and motor count from binary logs and maps to the closest component profile. Now
wired into **all entry points:**

| Entry point | Behaviour |
|---|---|
| `main.py` (single-file) | Auto-detects when `--fc`/`--power`/`--propulsion` are omitted |
| `main.py` (`--dir` batch) | Per-file auto-detection — each log gets its own profile |
| `app.py` (Streamlit) | Auto-detect banner + dropdown pre-fill (was already done) |
| `api.py` (`POST /diagnose`) | Auto-detects and returns `detected_components` in response |
| `api.py` (`GET /diagnose/{id}`) | Surfaces `components` + `detected_components` |

Explicit CLI flags / form values always take precedence over auto-detection.

### 2. Parallel LLM diagnostics — 5× wall-clock speedup

Added `diagnose_anomalies_parallel()` utility in `main.py`. All three entry points
(CLI, Streamlit, FastAPI) now fan out anomaly API calls concurrently via
`concurrent.futures.ThreadPoolExecutor` instead of processing them one-at-a-time.

| Detail | Value |
|---|---|
| Concurrency mechanism | `ThreadPoolExecutor` (stdlib, I/O-bound) |
| Default max workers | 5 (configurable via `DEEPSEEK_MAX_CONCURRENCY` env var) |
| Retry logic | 3 attempts with exponential backoff (moved into shared utility) |
| Error isolation | Individual call failures return error placeholders — never crash the batch |
| Result ordering | Preserved (matches input anomaly order) |

The model is **unchanged** — still `deepseek-reasoner`. Each individual call gets
its full reasoning time. Only the waiting is parallelized.

### 3. Minor fixes

- **Unicode fix:** `→` replaced with `->` in `template_builder.py` auto-detect print
  (cp1252 encoding error on Windows terminals)
- **Dead imports removed:** `asyncio` from `api.py`, unused `call_diagnostic_api` /
  `validate_diagnostic_report` from `app.py` and `api.py`

---

## Quick verification

```bash
python -m pytest tests/ -v           # 44/44 pass (36 existing + 8 new test_main.py)
python audit_codebase.py              # pre-existing issues only, 0 new

# Auto-detect from real file
python main.py --csv "Flight Log Examples/2026-03-23 16-13-21.bin"
# → Auto-detect: 6-motor -> pwm_standard, ~4S -> 4s_lipo
# → Parallel diagnostics (max 5 concurrent)

# Explicit flags override auto-detect
python main.py --fc cube_orange --power 6s_lipo --propulsion dshot600

# Parallelism control
DEEPSEEK_MAX_CONCURRENCY=10 python main.py    # 10 concurrent API calls
```

---

## Architecture (unchanged)

Three-layer pipeline:
1. **Component Registry** (`components/`) — 23 JSON profiles, auto-discovered
2. **Template Builder** (`template_builder.py`) — deep-merge, validation, auto-detect
3. **Parser** (`parser.py`) + **Orchestrator** (`main.py`) — 9 anomaly detectors, parallel LLM integration

Plus: Streamlit UI (`app.py`), FastAPI (`api.py`).

---

## Key files

| File | Role |
|------|------|
| `main.py` | CLI orchestrator, parallel diagnostic utility, LLM prompt + API calls |
| `parser.py` | Binary ingestion (2 format handlers) + 9 anomaly detectors |
| `template_builder.py` | Profile assembly, auto-detection, component discovery |
| `app.py` | Streamlit UI (dark theme, auto-detect, parallel diagnostics) |
| `api.py` | FastAPI REST dashboard (auto-detect, parallel diagnostics) |
| `components/` | 23 JSON profiles (5 FC, 12 power, 6 propulsion) |
| `docs/` | ARCHITECTURE.md, DATA_SCHEMA.md, DIAGNOSTIC_RULES.md |
| `tests/` | 44 tests (14 template_builder + 22 parser + 8 main) |
| `handover.md` | This file |

---

## Configuration

| Variable | Purpose | Default |
|---|---|---|
| `DEEPSEEK_API_KEY` | API authentication | (required) |
| `DEEPSEEK_MAX_CONCURRENCY` | Max parallel LLM calls | 5 |

---

## Known limitations

- Context windows are row-based (not time-based).
- Forward-fill in binary log ingestion can introduce temporal skew.
- No WebSocket support in FastAPI.
- In-memory job store in FastAPI clears on restart.
- No `.tlog` test fixtures in the repository.
- Parser redline is static — no TWR-aware adjustment at the parser level (LLM handles this).
- No baseline-learning phase — hover PWM is from the profile, not measured from the flight.
- No flight-phase detection — all rows treated identically.
- ArduPilot Copter only — Plane, Rover, PX4, Betaflight are out of scope.
- Multirotor only (4–8 motors, symmetric opposing pairs).
- LiPo only — Li-Ion and LiHV are not yet profiled.

Full details: [`PROJECT_SCOPE.md`](PROJECT_SCOPE.md).

---

## In progress (nothing — all committed)

All items from the previous handover are complete:
- ✅ Auto-detection in `main.py`
- ✅ Auto-detection in `api.py`
- ✅ Parallel LLM diagnostics

## Immediate next steps

1. **Live-data testing**: Run more real .bin/.tlog files through the full pipeline
2. **Baseline-learning phase**: Analyse first N seconds of hover to measure actual vibration/PWM/current baselines
3. **More FC components**: Pixhawk 5X, Matek H743, SpeedyBee F405
4. **More power chemistries**: Li-Ion, LiHV profiles
5. **Time-based context windows**: Replace ±20 rows with ±2s
6. **Persistent job store for FastAPI**: Redis or SQLite
7. **`.tlog` test fixtures**: Add sample .tlog files for automated testing
