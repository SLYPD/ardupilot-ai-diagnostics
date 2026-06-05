# Contributing

Thanks for your interest in contributing to the ArduPilot/MAVLink telemetry
diagnostic engine. This document explains how to set up your environment, follow
the project's conventions, and submit changes.

## Development Environment Setup

```bash
# Clone the repository
git clone <repo-url>
cd <repo>

# Create and activate a virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
# or
.venv\Scripts\activate      # Windows

# Install dependencies
pip install -r requirements.txt
```

All dependencies are pinned with minimum versions in `requirements.txt`.
The project targets Python 3.10+.

## Code Style

- Match the style of the surrounding code. This project favours plain,
  readable Python with explicit variable names.
- Use the same comment density and docstring style you see in the existing
  modules (`parser.py`, `template_builder.py`, `main.py`).
- Linting is handled by **ruff** (configured in `audit_codebase.py`). No
  separate ruff config file is needed — the audit script applies the same
  checks every contributor runs.
- Dead-code detection is handled by **vulture**, also invoked through the audit
  script.

## Adding a New Hardware Component

The component registry lives under `components/` and is organised into three
categories:

| Directory | What belongs there |
|---|---|
| `components/flight_controllers/` | FCU metadata, VCC rail limits, vibration thresholds, IMU config |
| `components/power_systems/` | Battery chemistry, cell count, voltage ranges, sag parameters |
| `components/propulsion/` | Airframe type, motor layout, PWM redline, imbalance thresholds |

To add a new component:

1. Create a JSON file in the matching subdirectory. Name it with a short,
   descriptive slug using underscores (e.g. `4s_lion.json`).
2. Include only the keys that category owns (see the Component Schema table in
   `CLAUDE.md` for the full breakdown). Use an existing component file as a
   template.
3. No registration step is needed — `template_builder.py` auto-discovers every
   `.json` file in those directories. Your component is immediately available
   via `--fc` / `--power` / `--propulsion` using the filename stem (without
   the `.json` extension).

## Before Submitting

Run the codebase audit script. It checks for style violations, bug-prone
patterns, and dead code:

```bash
python audit_codebase.py
```

If the audit reports issues that your change introduced, fix them in the same
branch. Pre-existing, unrelated findings can be skipped — but please call them
out in your pull request description so reviewers know you saw them.

## License

All contributions are made under the **GNU Affero General Public License v3.0
(AGPL-3.0)**. By opening a pull request you agree to license your work under
those terms.
