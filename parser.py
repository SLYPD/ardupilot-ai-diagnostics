import sys
import pandas as pd
from template_builder import build_profile


# ---------------------------------------------------------------------------
# MAVLink Binary Log Ingestion
# ---------------------------------------------------------------------------

def parse_binary_log(file_path):
    """Read an ArduPilot .bin (Dataflash) or .tlog (Telemetry) file via
    pymavlink and return a pandas DataFrame matching the internal CSV schema.

    Maps MAVLink message types to our standard columns:
        BAT  -> BAT.Volt, BAT.Curr
        POWR -> VCC
        VIBE -> VIBE.VibeX, VIBE.VibeY, VIBE.VibeZ, VIBE.Clip0/1/2
        ATT  -> ATT.DesRoll, ATT.Roll, ATT.DesPitch, ATT.Pitch
        RCOU -> RCOU.C1 .. RCOU.C14

    Returns a DataFrame with columns matching the expected CSV schema,
    or an empty DataFrame if no relevant messages were found.

    Raises ImportError when pymavlink is missing and RuntimeError when
    the log is corrupt or unreadable.
    """
    try:
        from pymavlink import mavutil
    except ImportError:
        raise ImportError(
            "pymavlink is required to read binary ArduPilot logs. "
            "Install it with: pip install pymavlink"
        )

    try:
        mlog = mavutil.mavlink_connection(file_path)
    except Exception as exc:
        raise RuntimeError(
            f"Unable to open binary log '{file_path}'. "
            f"Verify the file is a valid ArduPilot .bin or .tlog. "
            f"Underlying error: {exc}"
        ) from exc

    try:
        # Message-type -> [(source_attr, our_column_name), ...]
        FIELD_MAP = {
            'BAT':  [('Volt', 'BAT.Volt'), ('Curr', 'BAT.Curr')],
            'POWR': [('Vcc', 'VCC')],
            'VIBE': [
                ('VibeX', 'VIBE.VibeX'), ('VibeY', 'VIBE.VibeY'),
                ('VibeZ', 'VIBE.VibeZ'),
                ('Clip0', 'VIBE.Clip0'), ('Clip1', 'VIBE.Clip1'),
                ('Clip2', 'VIBE.Clip2'),
            ],
            'ATT': [
                ('DesRoll', 'ATT.DesRoll'), ('Roll', 'ATT.Roll'),
                ('DesPitch', 'ATT.DesPitch'), ('Pitch', 'ATT.Pitch'),
            ],
            'RCOU': [(f'C{i}', f'RCOU.C{i}') for i in range(1, 15)],
        }

        streams = {mtype: [] for mtype in FIELD_MAP}
        seen_fields = {mtype: set() for mtype in FIELD_MAP}

        while True:
            try:
                msg = mlog.recv_match()
            except Exception as exc:
                raise RuntimeError(
                    f"Error reading binary log — file may be truncated "
                    f"or corrupt. ({exc})"
                ) from exc

            if msg is None:
                break

            msg_type = msg.get_type()
            if msg_type not in FIELD_MAP:
                continue

            # Audit: collect every field name the firmware exposes on this
            # message type so we can verify FIELD_MAP alignment.
            for field_name in msg.get_fieldnames():
                seen_fields[msg_type].add(field_name)

            # ArduPilot timestamps are in microseconds (TimeUS)
            ts = getattr(msg, 'TimeUS', None)
            if ts is None:
                continue
            ts = ts / 1_000_000.0

            row = {'TimeS': ts}
            for attr, col in FIELD_MAP[msg_type]:
                if hasattr(msg, attr):
                    row[col] = getattr(msg, attr)

            streams[msg_type].append(row)

        # =================================================================
        # STRUCTURE AUDIT — print the actual firmware field layout found
        # in this specific log so we can verify FIELD_MAP alignment.
        # =================================================================
        print("Parser: === Firmware Structure Audit ===")
        for mtype in ('BAT', 'POWR', 'VIBE', 'RCOU', 'ATT'):
            fields = seen_fields.get(mtype, set())
            if fields:
                print(f"  {mtype} ({len(fields)} fields): {sorted(fields)}")
            else:
                print(f"  {mtype}: (no messages found)")
        print("Parser: ==================================")

        # Build per-type DataFrames indexed by TimeS.
        # ArduPilot can log multiple messages at the same TimeUS
        # microsecond — deduplicate each stream's index before any
        # merge or reindex so Pandas never sees duplicate labels.
        dfs = {}
        for mtype, data in streams.items():
            if data:
                df = pd.DataFrame(data).set_index('TimeS')
                df = df[~df.index.duplicated(keep='last')]
                df = df.dropna(axis=1, how='all')
                if not df.empty:
                    dfs[mtype] = df

        if not dfs:
            print("Parser: Binary log contained no BAT/POWR/VIBE/ATT/RCOU "
                  "messages.")
            return pd.DataFrame()

        # Unified timeline -> outer-join each stream with forward-fill
        all_times = sorted(
            set().union(*(set(df.index) for df in dfs.values()))
        )

        aligned = {}
        for mtype, df in dfs.items():
            df = df.reindex(df.index.union(all_times)).sort_index()
            df = df.ffill()
            df = df.reindex(all_times)
            aligned[mtype] = df

        result = pd.concat(aligned.values(), axis=1)
        result = result.reset_index(names='TimeS')

        # Baseline columns — default to 0 where the log didn't contain them
        for col in ['VIBE.Clip0', 'VIBE.Clip1', 'VIBE.Clip2', 'BAT.Enrg']:
            if col not in result.columns:
                result[col] = 0

        print(f"Parser: Extracted {len(result)} unified rows from {file_path}")
        return result

    finally:
        # Close the pymavlink connection so Windows can unlink the temp file.
        # Guard with locals() check so a failed mavlink_connection() call
        # that never assigned 'mlog' won't mask the original exception.
        if 'mlog' in locals():
            mlog.close()


# ---------------------------------------------------------------------------
# Anomaly Extraction Engine
# ---------------------------------------------------------------------------

def extract_anomalies(csv_path, profile=None, fc=None, power=None, propulsion=None):
    """Scans an ArduPilot telemetry CSV for hardware anomalies.

    Parameters:
        csv_path:   Path to the telemetry CSV file.
        profile:    A pre-built hardware profile dict (from template_builder).
                    When provided, fc/power/propulsion are ignored.
        fc:         Flight controller component name (e.g. 'pixhawk_6c').
        power:      Power system component name (e.g. '6s_lipo').
        propulsion: Propulsion component name (e.g. 'pwm_standard').

    If *profile* is None, a profile is assembled from the component arguments
    (falling back to defaults for any that are omitted).

    Returns a list of dicts, each containing:
        - type:      short anomaly label (e.g. "VCC_DROP", "MAX_THRUST_RCOU.C4")
        - timestamp: the row index where the anomaly was first detected
        - data:      a CSV string of the surrounding context window
    """
    # --- Resolve the profile ---
    if profile is None:
        profile = build_profile(fc=fc, power=power, propulsion=propulsion)

    cfg = profile
    thr = cfg["thresholds"]
    air = cfg["airframe"]
    pwr = cfg["power_system"]
    ctx = cfg["context_window"]

    # --- Unpack thresholds from the profile ---
    VCC_MIN     = pwr["vcc"]["min_v"]
    VCC_MAX     = pwr["vcc"]["max_v"]
    VIBE_Z_MAX  = thr["vibration"]["z_max_m_s2"]
    VIBE_XY_MAX = thr["vibration"]["xy_max_m_s2"]
    PWM_REDLINE = thr["pwm"]["redline"]

    IMB_HIGH    = thr["motor_imbalance"]["high_threshold_pwm"]
    IMB_LOW     = thr["motor_imbalance"]["low_threshold_pwm"]

    SAG_V_RATIO = thr["voltage_sag"]["volt_drop_ratio"]
    SAG_C_RATIO = thr["voltage_sag"]["current_spike_ratio"]

    CONTEXT_BEFORE = ctx["rows_before"]
    CONTEXT_AFTER  = ctx["rows_after"]

    # --- Motor pairs from profile (or auto-compute from motor_count) ---
    motor_pairs = air.get("motor_pairs", None)
    if motor_pairs is None:
        n = air.get("motor_count", 6)
        half = n // 2
        motor_pairs = [
            [f"RCOU.C{i}", f"RCOU.C{i + half}"]
            for i in range(1, half + 1)
        ]

    # --- Load the log (CSV or binary) ---
    print(f"Parser: Loading {csv_path} ...")
    ext = csv_path.lower().rsplit('.', 1)[-1] if '.' in csv_path else ''
    if ext in ('bin', 'tlog'):
        df = parse_binary_log(csv_path)
    else:
        df = pd.read_csv(csv_path)

    anomalies = []  # list of (label, first_violation_index)

    # ----------------------------------------------------------------
    # A. Power Delivery — VCC brownout / over-voltage
    # ----------------------------------------------------------------
    if 'VCC' in df.columns:
        drops = df[df['VCC'] < VCC_MIN]
        if not drops.empty:
            anomalies.append(("VCC_DROP", drops.index[0]))

        spikes = df[df['VCC'] > VCC_MAX]
        if not spikes.empty:
            anomalies.append(("VCC_OVER_VOLT", spikes.index[0]))

    # ----------------------------------------------------------------
    # B. Voltage Sag — BAT.Volt drop correlated with current spike
    # ----------------------------------------------------------------
    if 'BAT.Volt' in df.columns and 'BAT.Curr' in df.columns:
        volt_med = df['BAT.Volt'].median()
        curr_med = df['BAT.Curr'].median()
        sag_mask = (
            (df['BAT.Volt'] < volt_med * SAG_V_RATIO) &
            (df['BAT.Curr'] > curr_med * SAG_C_RATIO)
        )
        sag_events = df[sag_mask]
        if not sag_events.empty:
            anomalies.append(("VOLTAGE_SAG", sag_events.index[0]))

    # ----------------------------------------------------------------
    # C. Max Thrust — any motor at or above the PWM redline
    # ----------------------------------------------------------------
    motor_cols = [c for c in df.columns if c.startswith('RCOU.C')]
    for motor_col in motor_cols:
        redline_events = df[df[motor_col] > PWM_REDLINE]
        if not redline_events.empty:
            anomalies.append(
                (f"MAX_THRUST_{motor_col}", redline_events.index[0])
            )

    # ----------------------------------------------------------------
    # D. Motor Imbalance — opposing pairs diverging beyond thresholds
    # ----------------------------------------------------------------
    for m_a, m_b in motor_pairs:
        if m_a in df.columns and m_b in df.columns:
            imbalance = df[
                ((df[m_a] > IMB_HIGH) & (df[m_b] < IMB_LOW)) |
                ((df[m_b] > IMB_HIGH) & (df[m_a] < IMB_LOW))
            ]
            if not imbalance.empty:
                anomalies.append(
                    (f"IMBALANCE_{m_a}_vs_{m_b}", imbalance.index[0])
                )

    # ----------------------------------------------------------------
    # E. Vibration Spikes — Z-axis and XY-axis
    # ----------------------------------------------------------------
    for axis, col, limit in [
        ("Z", "VIBE.VibeZ", VIBE_Z_MAX),
        ("X", "VIBE.VibeX", VIBE_XY_MAX),
        ("Y", "VIBE.VibeY", VIBE_XY_MAX),
    ]:
        if col in df.columns:
            spikes = df[df[col] > limit]
            if not spikes.empty:
                anomalies.append((f"VIBE_{axis}_SPIKE", spikes.index[0]))

    # ----------------------------------------------------------------
    # F. IMU Clipping — mechanical shock or sensor saturation
    # ----------------------------------------------------------------
    clip_cols = [c for c in df.columns if c.startswith('VIBE.Clip')]
    for clip_col in clip_cols:
        clip_events = df[df[clip_col] > 0]
        if not clip_events.empty:
            anomalies.append(
                (f"IMU_CLIP_{clip_col}", clip_events.index[0])
            )

    # ----------------------------------------------------------------
    # G. Build Context-Window Payloads
    # ----------------------------------------------------------------
    essential_cols = [
        'TimeS',
        'BAT.Volt', 'BAT.Curr', 'BAT.Enrg',
        'VCC',
        'VIBE.VibeX', 'VIBE.VibeY', 'VIBE.VibeZ',
        'VIBE.Clip0', 'VIBE.Clip1', 'VIBE.Clip2',
        'ATT.DesRoll', 'ATT.Roll', 'ATT.DesPitch', 'ATT.Pitch',
        'RCOU.C1', 'RCOU.C2', 'RCOU.C3', 'RCOU.C4',
        'RCOU.C5', 'RCOU.C6', 'RCOU.C7', 'RCOU.C8',
    ]

    payloads = []
    for anomaly_type, index in anomalies:
        start_idx = max(0, index - CONTEXT_BEFORE)
        end_idx   = min(len(df), index + CONTEXT_AFTER)

        window = df.iloc[start_idx:end_idx]

        available_cols = [c for c in essential_cols if c in window.columns]
        clean_window = window[available_cols]

        payloads.append({
            "type":      anomaly_type,
            "timestamp": int(index),
            "data":      clean_window.to_csv(index=False),
        })

    return payloads


# --- Standalone Execution ---
if __name__ == "__main__":
    log_file   = "flight_log_01.csv"
    fc         = None
    power      = None
    propulsion = None

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
        elif args[i] in ("--help", "-h"):
            print("Usage: python parser.py [--fc NAME] [--power NAME] "
                  "[--propulsion NAME]")
            from template_builder import (
                DEFAULT_FC, DEFAULT_POWER, DEFAULT_PROPULSION
            )
            print("\nComponent flags (all optional, defaults shown):")
            print(f"  --fc         (default: {DEFAULT_FC})")
            print(f"  --power      (default: {DEFAULT_POWER})")
            print(f"  --propulsion (default: {DEFAULT_PROPULSION})")
            sys.exit(0)
        else:
            i += 1

    try:
        results = extract_anomalies(
            log_file, fc=fc, power=power, propulsion=propulsion
        )

        if not results:
            print("Parser: No anomalies detected. Hardware is nominal.")
        else:
            print(f"Parser: Found {len(results)} anomaly window(s).\n")
            print("-" * 60)
            for idx, r in enumerate(results):
                print(f"Payload {idx + 1}: {r['type']}  @ row {r['timestamp']}")
                print(r['data'])
                print("-" * 60)

    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)
