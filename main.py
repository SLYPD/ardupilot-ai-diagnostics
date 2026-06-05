import os
import sys
import glob
import json
import requests
from template_builder import (
    build_profile,
    list_all_components,
    DEFAULT_FC,
    DEFAULT_POWER,
    DEFAULT_PROPULSION,
    CATEGORY_DIRS,
)
from parser import extract_anomalies


# --- Configuration ---
API_KEY  = os.environ.get("DEEPSEEK_API_KEY", "")
BASE_URL = "https://api.deepseek.com/chat/completions"
MODEL    = "deepseek-reasoner"


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
Propellers   : {air['propellers']}
ESC          : {air['esc']['protocol']} @ {air['esc']['amp_rating_continuous']}A continuous
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

━━━ CRITICAL RULES ━━━
• ALL thrust calculations MUST reference the motor's MAXIMUM physical output
  capability (absolute max PWM {thr['pwm']['absolute_max']}), never the take-off/hover throttle.
• Voltage sag below {bat['min_safe_voltage_v']}V ({bat['min_safe_cell_voltage_v']}V/cell)
  is a CRITICAL power-delivery failure regardless of recovery.
• Correlate vibration spikes with RCOU motor outputs — matching spikes indicate
  mechanical/propulsion fault, not environmental wind.
""".strip()

    return text


def build_system_prompt(hardware_profile_text: str):
    """Compiles docs/*.md files plus the hardware profile into the system prompt."""
    print("Compiler: Assembling Context Bundle ...")

    md_files = sorted(glob.glob("docs/*.md"))
    if not md_files:
        raise FileNotFoundError(
            "No .md files found in docs/. "
            "Place ARCHITECTURE.md, DATA_SCHEMA.md, and "
            "DIAGNOSTIC_RULES.md in the docs/ directory."
        )

    context_bundle = ""
    for filepath in md_files:
        with open(filepath, 'r', encoding='utf-8') as f:
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

    user_prompt = (
        f"Analyze the following telemetry anomaly window. "
        f"Only use the sensor data provided below to determine the root cause.\n\n"
        f"Anomaly Type: {anomaly_payload['type']}\n"
        f"First Detection Row: {anomaly_payload['timestamp']}\n\n"
        f"--- Context Window CSV ---\n{anomaly_payload['data']}"
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


# --- Orchestrator ---
if __name__ == "__main__":
    TARGET_LOG = "flight_log_01.csv"

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
        elif args[i] in ("--list", "-l"):
            print("Available components:")
            for cat, names in list_all_components().items():
                print(f"  {CATEGORY_DIRS[cat]}/")
                for n in names:
                    defs = {
                        "fc": DEFAULT_FC, "power": DEFAULT_POWER,
                        "propulsion": DEFAULT_PROPULSION,
                    }
                    marker = "  ← default" if n == defs.get(cat) else ""
                    print(f"    - {n}{marker}")
            sys.exit(0)
        elif args[i] in ("--help", "-h"):
            print(
                "Usage: python main.py [--fc NAME] [--power NAME] "
                "[--propulsion NAME]"
            )
            print(f"\nDefaults: --fc {DEFAULT_FC} --power {DEFAULT_POWER} "
                  f"--propulsion {DEFAULT_PROPULSION}")
            print("\nOther options:")
            print("  --list, -l    List available components")
            print("  --help, -h    Show this message")
            sys.exit(0)
        else:
            i += 1

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
        print(f"Compiler: Loaded {len(glob.glob('docs/*.md'))} doc(s) "
              f"+ hardware profile into context.\n")

        # 3. Extract anomalies using the assembled profile
        print(f"Parser: Scanning {TARGET_LOG} for hardware faults ...")
        anomalies = extract_anomalies(TARGET_LOG, profile=profile)

        if not anomalies:
            print("Parser: No anomalies detected. Hardware is nominal.")
            sys.exit(0)

        print(f"Orchestrator: Found {len(anomalies)} fault(s). "
              f"Initiating API diagnostics ...\n")

        # 4. Feed each anomaly to the LLM
        for idx, anomaly in enumerate(anomalies):
            label = anomaly['type']
            print(f"--> Diagnosing Anomaly {idx + 1}/{len(anomalies)}: "
                  f"{label}  (row {anomaly['timestamp']})")

            raw_output = call_diagnostic_api(master_prompt, anomaly)
            report     = validate_diagnostic_report(raw_output, label)

            filename = f"report_anomaly_{idx + 1}_{label}.json"
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(report, f, indent=2, ensure_ascii=False)

            print(f"    [OK] Saved → {filename}  "
                  f"(confidence: {report.get('confidence_score', '?')})\n")

        print("Orchestrator: All anomalies diagnosed.")

    except FileNotFoundError as e:
        print(f"Fatal Error (missing file): {e}")
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        print(f"Fatal Error (API request failed): {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Fatal Error: {e}")
        sys.exit(1)
