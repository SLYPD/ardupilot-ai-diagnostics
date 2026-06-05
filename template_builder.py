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
import sys


COMPONENTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "components")

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
    filepath = os.path.join(COMPONENTS_DIR, subdir, f"{name}.json")
    if not os.path.exists(filepath):
        available = _list_available(subdir)
        raise FileNotFoundError(
            f"Component not found: {filepath}\n"
            f"Available in components/{subdir}/:\n"
            + "\n".join(f"  - {a}" for a in available)
            if available else "  (directory is empty or missing)"
        )
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def _list_available(subdir: str) -> list:
    """List available component names (without .json) in a subdirectory."""
    dirpath = os.path.join(COMPONENTS_DIR, subdir)
    if not os.path.isdir(dirpath):
        return []
    return sorted(
        os.path.splitext(f)[0]
        for f in os.listdir(dirpath)
        if f.endswith(".json")
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
                        marker = "  (default)"
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
