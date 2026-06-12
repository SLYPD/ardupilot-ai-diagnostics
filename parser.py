import os
import sys
import pandas as pd
from template_builder import build_profile, DEFAULT_FC, DEFAULT_POWER, DEFAULT_PROPULSION

# Maximum input size for safety (500 MB)
_MAX_CSV_BYTES = 500 * 1024 * 1024
_MAX_CSV_ROWS = 1_000_000
_MAX_CSV_COLS = 50


# ---------------------------------------------------------------------------
# MAVLink Binary Log Ingestion
# ---------------------------------------------------------------------------

def parse_binary_log(file_path):
    """Read an ArduPilot .bin (Dataflash) or .tlog/.rlog (MAVLink telemetry)
    file via pymavlink and return a pandas DataFrame matching the internal
    CSV schema.

    Dispatches to the correct format-specific handler:
        .bin  → _parse_bin_dataflash()  — Dataflash internal message types
        .tlog → _parse_tlog_mavlink()   — MAVLink v2 message types
        .rlog → _parse_tlog_mavlink()   — auto-redirects to matching .tlog
                                         if one exists in the same directory

    Returns a DataFrame with columns matching the expected CSV schema,
    or an empty DataFrame if no relevant messages were found.

    Raises ImportError when pymavlink is missing and RuntimeError when
    the log is corrupt or unreadable.
    """
    import os as _os

    ext = _os.path.splitext(file_path)[1].lower()

    if ext == '.bin':
        return _parse_bin_dataflash(file_path)
    elif ext in ('.tlog', '.rlog'):
        if ext == '.rlog':
            # .rlog files are Mission Planner debug artefacts that mix
            # binary MAVLink with ASCII debug text — they cannot be parsed
            # reliably.  Redirect to the matching .tlog when it exists.
            tlog_path = _os.path.splitext(file_path)[0] + '.tlog'
            if _os.path.exists(tlog_path):
                print(f"Parser: .rlog -> redirecting to matching .tlog: "
                      f"{_os.path.basename(tlog_path)}")
                return _parse_tlog_mavlink(tlog_path)
            # Fall through to best-effort parse if no .tlog is available.
            print("Parser: WARNING — .rlog files are mixed binary/text "
                  "and may not parse correctly.  If a matching .tlog "
                  "file is available, place it in the same directory.")
        return _parse_tlog_mavlink(file_path)
    else:
        raise RuntimeError(
            f"Unsupported binary log format: '{ext}'. "
            f"Expected .bin, .tlog, or .rlog."
        )


def _parse_bin_dataflash(file_path):
    """Parse an ArduPilot .bin (Dataflash) log via pymavlink.

    Maps Dataflash-internal message types to our standard columns:
        BAT  → BAT.Volt, BAT.Curr, BAT.Enrg (from EnrgTot)
        POWR → VCC
        VIBE → VIBE.VibeX/Y/Z + VIBE.Clip (demuxed to Clip0/1/2 by IMU)
        ATT  → ATT.DesRoll, ATT.Roll, ATT.DesPitch, ATT.Pitch
        RCOU → RCOU.C1 .. RCOU.C14
    """
    try:
        from pymavlink import mavutil
    except ImportError as exc:
        raise ImportError(
            "pymavlink is required to read binary ArduPilot logs. "
            "Install it with: pip install pymavlink"
        ) from exc

    try:
        mlog = mavutil.mavlink_connection(file_path)
    except Exception as exc:
        raise RuntimeError(
            f"Unable to open binary log '{file_path}'. "
            f"Verify the file is a valid ArduPilot .bin. "
            f"Underlying error: {exc}"
        ) from exc

    try:
        # Dataflash-internal message type → [(source_attr, our_col), …]
        BIN_FIELD_MAP = {
            'BAT':  [
                ('Volt', 'BAT.Volt'), ('Curr', 'BAT.Curr'),
                ('EnrgTot', 'BAT.Enrg'),
            ],
            'POWR': [('Vcc', 'VCC')],
            'VIBE': [
                ('VibeX', 'VIBE.VibeX'), ('VibeY', 'VIBE.VibeY'),
                ('VibeZ', 'VIBE.VibeZ'),
                ('Clip', 'VIBE.Clip'),   # single field → demuxed below
                ('IMU', 'VIBE.IMU'),     # which IMU this reading is from
            ],
            'ATT': [
                ('DesRoll', 'ATT.DesRoll'), ('Roll', 'ATT.Roll'),
                ('DesPitch', 'ATT.DesPitch'), ('Pitch', 'ATT.Pitch'),
            ],
            'RCOU': [(f'C{i}', f'RCOU.C{i}') for i in range(1, 15)],
        }

        streams = {mtype: [] for mtype in BIN_FIELD_MAP}
        seen_fields = {mtype: set() for mtype in BIN_FIELD_MAP}

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
            if msg_type not in BIN_FIELD_MAP:
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
            for attr, col in BIN_FIELD_MAP[msg_type]:
                if hasattr(msg, attr):
                    row[col] = getattr(msg, attr)

            streams[msg_type].append(row)

        # =================================================================
        # STRUCTURE AUDIT
        # =================================================================
        print("Parser: === Firmware Structure Audit (.bin Dataflash) ===")
        for mtype in ('BAT', 'POWR', 'VIBE', 'RCOU', 'ATT'):
            fields = seen_fields.get(mtype, set())
            if fields:
                print(f"  {mtype} ({len(fields)} fields): {sorted(fields)}")
            else:
                print(f"  {mtype}: (no messages found)")
        print("Parser: ==================================")

        # Build per-type DataFrames indexed by TimeS.
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

        # Unified timeline — outer-join each stream with forward-fill
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

        # --- Demux VIBE.Clip → Clip0/Clip1/Clip2 by IMU index ---
        if 'VIBE.Clip' in result.columns and 'VIBE.IMU' in result.columns:
            for imu_idx in range(3):
                col_name = f'VIBE.Clip{imu_idx}'
                result[col_name] = 0
                mask = result['VIBE.IMU'] == imu_idx
                result.loc[mask, col_name] = result.loc[mask, 'VIBE.Clip']
            result = result.drop(columns=['VIBE.Clip', 'VIBE.IMU'],
                                 errors='ignore')
        elif 'VIBE.Clip' in result.columns:
            # Fallback for older firmware that doesn't expose IMU field:
            # propagate the single Clip value to all three columns.
            result['VIBE.Clip0'] = result['VIBE.Clip']
            result['VIBE.Clip1'] = result['VIBE.Clip']
            result['VIBE.Clip2'] = result['VIBE.Clip']
            result = result.drop(columns=['VIBE.Clip'], errors='ignore')

        # Baseline columns — default to 0 where the log didn't contain them
        for col in ['VIBE.Clip0', 'VIBE.Clip1', 'VIBE.Clip2', 'BAT.Enrg']:
            if col not in result.columns:
                result[col] = 0

        print(f"Parser: Extracted {len(result)} unified rows from {file_path}")
        return result

    finally:
        if 'mlog' in locals():
            mlog.close()


def _parse_tlog_mavlink(file_path):
    """Parse a MAVLink .tlog telemetry log via pymavlink.

    Maps standard MAVLink v2 message types to our internal schema with
    unit conversions where needed:

        SYS_STATUS            → BAT.Volt (mV→V), BAT.Curr (cA→A)
        POWER_STATUS          → VCC (mV→V)
        VIBRATION             → VIBE.VibeX/Y/Z, VIBE.Clip0/1/2
        ATTITUDE              → ATT.Roll, ATT.Pitch (rad→deg)
        NAV_CONTROLLER_OUTPUT → ATT.DesRoll, ATT.DesPitch (deg, no conv)
        SERVO_OUTPUT_RAW      → RCOU.C1 .. RCOU.C14
    """
    try:
        from pymavlink import mavutil
    except ImportError as exc:
        raise ImportError(
            "pymavlink is required to read MAVLink telemetry logs. "
            "Install it with: pip install pymavlink"
        ) from exc

    import math as _math

    # MAVLink message type → [(src_attr, dst_col, converter_fn_or_None), …]
    TLOG_FIELD_MAP = {
        'SYS_STATUS': [
            # voltage_battery is in millivolts → volts
            ('voltage_battery', 'BAT.Volt', lambda v: v / 1000.0),
            # current_battery is in centiamps → amps
            ('current_battery', 'BAT.Curr', lambda v: v / 100.0),
        ],
        'POWER_STATUS': [
            # Vcc is in millivolts → volts
            ('Vcc', 'VCC', lambda v: v / 1000.0),
        ],
        'VIBRATION': [
            ('vibration_x', 'VIBE.VibeX', None),
            ('vibration_y', 'VIBE.VibeY', None),
            ('vibration_z', 'VIBE.VibeZ', None),
            ('clipping_0', 'VIBE.Clip0', None),
            ('clipping_1', 'VIBE.Clip1', None),
            ('clipping_2', 'VIBE.Clip2', None),
        ],
        'ATTITUDE': [
            # roll/pitch are in radians → degrees
            ('roll', 'ATT.Roll', lambda v: _math.degrees(v)),
            ('pitch', 'ATT.Pitch', lambda v: _math.degrees(v)),
        ],
        'NAV_CONTROLLER_OUTPUT': [
            # nav_roll/nav_pitch are already in degrees
            ('nav_roll', 'ATT.DesRoll', None),
            ('nav_pitch', 'ATT.DesPitch', None),
        ],
        'SERVO_OUTPUT_RAW': [
            (f'servo{i}_raw', f'RCOU.C{i}', None) for i in range(1, 15)
        ],
    }

    try:
        mlog = mavutil.mavlink_connection(file_path)
    except Exception as exc:
        raise RuntimeError(
            f"Unable to open MAVLink telemetry log '{file_path}'. "
            f"Verify the file is a valid .tlog. "
            f"Underlying error: {exc}"
        ) from exc

    try:
        streams = {mtype: [] for mtype in TLOG_FIELD_MAP}
        seen_types = set()

        while True:
            try:
                msg = mlog.recv_match()
            except Exception as exc:
                raise RuntimeError(
                    f"Error reading telemetry log — file may be truncated "
                    f"or corrupt. ({exc})"
                ) from exc

            if msg is None:
                break

            msg_type = msg.get_type()
            if msg_type not in TLOG_FIELD_MAP:
                continue

            seen_types.add(msg_type)

            # Use pymavlink's packet-level _timestamp (Unix seconds)
            ts = getattr(msg, '_timestamp', None)
            if ts is None:
                continue

            row = {'TimeS': ts}
            for attr, col, conv in TLOG_FIELD_MAP[msg_type]:
                if hasattr(msg, attr):
                    val = getattr(msg, attr)
                    if conv is not None:
                        val = conv(val)
                    row[col] = val

            streams[msg_type].append(row)

        # Report which message types were actually found
        print("Parser: === MAVLink Telemetry Structure Audit ===")
        for mtype in TLOG_FIELD_MAP:
            marker = "[OK]" if mtype in seen_types else "[MISSING]"
            print(f"  {mtype}: {marker}")
        print("Parser: ==================================")

        # Build per-type DataFrames indexed by TimeS
        dfs = {}
        for mtype, data in streams.items():
            if data:
                df = pd.DataFrame(data).set_index('TimeS')
                df = df[~df.index.duplicated(keep='last')]
                df = df.dropna(axis=1, how='all')
                if not df.empty:
                    dfs[mtype] = df

        if not dfs:
            print("Parser: Telemetry log contained no relevant "
                  "SYS_STATUS/POWER_STATUS/VIBRATION/ATTITUDE/"
                  "NAV_CONTROLLER_OUTPUT/SERVO_OUTPUT_RAW messages.")
            return pd.DataFrame()

        # Unified timeline — outer-join each stream with forward-fill
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

    # --- Load the log (CSV or binary) with safety checks ---
    print(f"Parser: Loading {csv_path} ...")
    ext = csv_path.lower().rsplit('.', 1)[-1] if '.' in csv_path else ''
    if ext in ('bin', 'tlog', 'rlog'):
        df = parse_binary_log(csv_path)
    else:
        # Validate CSV before full parsing
        file_size = os.path.getsize(csv_path)
        if file_size == 0:
            raise ValueError("CSV file is empty.")
        if file_size > _MAX_CSV_BYTES:
            raise ValueError(
                f"CSV file too large: {file_size / (1024*1024):.1f} MB. "
                f"Maximum is {_MAX_CSV_BYTES // (1024*1024)} MB."
            )
        df = pd.read_csv(csv_path)
        if len(df) > _MAX_CSV_ROWS:
            raise ValueError(
                f"CSV has {len(df)} rows; maximum is {_MAX_CSV_ROWS}."
            )
        if len(df.columns) > _MAX_CSV_COLS:
            raise ValueError(
                f"CSV has {len(df.columns)} columns; maximum is {_MAX_CSV_COLS}."
            )

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
    # --- Resolve battery absolute minimum from profile ---
    BAT_MIN_SAFE = pwr.get("battery", {}).get("min_safe_voltage_v", None)

    if 'BAT.Volt' in df.columns and 'BAT.Curr' in df.columns:
        # Rolling-median sag detection (window of 10 rows) so the baseline
        # adapts to recent flight conditions rather than the entire log.
        volt_med = df['BAT.Volt'].rolling(window=10, center=True).median()
        curr_med = df['BAT.Curr'].rolling(window=10, center=True).median()
        sag_mask = (
            (df['BAT.Volt'] < volt_med * SAG_V_RATIO) &
            (df['BAT.Curr'] > curr_med * SAG_C_RATIO)
        )

        # Also flag any row where voltage drops below the battery's absolute
        # minimum safe voltage (physics-referenced floor check).
        if BAT_MIN_SAFE is not None:
            sag_mask = sag_mask | (df['BAT.Volt'] < BAT_MIN_SAFE)

        sag_events = df[sag_mask]
        if not sag_events.empty:
            anomalies.append(("VOLTAGE_SAG", sag_events.index[0]))

    # ----------------------------------------------------------------
    # C. Max Thrust — any motor at or above the PWM redline
    # ----------------------------------------------------------------
    motor_cols = [c for c in df.columns if c.startswith('RCOU.C')]
    for motor_col in motor_cols:
        redline_events = df[df[motor_col] >= PWM_REDLINE]
        if not redline_events.empty:
            anomalies.append(
                (f"MAX_THRUST_{motor_col}", redline_events.index[0])
            )

    # ----------------------------------------------------------------
    # D. Motor Imbalance — opposing pairs diverging beyond thresholds
    #    Requires 3+ consecutive rows of divergence to filter single-row
    #    sensor glitches.
    # ----------------------------------------------------------------
    for m_a, m_b in motor_pairs:
        if m_a in df.columns and m_b in df.columns:
            imbalance_raw = (
                ((df[m_a] > IMB_HIGH) & (df[m_b] < IMB_LOW)) |
                ((df[m_b] > IMB_HIGH) & (df[m_a] < IMB_LOW))
            ).astype(int)
            # Persistence filter: 3+ consecutive rows
            persistent = imbalance_raw.rolling(window=3, min_periods=1).sum() >= 3
            if persistent.any():
                anomalies.append(
                    (f"IMBALANCE_{m_a}_vs_{m_b}", persistent.idxmax())
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
    # G. ATT Desync — desired vs actual attitude divergence
    # ----------------------------------------------------------------
    att_desync_threshold = thr.get("att_desync", {}).get("max_divergence_deg", 15.0)

    if 'ATT.DesRoll' in df.columns and 'ATT.Roll' in df.columns:
        roll_div = (df['ATT.DesRoll'] - df['ATT.Roll']).abs()
        desync = df[roll_div > att_desync_threshold]
        if not desync.empty:
            anomalies.append(("ATT_DESYNC_ROLL", desync.index[0]))

    if 'ATT.DesPitch' in df.columns and 'ATT.Pitch' in df.columns:
        pitch_div = (df['ATT.DesPitch'] - df['ATT.Pitch']).abs()
        desync = df[pitch_div > att_desync_threshold]
        if not desync.empty:
            anomalies.append(("ATT_DESYNC_PITCH", desync.index[0]))

    # ----------------------------------------------------------------
    # H. VCC Fluctuation — excessive rail voltage noise
    # ----------------------------------------------------------------
    VCC_FLUCT_MAX = pwr.get("vcc", {}).get("max_fluctuation_v", 0.15)
    if 'VCC' in df.columns:
        vcc_roll_max = df['VCC'].rolling(window=5, center=True).max()
        vcc_roll_min = df['VCC'].rolling(window=5, center=True).min()
        vcc_fluct = (vcc_roll_max - vcc_roll_min).fillna(0)
        fluct_events = df[vcc_fluct > VCC_FLUCT_MAX]
        if not fluct_events.empty:
            anomalies.append(("VCC_FLUCTUATION", fluct_events.index[0]))

    # ----------------------------------------------------------------
    # I. Min Thrust — any motor at or below minimum PWM
    # ----------------------------------------------------------------
    PWM_MIN = thr.get("pwm", {}).get("min", 1000)
    for motor_col in motor_cols:
        min_events = df[df[motor_col] <= PWM_MIN]
        if not min_events.empty:
            anomalies.append(
                (f"MIN_THRUST_{motor_col}", min_events.index[0])
            )

    # ----------------------------------------------------------------
    # J. Build Context-Window Payloads
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
    log_file   = None
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
        elif args[i] == "--csv" and i + 1 < len(args):
            log_file = args[i + 1]
            i += 2
        elif args[i] == "--dir" and i + 1 < len(args):
            dir_path = args[i + 1]
            if not os.path.isdir(dir_path):
                print(f"Error: '{dir_path}' is not a directory.", file=sys.stderr)
                sys.exit(1)
            import glob
            files = sorted(
                glob.glob(os.path.join(dir_path, "*.csv")) +
                glob.glob(os.path.join(dir_path, "*.bin")) +
                glob.glob(os.path.join(dir_path, "*.tlog")) +
                glob.glob(os.path.join(dir_path, "*.rlog"))
            )
            if not files:
                print(f"No telemetry files found in '{dir_path}'.", file=sys.stderr)
                sys.exit(0)
            for fpath in files:
                print(f"\n{'='*60}\nProcessing: {os.path.basename(fpath)}\n{'='*60}")
                try:
                    results = extract_anomalies(fpath, fc=fc, power=power, propulsion=propulsion)
                    if not results:
                        print("  No anomalies detected.")
                    else:
                        print(f"  Found {len(results)} anomaly window(s).")
                        for idx, r in enumerate(results):
                            print(f"    [{idx+1}] {r['type']} @ row {r['timestamp']}")
                except Exception as exc:
                    print(f"  Error: {exc}", file=sys.stderr)
            sys.exit(0)
        elif args[i] in ("--help", "-h"):
            print("Usage: python parser.py [--fc NAME] [--power NAME] "
                  "[--propulsion NAME] [--csv FILE] [--dir DIR]")
            print("\nComponent flags (all optional, defaults shown):")
            print(f"  --fc         (default: {DEFAULT_FC})")
            print(f"  --power      (default: {DEFAULT_POWER})")
            print(f"  --propulsion (default: {DEFAULT_PROPULSION})")
            print("\nInput flags:")
            print("  --csv FILE   Process a single telemetry log")
            print("  --dir DIR    Process all .csv/.bin/.tlog files in a directory")
            sys.exit(0)
        else:
            i += 1

    # --- Single-file mode (default: flight_log_01.csv for convenience) ---
    if log_file is None:
        log_file = "flight_log_01.csv"

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
