"""
Modular Hardware Profile Builder — deep-merges component JSONs into a single
Active Hardware Profile at runtime.

Usage (CLI):
    python template_builder.py --fc pixhawk_6c --power 6s_lipo --propulsion pwm_standard

Usage (library):
    from template_builder import build_profile
    profile = build_profile(fc="cube_orange", power="12s_lipo", propulsion="dshot600")
"""

import json
import os
import re
import sys

COMPONENTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "components")

# Only allow names that are safe to pass directly to os.path.join — no path
# separators, no parent-directory escapes, no null bytes.
_SAFE_COMPONENT_NAME_RE = re.compile(r'^[a-zA-Z0-9_][a-zA-Z0-9._-]*$')


def _natural_sort_key(name: str) -> list:
    """Split name into alternating text/number parts for natural sort.

    '10s_lipo' → ['', 10, 's_lipo'] so that 10 sorts after 9 numerically
    rather than lexicographically ('10' < '9').
    """
    return [int(part) if part.isdigit() else part.lower()
            for part in re.split(r'(\d+)', name)]


def _validate_component_name(name: str) -> None:
    """Raise ValueError if *name* contains characters that could allow path
    traversal or filesystem escape."""
    if not _SAFE_COMPONENT_NAME_RE.match(name):
        raise ValueError(
            f"Invalid component name '{name}'. "
            "Use only letters, digits, underscores, hyphens, and dots."
        )

# Defaults — equivalent to the old pixhawk_6c_mini_6s static profile.
DEFAULT_FC         = "pixhawk_6c"
DEFAULT_POWER      = "6s_lipo"
DEFAULT_PROPULSION = "pwm_standard"

# Map each component category to its subdirectory under components/
CATEGORY_DIRS = {
    "fc":         "flight_controllers",
    "power":      "power_systems",
    "propulsion": "propulsion",
}


def _load_json(subdir: str, name: str) -> dict:
    """Load a single component JSON file.

    Args:
        subdir: subdirectory name under components/ (e.g. 'flight_controllers')
        name:   file basename without .json extension

    Returns the parsed dict, or raises FileNotFoundError with a helpful message.
    """
    _validate_component_name(name)
    filepath = os.path.join(COMPONENTS_DIR, subdir, f"{name}.json")
    if not os.path.exists(filepath):
        available = _list_available(subdir)
        raise FileNotFoundError(
            f"Component not found: {filepath}\n"
            f"Available in components/{subdir}/:\n"
            + "\n".join(f"  - {a}" for a in available)
            if available else "  (directory is empty or missing)"
        )
    with open(filepath, encoding="utf-8") as f:
        return json.load(f)


def _list_available(subdir: str) -> list:
    """List available component names (without .json) in a subdirectory."""
    dirpath = os.path.join(COMPONENTS_DIR, subdir)
    if not os.path.isdir(dirpath):
        return []
    return sorted(
        (os.path.splitext(f)[0] for f in os.listdir(dirpath) if f.endswith(".json")),
        key=_natural_sort_key,
    )


def list_all_components() -> dict:
    """Return {category: [name, ...]} for all available components."""
    return {
        label: _list_available(subdir)
        for label, subdir in CATEGORY_DIRS.items()
    }


def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base*.

    - Nested dicts are merged recursively.
    - Lists and scalars from *override* replace those in *base*.
    - *base* is not mutated; a new dict is returned.
    """
    result = {}
    all_keys = set(base) | set(override)

    for key in all_keys:
        if key in override and key in base:
            if isinstance(base[key], dict) and isinstance(override[key], dict):
                result[key] = deep_merge(base[key], override[key])
            else:
                result[key] = override[key]
        elif key in override:
            result[key] = override[key]
        else:
            result[key] = base[key]

    return result


def build_profile(fc=None, power=None, propulsion=None) -> dict:
    """Load and deep-merge component JSONs into an Active Hardware Profile.

    Args:
        fc:         flight_controllers basename (e.g. 'pixhawk_6c')
        power:      power_systems basename (e.g. '6s_lipo')
        propulsion: propulsion basename (e.g. 'pwm_standard')

    Returns a single dict representing the complete hardware profile.

    Merge order: propulsion (base) → flight_controller → power_system.
    Later components' keys override earlier ones where they overlap.
    """
    fc         = fc or DEFAULT_FC
    power      = power or DEFAULT_POWER
    propulsion = propulsion or DEFAULT_PROPULSION

    # Base layer — propulsion (airframe, PWM thresholds, context window)
    profile = _load_json("propulsion", propulsion)

    # Overlay flight controller (FCU metadata, VCC, vibration thresholds)
    fc_dict = _load_json("flight_controllers", fc)
    profile = deep_merge(profile, fc_dict)

    # Overlay power system (battery, voltage-sag thresholds)
    pwr_dict = _load_json("power_systems", power)
    profile = deep_merge(profile, pwr_dict)

    # Stamp the assembled profile metadata
    profile.setdefault("profile", {})
    profile["profile"]["profile_id"] = f"{fc}__{power}__{propulsion}"
    profile["profile"]["name"] = (
        f"{fc_dict['flight_controller']['model']} — "
        f"{pwr_dict['power_system']['battery']['cell_count']}S "
        f"{profile['airframe']['type']}"
    )
    profile["profile"]["description"] = (
        f"Runtime assembly: FC={fc}  |  Power={power}  |  "
        f"Propulsion={propulsion}"
    )
    profile["profile"]["components"] = {
        "flight_controller": fc,
        "power_system":      power,
        "propulsion":        propulsion,
    }

    return profile


# --- Auto-Detection ---

# MAV_TYPE → motor-count heuristic (used for .tlog HEARTBEAT)
_MAVTYPE_MOTOR_COUNT = {
    2: 4,    # MAV_TYPE_QUADROTOR
    13: 6,   # MAV_TYPE_HEXAROTOR
    14: 8,   # MAV_TYPE_OCTOROTOR
    15: 3,   # MAV_TYPE_TRICOPTER (unsupported but detectable)
}

# FRAME_CLASS (ArduPilot Copter) → airframe family
_FRAMECLASS_AIRFRAME = {
    1: "Plane",
    2: "Copter",
    3: "Rover",
    4: "Sub",
}


def auto_detect_profile(file_path: str) -> dict | None:
    """Read metadata from an ArduPilot log and suggest matching components.

    Inspects PARM / HEARTBEAT / SYS_STATUS messages to determine:
        - motor count → closest propulsion profile
        - battery voltage → closest cell-count power profile
        - firmware type → validates ArduPilot Copter

    Returns a dict ``{fc, power, propulsion}`` with suggested component
    names, or None when detection fails (unsupported format, CSV, etc.).

    Does NOT import pymavlink unless the file is a binary format — safe to
    call unconditionally.
    """
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in ('.bin', '.tlog', '.rlog'):
        return None  # CSV — nothing to auto-detect

    try:
        from pymavlink import mavutil
    except ImportError:
        return None

    try:
        mlog = mavutil.mavlink_connection(file_path)
    except Exception:
        return None

    motor_count = None
    max_bat_voltage = None
    bat_voltage_samples = []
    firmware_label = None

    try:
        count = 0
        while True:
            try:
                msg = mlog.recv_match()
            except Exception:
                break
            if msg is None:
                break

            msg_type = msg.get_type()

            # --- .bin Dataflash ---
            if msg_type == 'PARM':
                name = getattr(msg, 'Name', '')
                val = getattr(msg, 'Value', None)
                if name == 'FRAME_CLASS' and val is not None:
                    if int(val) != 2:
                        # Not ArduPilot Copter — bail out
                        mlog.close()
                        airframe = _FRAMECLASS_AIRFRAME.get(int(val), f'type {int(val)}')
                        print(f"Auto-detect: FRAME_CLASS={int(val)} ({airframe}) — "
                              "only Copter is supported.  "
                              "Using default components.")
                        return None
                elif name == 'MOT_BAT_VOLT_MAX' and val is not None:
                    max_bat_voltage = float(val)

            elif msg_type == 'BAT' and motor_count is None:
                # Accumulate a few voltage samples for cell-count estimation
                volt = getattr(msg, 'Volt', None)
                if volt is not None and len(bat_voltage_samples) < 20:
                    bat_voltage_samples.append(float(volt))

            elif msg_type == 'RCOU' and motor_count is None:
                # Count non-zero RCOU channels
                active = 0
                for i in range(1, 15):
                    val = getattr(msg, f'C{i}', None)
                    if val is not None and float(val) > 0:
                        active = max(active, i)
                if active > 0:
                    motor_count = active

            # --- .tlog MAVLink ---
            elif msg_type == 'HEARTBEAT':
                autopilot = getattr(msg, 'autopilot', None)
                mavtype = getattr(msg, 'type', None)
                if autopilot is not None and autopilot != 3:
                    mlog.close()
                    print(f"Auto-detect: MAV_AUTOPILOT={autopilot} — "
                          "only ArduPilot is supported.  "
                          "Using default components.")
                    return None
                if mavtype is not None and motor_count is None:
                    motor_count = _MAVTYPE_MOTOR_COUNT.get(int(mavtype))

            elif msg_type == 'AUTOPILOT_VERSION':
                fw = getattr(msg, 'flight_sw_version', None)
                if fw is not None:
                    firmware_label = f"ArduPilot {fw}"

            elif msg_type == 'SYS_STATUS' and len(bat_voltage_samples) < 20:
                volt = getattr(msg, 'voltage_battery', None)
                if volt is not None:
                    bat_voltage_samples.append(float(volt) / 1000.0)

            elif msg_type == 'PARAM_VALUE':
                pid = getattr(msg, 'param_id', '')
                if pid:
                    pid = pid.strip('\x00')
                if pid == 'FRAME_CLASS':
                    val = getattr(msg, 'param_value', None)
                    if val is not None and int(float(val)) != 2:
                        mlog.close()
                        print(f"Auto-detect: FRAME_CLASS={int(float(val))} — "
                              "only Copter is supported.  "
                              "Using default components.")
                        return None

            count += 1
            if count > 200000:
                break

    finally:
        if 'mlog' in locals():
            mlog.close()

    # --- Determine cell count from voltage ---
    cell_count = None
    if max_bat_voltage is not None:
        # Use MOT_BAT_VOLT_MAX parameter (most reliable)
        cell_count = round(float(max_bat_voltage) / 4.2)
    elif bat_voltage_samples:
        # Estimate from first N voltage readings
        avg_v = sum(bat_voltage_samples) / len(bat_voltage_samples)
        cell_count = round(avg_v / 3.7)
    cell_count = max(1, min(12, cell_count or 6))

    # --- Determine motor count ---
    if motor_count is None:
        motor_count = 6  # default hexa

    # --- Map motor count → propulsion ---
    PROPULSION_BY_MOTORS = {
        4: 'quad_x',
        6: 'pwm_standard',
        8: 'x8_flat_octo',
    }
    propulsion = PROPULSION_BY_MOTORS.get(motor_count, 'pwm_standard')

    # --- Map cell count → power system ---
    power = f'{cell_count}s_lipo'
    # Verify the power profile actually exists
    available_power = _list_available('power_systems')
    if power not in available_power:
        # Find the closest available cell count
        cell_nums = sorted(
            [int(n.replace('s_lipo', '')) for n in available_power
             if n.endswith('s_lipo')]
        )
        if cell_nums:
            closest = min(cell_nums, key=lambda c: abs(c - cell_count))
            power = f'{closest}s_lipo'

    result = {
        'fc': DEFAULT_FC,
        'power': power,
        'propulsion': propulsion,
    }

    print(
        f"Auto-detect: {motor_count}-motor airframe -> '{propulsion}', "
        f"~{cell_count}S battery -> '{power}', "
        f"FC -> '{DEFAULT_FC}' (default)"
        + (f"  [{firmware_label}]" if firmware_label else "")
    )

    return result


# --- CLI ---
if __name__ == "__main__":
    fc         = DEFAULT_FC
    power      = DEFAULT_POWER
    propulsion = DEFAULT_PROPULSION

    # Minimal arg parser for --fc / --power / --propulsion flags
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
                    marker = ""
                    if (cat == "fc" and n == DEFAULT_FC) or \
                       (cat == "power" and n == DEFAULT_POWER) or \
                       (cat == "propulsion" and n == DEFAULT_PROPULSION):
                        marker = "  [default]"
                    print(f"    - {n}{marker}")
            sys.exit(0)
        elif args[i] in ("--help", "-h"):
            print(__doc__)
            print("Available components:")
            for cat, names in list_all_components().items():
                print(f"  {CATEGORY_DIRS[cat]}/: {', '.join(names)}")
            print(f"\nDefaults: --fc {DEFAULT_FC} --power {DEFAULT_POWER} "
                  f"--propulsion {DEFAULT_PROPULSION}")
            sys.exit(0)
        else:
            i += 1

    try:
        profile = build_profile(fc=fc, power=power, propulsion=propulsion)
        print(json.dumps(profile, indent=2))
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
