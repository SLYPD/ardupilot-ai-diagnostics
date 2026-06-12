import concurrent.futures
import glob
import json
import os
import re
import sys
import time

import requests

from parser import extract_anomalies
from template_builder import (
    CATEGORY_DIRS,
    DEFAULT_FC,
    DEFAULT_POWER,
    DEFAULT_PROPULSION,
    auto_detect_profile,
    build_profile,
    list_all_components,
)

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))

# --- Configuration ---
API_KEY  = os.environ.get("DEEPSEEK_API_KEY", "")
BASE_URL = "https://api.deepseek.com/chat/completions"
MODEL    = "deepseek-reasoner"
_MAX_CONCURRENCY = int(os.environ.get("DEEPSEEK_MAX_CONCURRENCY", "5"))


def format_profile_for_llm(profile: dict) -> str:
    """Render a hardware profile dict into a readable text block for the
    LLM system prompt."""
    p   = profile
    air = p["airframe"]
    fc  = p["flight_controller"]
    bat = p["power_system"]["battery"]
    vcc = p["power_system"]["vcc"]
    thr = p["thresholds"]

    imu_lines = "\n".join(
        f"    • {i['id']}: {i['chip']}"
        + (" (Isolated)" if i.get("isolated") else "")
        for i in fc["imu"]
    )

    pair_lines = "\n".join(
        f"    • {a} ↔ {b}" for a, b in air["motor_pairs"]
    )

    # Build the component-source footer
    comps = p.get("profile", {}).get("components", {})
    comp_footer = ""
    if comps:
        comp_footer = (
            f"\n[Assembled from components: "
            f"fc={comps.get('flight_controller', '?')}, "
            f"power={comps.get('power_system', '?')}, "
            f"propulsion={comps.get('propulsion', '?')}]"
        )

    text = f"""
[HARDWARE PROFILE: {p['profile']['name']}]
Profile ID : {p['profile']['profile_id']}
Description: {p['profile']['description']}{comp_footer}

━━━ FLIGHT CONTROLLER ━━━
Model        : {fc['model']}
MCU          : {fc['mcu']}
Firmware     : {fc['firmware']}
Barometer    : {fc['barometer']}
Power Module : {fc['power_module']}
IMU Configuration:
{imu_lines}

━━━ AIRFRAME & PROPULSION ━━━
Type         : {air['type']} ({air['motor_count']} motors)
Motor Layout : {air['motor_layout']}
Motors       : {air['motors']}
Motor KV     : {air.get('motor_kv', '?')} RPM/V
Propellers   : {air['propellers']} ({air.get('propeller_size_inches', '?')}")
ESC          : {air['esc']['protocol']} @ {air['esc']['amp_rating_continuous']}A continuous
All-Up Weight: {air.get('all_up_weight_g', '?')} g
Thrust/Weight: {air.get('thrust_to_weight_ratio', '?')}:1
Hover PWM    : ~{air.get('hover_pwm_typical', '?')} (typical for this airframe at hover)
Opposing Pairs (imbalance detection):
{pair_lines}

━━━ POWER SYSTEM ━━━
Battery Chemistry     : {bat['chemistry']}
Cell Count (S)        : {bat['cell_count']}S
Nominal Voltage       : {bat['nominal_voltage_v']} V
Max Fully Charged     : {bat['max_voltage_v']} V
Absolute Min Safe      : {bat['min_safe_voltage_v']} V ({bat['min_safe_cell_voltage_v']} V per cell)

VCC (FC Board Rail)   : {vcc['min_v']} V – {vcc['max_v']} V
Max VCC Fluctuation   : {vcc['max_fluctuation_v']} V
{vcc.get('description', '')}

━━━ DIAGNOSTIC THRESHOLDS ━━━
PWM Range              : {thr['pwm']['min']} – {thr['pwm']['absolute_max']}  (redline: >{thr['pwm']['redline']})
Vibration XY Max       : {thr['vibration']['xy_max_m_s2']} m/s²
Vibration Z Max        : {thr['vibration']['z_max_m_s2']} m/s²
Motor Imbalance        : High >{thr['motor_imbalance']['high_threshold_pwm']} | Low <{thr['motor_imbalance']['low_threshold_pwm']}
  {thr['motor_imbalance']['description']}
Voltage Sag Detection  : Volt < median × {thr['voltage_sag']['volt_drop_ratio']}  &  Curr > median × {thr['voltage_sag']['current_spike_ratio']}
  {thr['voltage_sag']['description']}
IMU Clipping           : Max {thr['imu_clipping']['max_clip_count']}
  {thr['imu_clipping']['description']}
ATT Desync Threshold   : {thr.get('att_desync', {}).get('max_divergence_deg', 15.0)}°
Min Thrust Detection   : PWM ≤ {thr['pwm']['min']} (dead/stuck motor detection)
VCC Fluctuation Limit  : {vcc.get('max_fluctuation_v', 0.15)} V (5-row rolling window)

━━━ CRITICAL RULES ━━━
• ALL thrust calculations MUST reference the motor's MAXIMUM physical output
  capability (absolute max PWM {thr['pwm']['absolute_max']}), never the take-off/hover throttle.
• THRUST-TO-WEIGHT CONTEXT: This aircraft has a TWR of {air.get('thrust_to_weight_ratio', '?')}:1
  and hovers at ~{air.get('hover_pwm_typical', '?')} PWM.
  — A max-thrust event at PWM {thr['pwm']['redline']} on a high-TWR aircraft (e.g. 8:1 racer, hover ~1350)
    is a genuine propulsion fault — the motors should never need to approach redline.
  — The SAME event on a low-TWR aircraft (e.g. 3:1 heavy lifter, hover ~1650)
    may be normal during aggressive climb or heavy payload — assess the context
    (surrounding PWM values, attitude change, current draw) before flagging as critical.
  — Voltage sag during high-throttle on a high-TWR aircraft suggests the battery
    is being pushed to its limit; on a low-TWR aircraft operating near redline,
    sag may indicate a failing pack or undersized battery for the payload.
• Voltage sag below {bat['min_safe_voltage_v']}V ({bat['min_safe_cell_voltage_v']}V/cell)
  is a CRITICAL power-delivery failure regardless of recovery or TWR.
• Correlate vibration spikes with RCOU motor outputs — matching spikes indicate
  mechanical/propulsion fault, not environmental wind.
  — High vibration at low PWM (near hover) suggests a bent shaft, damaged prop,
    or bearing failure.  High vibration only at high PWM with high TWR may be
    aerodynamic (prop flutter at speed) rather than mechanical.
• ATT desync >{thr.get('att_desync', {}).get('max_divergence_deg', 15.0)}° between desired and actual attitude
  indicates flight controller authority loss — verify motor/ESC health.
  — On low-TWR aircraft, some desync during aggressive commanded manoeuvres is
    expected (the aircraft physically cannot achieve the commanded rate).
  — On high-TWR aircraft (racer/freestyle), desync is almost always a fault.
• Motor running ≤ PWM min ({thr['pwm']['min']}) indicates complete failure — flag as
  critical regardless of other sensor readings or TWR.
• VCC rail fluctuation >{vcc.get('max_fluctuation_v', 0.15)} V indicates power supply instability —
  check BEC, wiring, and connectors.
""".strip()

    return text


def build_system_prompt(hardware_profile_text: str):
    """Compiles docs/*.md files plus the hardware profile into the system prompt."""
    print("Compiler: Assembling Context Bundle ...")

    md_files = sorted(glob.glob(os.path.join(_THIS_DIR, "docs/*.md")))
    if not md_files:
        raise FileNotFoundError(
            "No .md files found in docs/. "
            "Place ARCHITECTURE.md, DATA_SCHEMA.md, and "
            "DIAGNOSTIC_RULES.md in the docs/ directory."
        )

    context_bundle = ""
    for filepath in md_files:
        with open(filepath, encoding='utf-8') as f:
            context_bundle += f"\n\n[{os.path.basename(filepath)}]\n"
            context_bundle += f.read()

    system_prompt = f"""You are an expert ArduPilot and MAVLink telemetry diagnostic engine.
Your objective is to analyze filtered telemetry anomalies and output a strictly factual,
structured engineering diagnostic report.

=== CONTEXT BUNDLE ===
{context_bundle}

{hardware_profile_text}
=== END CONTEXT BUNDLE ===

STRICT CONSTRAINTS (ANTI-HALLUCINATION PROTOCOL):
1. NO ASSUMPTIONS: You may only reference sensor data present in the user's payload.
   If a variable is not provided, you must not mention it.
2. EVIDENCE REQUIREMENT: For every root cause identified, you MUST cite the specific
   timestamp and sensor variable.
3. THRUST CALCULATIONS: Always base your analysis and safety thresholds on the motor's
   MAXIMUM physical output capacity, never the take-off hover point.
4. UNKNOWN STATES: If the data does not contain a clear explanation, output exactly:
   "DIAGNOSTIC INCONCLUSIVE: Insufficient data to determine root cause."

OUTPUT FORMAT:
Return strictly valid JSON matching this structure:
{{
  "timestamp_range": "<start>s — <end>s",
  "primary_anomaly": "<anomaly type label>",
  "cited_evidence": ["<timestamp> <variable> = <value>", "..."],
  "root_cause_analysis": "<concise engineering explanation>",
  "actionable_fix": "<specific repair or pre-flight check>",
  "confidence_score": "High | Low | Inconclusive"
}}"""

    return system_prompt


def call_diagnostic_api(system_prompt, anomaly_payload, api_key=None):
    """Sends a single anomaly window to the LLM and returns the raw JSON string.

    Args:
        system_prompt:   assembled system prompt with hardware context
        anomaly_payload: dict with 'type', 'timestamp', and 'data' keys
        api_key:         DeepSeek API key. Falls back to module-level API_KEY
                         (which reads DEEPSEEK_API_KEY env var).
    """
    key = api_key or API_KEY
    if not key:
        raise ValueError(
            "No DeepSeek API key provided. Set DEEPSEEK_API_KEY environment "
            "variable or pass api_key to call_diagnostic_api()."
        )

    # Sanitize CSV cell values before embedding in the LLM prompt to reduce
    # prompt-injection surface.
    sanitized_data = _sanitize_csv_for_llm(anomaly_payload['data'])

    user_prompt = (
        f"Analyze the following telemetry anomaly window. "
        f"Only use the sensor data provided below to determine the root cause.\n\n"
        f"Anomaly Type: {anomaly_payload['type']}\n"
        f"First Detection Row: {anomaly_payload['timestamp']}\n\n"
        f"--- Context Window CSV ---\n{sanitized_data}"
    )

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type":  "application/json",
    }

    body = {
        "model":       MODEL,
        "messages": [
            {"role": "system",    "content": system_prompt},
            {"role": "user",      "content": user_prompt},
        ],
        "temperature": 0.0,
    }

    response = requests.post(BASE_URL, headers=headers, json=body, timeout=120)
    response.raise_for_status()

    return response.json()['choices'][0]['message']['content']


def validate_diagnostic_report(raw_json: str, anomaly_type: str) -> dict:
    """Parse the LLM output and ensure it has the required fields."""
    try:
        report = json.loads(raw_json)
    except json.JSONDecodeError:
        if raw_json.startswith("```"):
            raw_json = raw_json.split("```")[1]
            if raw_json.startswith("json"):
                raw_json = raw_json[4:]
            raw_json = raw_json.strip()
        report = json.loads(raw_json)

    required = [
        "timestamp_range", "primary_anomaly", "cited_evidence",
        "root_cause_analysis", "actionable_fix", "confidence_score",
    ]
    for field in required:
        report.setdefault(field, "NOT PROVIDED BY LLM")

    report["anomaly_type"] = anomaly_type
    return report


def _diagnose_one(
    anomaly: dict,
    master_prompt: str,
    api_key: str | None = None,
    retries: int = 3,
) -> dict:
    """Diagnose a single anomaly with retry logic.  Always returns a report
    dict (never raises) — returns an error placeholder on failure."""
    label = anomaly["type"]
    for attempt in range(retries):
        try:
            raw = call_diagnostic_api(master_prompt, anomaly, api_key=api_key)
            return validate_diagnostic_report(raw, label)
        except Exception:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    # All retries exhausted
    return {
        "timestamp_range": "?",
        "primary_anomaly": label,
        "cited_evidence": [],
        "root_cause_analysis": "API ERROR after retries.",
        "actionable_fix": "Retry the diagnostic.",
        "confidence_score": "Inconclusive",
        "anomaly_type": label,
    }


def diagnose_anomalies_parallel(
    anomalies: list,
    master_prompt: str,
    api_key: str | None = None,
    max_concurrency: int | None = None,
) -> list[dict]:
    """Diagnose multiple anomaly windows in parallel.

    Each anomaly is an independent API call — they fan out concurrently
    via a thread pool.  Individual failures are caught per-anomaly and
    return error-placeholder reports so one failure never crashes the
    whole batch.

    Results are returned in the **same order** as the input list.
    """
    if not anomalies:
        return []

    workers = max_concurrency if max_concurrency is not None else _MAX_CONCURRENCY

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        # Map preserves order — each future maps 1:1 to an anomaly
        futures = [
            pool.submit(_diagnose_one, anomaly, master_prompt, api_key)
            for anomaly in anomalies
        ]
        for i, future in enumerate(futures):
            try:
                results.append(future.result())
            except Exception:
                # _diagnose_one should never raise, but guard anyway
                label = anomalies[i]["type"]
                results.append({
                    "timestamp_range": "?",
                    "primary_anomaly": label,
                    "cited_evidence": [],
                    "root_cause_analysis": "Internal error in parallel executor.",
                    "actionable_fix": "Retry the diagnostic.",
                    "confidence_score": "Inconclusive",
                    "anomaly_type": label,
                })

    return results


# ---------------------------------------------------------------------------
# Prompt-injection mitigation — sanitize CSV cell content before LLM
# injection by corrupting known injection-pattern keywords.
# ---------------------------------------------------------------------------
_INJECTION_PATTERNS = [
    (re.compile(r'\b(system)\b', re.I), '[s_ysterm]'),
    (re.compile(r'\b(assistant)\b', re.I), '[a_ssistant]'),
    (re.compile(r'\b(user)\b', re.I), '[u_ser]'),
    (re.compile(r'\b(ignore)\b', re.I), '[i_gnore]'),
    (re.compile(r'\b(override)\b', re.I), '[o_verride]'),
    (re.compile(r'\b(instruction)\b', re.I), '[i_nstruction]'),
]


def _sanitize_csv_for_llm(csv_text: str) -> str:
    """Corrupt known LLM prompt-injection keywords in CSV cell values."""
    sanitized = csv_text
    for pattern, replacement in _INJECTION_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized


# --- Orchestrator ---
if __name__ == "__main__":
    TARGET_LOG = None

    fc         = None
    power      = None
    propulsion = None

    # --- Parse CLI flags ---
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--fc" and i + 1 < len(args):
            fc = args[i + 1]
            i += 2
        elif args[i] == "--power" and i + 1 < len(args):
            power = args[i + 1]
            i += 2
        elif args[i] == "--propulsion" and i + 1 < len(args):
            propulsion = args[i + 1]
            i += 2
        elif args[i] == "--csv" and i + 1 < len(args):
            TARGET_LOG = args[i + 1]
            i += 2
        elif args[i] == "--dir" and i + 1 < len(args):
            dir_path = args[i + 1]
            if not os.path.isdir(dir_path):
                print(f"Error: '{dir_path}' is not a directory.", file=sys.stderr)
                sys.exit(1)
            files = sorted(
                glob.glob(os.path.join(dir_path, "*.csv")) +
                glob.glob(os.path.join(dir_path, "*.bin")) +
                glob.glob(os.path.join(dir_path, "*.tlog")) +
                glob.glob(os.path.join(dir_path, "*.rlog"))
            )
            if not files:
                print(f"No telemetry files found in '{dir_path}'.", file=sys.stderr)
                sys.exit(0)

            # --- Batch processing (per-file auto-detection) ---
            total_anomalies = 0
            for fpath in files:
                basename = os.path.splitext(os.path.basename(fpath))[0]
                print(f"{'='*60}\nProcessing: {os.path.basename(fpath)}\n{'='*60}")

                # Auto-detect profile per file when no explicit flags given
                _fc, _power, _propulsion = fc, power, propulsion
                if _fc is None and _power is None and _propulsion is None:
                    detected = auto_detect_profile(fpath)
                    if detected:
                        _fc = detected["fc"]
                        _power = detected["power"]
                        _propulsion = detected["propulsion"]

                try:
                    profile = build_profile(fc=_fc, power=_power, propulsion=_propulsion)
                    print(
                        f"Orchestrator: Assembled profile → "
                        f"'{profile['profile']['name']}'"
                    )
                    hw_text = format_profile_for_llm(profile)
                    master_prompt = build_system_prompt(hw_text)

                    anomalies = extract_anomalies(fpath, profile=profile)
                    if not anomalies:
                        print("  No anomalies detected.")
                        continue
                    total_anomalies += len(anomalies)

                    reports = diagnose_anomalies_parallel(anomalies, master_prompt)
                    for idx, report in enumerate(reports):
                        label = report.get("anomaly_type", anomalies[idx]["type"])
                        filename = f"report_{basename}_anomaly_{idx+1}_{label}.json"
                        with open(filename, 'w', encoding='utf-8') as f:
                            json.dump(report, f, indent=2, ensure_ascii=False)
                        print(f"  [{idx+1}/{len(reports)}] {label} -> {filename}")
                except Exception as exc:
                    print(f"  Error: {exc}", file=sys.stderr)

            print(f"\nOrchestrator: Batch complete. {total_anomalies} total anomalies diagnosed.")
            sys.exit(0)
        elif args[i] in ("--list", "-l"):
            print("Available components:")
            for cat, names in list_all_components().items():
                print(f"  {CATEGORY_DIRS[cat]}/")
                for n in names:
                    defs = {
                        "fc": DEFAULT_FC, "power": DEFAULT_POWER,
                        "propulsion": DEFAULT_PROPULSION,
                    }
                    marker = "  [default]" if n == defs.get(cat) else ""
                    print(f"    - {n}{marker}")
            sys.exit(0)
        elif args[i] in ("--help", "-h"):
            print(
                "Usage: python main.py [--fc NAME] [--power NAME] "
                "[--propulsion NAME] [--csv FILE] [--dir DIR]"
            )
            print(f"\nDefaults: --fc {DEFAULT_FC} --power {DEFAULT_POWER} "
                  f"--propulsion {DEFAULT_PROPULSION}")
            print("\nOther options:")
            print("  --csv FILE     Process a single telemetry log")
            print("  --dir DIR      Process all .csv/.bin/.tlog/.rlog files in a directory")
            print("  --list, -l     List available components")
            print("  --help, -h     Show this message")
            sys.exit(0)
        else:
            i += 1

    # --- Single-file mode (default: flight_log_01.csv for convenience) ---
    if TARGET_LOG is None:
        TARGET_LOG = "flight_log_01.csv"

    # --- Auto-detect hardware profile from log metadata ---
    if fc is None and power is None and propulsion is None:
        detected = auto_detect_profile(TARGET_LOG)
        if detected:
            fc = detected["fc"]
            power = detected["power"]
            propulsion = detected["propulsion"]

    try:
        # 1. Assemble the Active Hardware Profile from components
        profile = build_profile(fc=fc, power=power, propulsion=propulsion)
        print(
            f"Orchestrator: Assembled profile → "
            f"'{profile['profile']['name']}'"
        )
        comps = profile["profile"]["components"]
        print(
            f"  FC: {comps['flight_controller']}  |  "
            f"Power: {comps['power_system']}  |  "
            f"Propulsion: {comps['propulsion']}\n"
        )

        # 2. Compile the system prompt (docs/*.md + hardware profile text)
        hw_text       = format_profile_for_llm(profile)
        master_prompt = build_system_prompt(hw_text)
        print(
            f"Compiler: Loaded "
            f"{len(glob.glob(os.path.join(_THIS_DIR, 'docs/*.md')))} doc(s) "
            f"+ hardware profile into context.\n"
        )

        # 3. Extract anomalies using the assembled profile
        print(f"Parser: Scanning {TARGET_LOG} for hardware faults ...")
        anomalies = extract_anomalies(TARGET_LOG, profile=profile)

        if not anomalies:
            print("Parser: No anomalies detected. Hardware is nominal.")
            sys.exit(0)

        print(f"Orchestrator: Found {len(anomalies)} fault(s). "
              f"Initiating API diagnostics (parallel, max {_MAX_CONCURRENCY} concurrent) ...\n")

        # 4. Diagnose all anomalies in parallel
        reports = diagnose_anomalies_parallel(anomalies, master_prompt)

        for idx, report in enumerate(reports):
            label = report.get("anomaly_type", anomalies[idx]["type"])
            filename = f"report_anomaly_{idx + 1}_{label}.json"
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(report, f, indent=2, ensure_ascii=False)

            print(f"    [{idx+1}/{len(reports)}] {label} → {filename}  "
                  f"(confidence: {report.get('confidence_score', '?')})")

        print("\nOrchestrator: All anomalies diagnosed.")

    except FileNotFoundError as e:
        print(f"Fatal Error (missing file): {e}")
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        print(f"Fatal Error (API request failed): {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Fatal Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
