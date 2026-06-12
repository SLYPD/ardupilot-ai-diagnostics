# Hardware & Telemetry Copilot
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

Post-mortem diagnostic engine for ArduPilot / MAVLink telemetry logs. The
pipeline scans flight logs for hardware anomalies — voltage sags, vibration
spikes, max-thrust events, motor imbalance, and IMU clipping — then feeds
localized anomaly windows to a large language model (DeepSeek) for structured
root-cause analysis.

## Features

- **Native binary ingestion** — reads ArduPilot `.bin` (Dataflash) and `.tlog`
  (Telemetry) files via pymavlink, in addition to CSV
- **Modular hardware profiles** — mix-and-match **5 flight controllers**, **12
  battery options (1S–12S)**, and **6 propulsion configurations** from a JSON
  component registry
- **9 anomaly detectors** — VCC drop/overvolt, voltage sag, max thrust, motor
  imbalance, vibration spikes, IMU clipping, ATT desync, VCC fluctuation, and
  stuck-motor detection
- **Multi-sensor correlation** — cross-references voltage sag against current
  draw, vibration spikes against motor outputs, and opposing motor-pair imbalance
  with persistence filtering
- **LLM-powered diagnostics** — sends context windows to the DeepSeek API for
  structured engineering reports with cited evidence and confidence scores
- **Streamlit UI** — drag-and-drop file upload, sidebar hardware config, and
  interactive report cards with security hardening
- **FastAPI dashboard** — REST API for programmatic log upload and async
  diagnostic processing

## Scope

**This system is a threshold-based post-mortem scanner for ArduPilot Copter
multirotors.**  It does NOT support fixed-wing, helicopters, Betaflight, PX4,
or any firmware other than ArduPilot.

Before using this tool, please read **[PROJECT_SCOPE.md](PROJECT_SCOPE.md)** —
it defines exactly which aircraft types, battery chemistries, ESC protocols,
motor configurations, and flight styles are in scope (and which are not).

## Architecture

```
--fc pixhawk_6c --power 6s_lipo --propulsion pwm_standard
         |                    |                    |
         v                    v                    v
  components/           components/          components/
  flight_controllers/   power_systems/       propulsion/
         |                    |                    |
         +--------------------+--------------------+
                              |
                              v
                    template_builder.py  (deep-merge)
                              |
                    +---------+---------+
                    v                   v
              parser.py            main.py
          (thresholds)       (LLM system prompt)
```

**Three layers, assembled at runtime:**

1. **Component Registry** (`components/`) — JSON files in three categories:
   - `flight_controllers/` — FCU metadata, VCC rail limits, vibration thresholds, IMU config
   - `power_systems/` — battery chemistry, cell count, voltage ranges, sag-detection parameters
   - `propulsion/` — airframe type, motor layout, motor pairs, PWM redline, imbalance thresholds

2. **Template Builder** (`template_builder.py`) — deep-merges component JSONs
   into a unified Active Hardware Profile

3. **Pipeline** — `parser.py` scans telemetry against thresholds; `main.py`
   formats the context and calls the LLM API

## Installation

```bash
git clone <repo-url>
cd <repo>
pip install -r requirements.txt
```

For exact reproducible builds, use the lockfile:
```bash
pip install -r requirements.lock
```

## Usage

### Streamlit UI

```bash
streamlit run app.py
```

Open http://localhost:8501, upload a telemetry log, select your hardware
components in the sidebar, enter your DeepSeek API key, and click
**Run Diagnostics**.

### CLI

```bash
# Run with defaults (pixhawk_6c + 6s_lipo + pwm_standard)
python main.py

# Run with specific components
python main.py --fc cube_orange --power 12s_lipo --propulsion dshot600

# Run with a specific log file
python main.py --csv path/to/log.bin
python parser.py --csv path/to/log.tlog

# Batch process a directory of logs
python main.py --dir logs/
python parser.py --dir logs/

# List available components (26 total)
python template_builder.py --list

# Run parser standalone (offline, no API calls)
python parser.py --fc cube_orange --power 6s_lipo --propulsion pwm_standard
```

### FastAPI Dashboard

```bash
uvicorn api:app --reload --port 8000
```

Open http://localhost:8000/docs for interactive Swagger documentation.

Endpoints:
- `GET /health` — liveness check
- `GET /components` — list all available hardware components
- `POST /diagnose` — upload a telemetry log + hardware config + API key for async processing
- `GET /diagnose/{job_id}` — poll for diagnostic results
- `DELETE /diagnose/{job_id}` — remove a completed/failed job

## API Key

You must provide your own DeepSeek API key. Two options:

1. **Environment variable:** `export DEEPSEEK_API_KEY="sk-..."`
2. **Streamlit sidebar:** paste your key into the password field (it is never
   stored on disk)

The CLI (`main.py`) reads from the environment variable. The UI prompts you
in the sidebar.

## Supported Formats

| Extension | Format | Parser |
|---|---|---|
| `.bin` | ArduPilot Dataflash | `parser.parse_binary_log()` via pymavlink |
| `.tlog` | MAVLink Telemetry | `parser.parse_binary_log()` via pymavlink |
| `.csv` | Tabular CSV | `pd.read_csv()` |

## Adding Hardware Components

Drop a JSON file into the matching `components/` subdirectory with the
relevant subset of keys. The template builder automatically discovers it —
no registration needed.

Example: adding `components/power_systems/4s_lipo.json` makes it immediately
available via `--power 4s_lipo`.

## Code Quality

```bash
# Run all tests
python -m pytest tests/ -v

# Run full audit (ruff + vulture + bandit + pip-audit)
python audit_codebase.py
```

**CI:** GitHub Actions runs tests + audit on every push and PR. A weekly
dependency audit checks for new CVEs every Monday.

## Contributing

Contributions are welcome and encouraged — bug fixes, new hardware profiles, 
parser improvements, UI enhancements.

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/your-feature`)
3. Commit your changes
4. Open a pull request with a clear description of what was improved and why

Please read [CONTRIBUTING.md](CONTRIBUTING.md) before submitting.

Note: By contributing, you agree your contributions are licensed under AGPL-3.0.

## License

This project is licensed under the **GNU Affero General Public License v3.0 
(AGPL-3.0)**. See the [LICENSE](LICENSE) file for full terms.

**For commercial use** (closed-source products, SaaS deployment, or any use 
that cannot comply with AGPL terms), a separate commercial license is required. 
Contact: dgamingwaytine@gmail.com

