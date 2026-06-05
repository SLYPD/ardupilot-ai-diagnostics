# Handover 1 — Hardware & Telemetry Copilot

**Date:** 2026-06-05
**Status:** Streamlit UI operational; Windows file-lock fixed

---

## 1. Current State

We successfully built the Streamlit UI (`app.py`) and integrated `pymavlink`
binary parsing into `parser.py`. The pipeline now accepts three formats:

| Extension | Format | Parser |
|---|---|---|
| `.bin` | ArduPilot Dataflash | `parser.parse_binary_log()` via `pymavlink.mavutil` |
| `.tlog` | MAVLink Telemetry | `parser.parse_binary_log()` via `pymavlink.mavutil` |
| `.csv` | Tabular CSV | `pd.read_csv()` (unchanged) |

The binary ingestion layer extracts **BAT**, **POWR**, **VIBE**, **ATT**, and
**RCOU** MAVLink message frames, maps them to the internal column schema, and
merges independent message streams onto a unified timeline via forward-fill.

The Streamlit app uses a 4-phase "labor illusion" status block:
1. Assemble Hardware Profile
2. Scan raw telemetry
3. Cross-correlate sensor data
4. Synthesize DeepSeek report

---

## 2. The Blocker (RESOLVED)

Testing with a real ArduPilot `.bin` file on Windows threw a
`PermissionError [Errno 13]` because `tempfile.NamedTemporaryFile` locks the
file while it remains open in the `with` block, preventing `pymavlink` from
reading it.

**Root cause:** The original code kept the `with tempfile.NamedTemporaryFile(…):`
context manager open across the entire pipeline execution. On Windows, this
holds an exclusive lock on the temp file, so when `parser.py` (or pymavlink)
tries to open it for reading, the OS denies access.

**Fix applied in `app.py`:**

1. The `with` block now **only** wraps the `tmp.write(…)` call — it exits
   immediately after the bytes are flushed, releasing the Windows file lock.
2. All pipeline execution (profile build, anomaly extraction, API calls,
   report rendering) now runs **outside** the file handle context.
3. A `try`/`finally` block wraps the entire pipeline so `os.unlink(tmp_path)`
   always runs, even when an exception or `st.stop()` fires.

The temp file suffix is set via `Path(uploaded_file.name).suffix` to
guarantee `.bin`/`.tlog` extensions are preserved for correct routing in
`extract_anomalies()`.

---

## 3. Key Files

| File | Role |
|---|---|
| `app.py` | Streamlit UI — upload, config sidebar, labor illusion, report cards |
| `parser.py` | Anomaly scanner + `parse_binary_log()` binary ingestion layer |
| `template_builder.py` | Deep-merge engine for modular hardware profiles |
| `main.py` | CLI orchestrator + DeepSeek API formatting |
| `components/` | 6 JSON files across 3 categories (FC, power, propulsion) |
| `docs/` | LLM context: ARCHITECTURE.md, DATA_SCHEMA.md, DIAGNOSTIC_RULES.md |
| `CLAUDE.md` | Project guidance for Claude Code |
| `handover.md` | Original architecture handover (2026-06-04) |

---

## 4. Running the App

```powershell
$env:PYTHONIOENCODING = 'utf-8'
python -m streamlit run app.py --server.port 8501 --server.headless true
```

Open [http://localhost:8501](http://localhost:8501).

Dependencies: `pip install pandas requests pymavlink streamlit`
