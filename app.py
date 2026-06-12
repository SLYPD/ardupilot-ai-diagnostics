"""
Streamlit UI — Hardware & Telemetry Copilot
============================================
Drag-and-drop ArduPilot telemetry CSVs, select hardware components,
and receive structured DeepSeek diagnostic reports.
"""
import html
import os
import re
import time
import uuid
from pathlib import Path

import streamlit as st

from main import (
    build_system_prompt,
    diagnose_anomalies_parallel,
    format_profile_for_llm,
)
from parser import extract_anomalies

# -- backend imports (from the existing pipeline) --
from template_builder import (
    DEFAULT_FC,
    DEFAULT_POWER,
    DEFAULT_PROPULSION,
    auto_detect_profile,
    build_profile,
    list_all_components,
)

# ---------------------------------------------------------------------------
# Security constants
# ---------------------------------------------------------------------------
_MAX_UPLOAD_BYTES = 500 * 1024 * 1024  # 500 MB

# ---------------------------------------------------------------------------
# PAGE CONFIG
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Hardware & Telemetry Copilot",
    page_icon="🛰️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# CUSTOM CSS — clean, high-end engineering-tool aesthetic
# ---------------------------------------------------------------------------
st.markdown("""
<style>
    /* Monospace for telemetry data in expander code blocks */
    .stExpander pre {
        font-family: 'Cascadia Code', 'Fira Code', 'Consolas', monospace;
    }
    /* Summary metric card styling */
    div[data-testid="stMetric"] {
        background-color: #1A1C23;
        border: 1px solid #2E3138;
        border-radius: 8px;
        padding: 8px 12px;
    }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# SIDEBAR — hardware config
# ---------------------------------------------------------------------------
st.sidebar.markdown("# ⚙️ Hardware Configuration")

components_available = list_all_components()

fc_options         = components_available["fc"]
power_options      = components_available["power"]
propulsion_options = components_available["propulsion"]

# Default index for each dropdown — use auto-detected values if available
_detected_fc = st.session_state.get("detected_fc")
_detected_power = st.session_state.get("detected_power")
_detected_prop = st.session_state.get("detected_propulsion")

fc_idx = fc_options.index(_detected_fc) if _detected_fc and _detected_fc in fc_options else (
    fc_options.index(DEFAULT_FC) if DEFAULT_FC in fc_options else 0)
power_idx = power_options.index(_detected_power) if _detected_power and _detected_power in power_options else (
    power_options.index(DEFAULT_POWER) if DEFAULT_POWER in power_options else 0)
prop_idx = propulsion_options.index(_detected_prop) if _detected_prop and _detected_prop in propulsion_options else (
    propulsion_options.index(DEFAULT_PROPULSION) if DEFAULT_PROPULSION in propulsion_options else 0)

# --- Hardware Selection section ---
st.sidebar.subheader("Hardware Selection")

# Show an auto-detect banner when detection succeeded
if _detected_fc or _detected_power or _detected_prop:
    parts = []
    if _detected_fc: parts.append(f"FC: {_detected_fc}")
    if _detected_power: parts.append(f"Power: {_detected_power}")
    if _detected_prop: parts.append(f"Prop: {_detected_prop}")
    st.sidebar.markdown(
        f"<div style='background:#0d3329; border:1px solid #00BFA6; "
        f"border-radius:6px; padding:8px; margin:4px 0; font-size:0.8em;'>"
        f"🛰️ <b>Auto-detected from log</b><br>"
        f"<span style='color:#00BFA6;'>{'  |  '.join(parts)}</span></div>",
        unsafe_allow_html=True,
    )

selected_fc         = st.sidebar.selectbox("Flight Controller", fc_options, index=fc_idx)
selected_power      = st.sidebar.selectbox("Power System", power_options, index=power_idx)
selected_propulsion = st.sidebar.selectbox("Propulsion", propulsion_options, index=prop_idx)

# --- Profile info card ---
st.sidebar.markdown("---")
profile_card_html = f"""
<div style="\
background:#1A1C23;\
 border:1px solid #00BFA6;\
 border-radius:8px;\
 padding:12px;\
 margin:4px 0;\
">
    <div style="font-size:0.8em; color:#8A8A9A; margin-bottom:4px;">ASSEMBLED PROFILE</div>
    <div style="display:flex; justify-content:space-between; flex-wrap:wrap; gap:4px;">
        <span style="color:#EAEAEA; font-weight:600;">FC</span>
        <span style="color:#00BFA6;">{html.escape(selected_fc)}</span>
    </div>
    <div style="display:flex; justify-content:space-between; flex-wrap:wrap; gap:4px;">
        <span style="color:#EAEAEA; font-weight:600;">Power</span>
        <span style="color:#00BFA6;">{html.escape(selected_power)}</span>
    </div>
    <div style="display:flex; justify-content:space-between; flex-wrap:wrap; gap:4px;">
        <span style="color:#EAEAEA; font-weight:600;">Prop</span>
        <span style="color:#00BFA6;">{html.escape(selected_propulsion)}</span>
    </div>
</div>
"""
st.sidebar.markdown(profile_card_html, unsafe_allow_html=True)

# --- API Key section ---
st.sidebar.markdown("---")
st.sidebar.subheader("API Key")
api_key = st.sidebar.text_input(
    "DeepSeek API Key",
    type="password",
    placeholder="sk-...",
    help="Your key is never stored — it is only used for this session.",
    label_visibility="collapsed",
)
if api_key:
    st.sidebar.markdown("🟢 **Connected** — LLM diagnostics active")
else:
    st.sidebar.markdown("⚪ Enter a key to enable LLM diagnostics.")

# --- Footer ---
st.sidebar.markdown("---")
st.sidebar.caption("v1.0.0 — Hardware & Telemetry Copilot")

# ---------------------------------------------------------------------------
# MAIN AREA — title + upload
# ---------------------------------------------------------------------------
st.title("🛰️ Hardware & Telemetry Copilot")
st.caption("Post-mortem diagnostic engine for ArduPilot / MAVLink telemetry logs — native .bin, .tlog, and .csv support.")

st.markdown("---")

uploaded_file = st.file_uploader(
    "📤 Upload a telemetry log",
    type=["csv", "bin", "tlog", "rlog"],
    help="Accepts ArduPilot .bin (Dataflash), .tlog/.rlog (Telemetry), or .csv logs.",
)

# -- Auto-detect aircraft profile from uploaded file --------------------------
if "detected_fc" not in st.session_state:
    st.session_state.detected_fc = None
if "detected_power" not in st.session_state:
    st.session_state.detected_power = None
if "detected_propulsion" not in st.session_state:
    st.session_state.detected_propulsion = None
if "detected_file_name" not in st.session_state:
    st.session_state.detected_file_name = None

if uploaded_file is not None and uploaded_file.name != st.session_state.detected_file_name:
    # Write to temp file so auto_detect can read it
    file_bytes = uploaded_file.getvalue()
    if len(file_bytes) > 0 and len(file_bytes) <= _MAX_UPLOAD_BYTES:
        orig_suffix = Path(uploaded_file.name).suffix or ".bin"
        tmp_path = f"temp_detect_{uuid.uuid4().hex}{orig_suffix}"
        with open(tmp_path, "wb") as f:
            f.write(file_bytes)
        try:
            detected = auto_detect_profile(tmp_path)
            if detected:
                st.session_state.detected_fc = detected.get("fc")
                st.session_state.detected_power = detected.get("power")
                st.session_state.detected_propulsion = detected.get("propulsion")
                st.session_state.detected_file_name = uploaded_file.name
            else:
                st.session_state.detected_fc = None
                st.session_state.detected_power = None
                st.session_state.detected_propulsion = None
                st.session_state.detected_file_name = uploaded_file.name
        except Exception:
            st.session_state.detected_fc = None
            st.session_state.detected_power = None
            st.session_state.detected_propulsion = None
            st.session_state.detected_file_name = uploaded_file.name
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Magic-byte / content validation for uploaded files
# ---------------------------------------------------------------------------
def _validate_file_content(content: bytes, ext: str) -> str | None:
    """Return an error message if the content doesn't match the claimed
    extension, or None if it passes."""
    if ext == "csv":
        # CSV files must start with printable ASCII text
        if not re.match(rb'^[\x20-\x7E\r\n\t,]+', content[:200]):
            return "Uploaded .csv file does not appear to contain valid CSV text."
        return None
    elif ext in ("tlog", "rlog"):
        # MAVLink telemetry files — pymavlink is the authority on whether
        # these are valid.  Only reject files that are provably not MAVLink:
        # check the first 256 bytes for a MAVLink v1/v2 magic marker.
        # Many .tlog files have headers or padding before the first packet,
        # so an exact match at byte 0 is too strict.
        window = content[:256]
        if b'\xfd' in window or b'\xfe' in window:
            return None
        # Also accept all-zeros preamble (timestamp header padding)
        if len(content) >= 8 and content[:8] == b'\x00\x00\x00\x00\x00\x00\x00\x00':
            return None
        # No magic found — but still let pymavlink have the final say.
        # Some valid files have unusual headers; the parser's RuntimeError
        # handler shows a clear message if the file truly can't be read.
        print(f"Warning: No MAVLink magic byte found in first 256 bytes of "
              f".{ext} file — deferring to pymavlink for final validation.")
        return None
    elif ext == "bin":
        # ArduPilot Dataflash logs have their own binary format —
        # pymavlink auto-detects it. Emptiness already checked by caller.
        return None

# ---------------------------------------------------------------------------
# RUN DIAGNOSTICS — the labor illusion
# ---------------------------------------------------------------------------
if st.button("▶ Run Diagnostics", type="primary", disabled=(uploaded_file is None)):
    if uploaded_file is None:
        st.warning("Please upload a telemetry log file first.")
    elif not api_key:
        st.warning("Please enter your DeepSeek API key in the sidebar.")
    else:
        # --- Validate upload size ---
        file_bytes = uploaded_file.getvalue()
        if len(file_bytes) > _MAX_UPLOAD_BYTES:
            st.error(
                f"File too large ({len(file_bytes) / (1024*1024):.1f} MB). "
                f"Maximum allowed size is {_MAX_UPLOAD_BYTES // (1024*1024)} MB."
            )
            st.stop()
        if len(file_bytes) == 0:
            st.error("Uploaded file is empty.")
            st.stop()

        # --- Validate content matches claimed extension ---
        orig_suffix = Path(uploaded_file.name).suffix.lower().lstrip('.') or "csv"
        content_error = _validate_file_content(file_bytes, orig_suffix)
        if content_error:
            st.error(content_error)
            st.stop()

        # Write uploaded bytes to a uniquely-named temp file.
        orig_suffix_full = Path(uploaded_file.name).suffix or ".csv"
        tmp_path = f"temp_flight_{uuid.uuid4().hex}{orig_suffix_full}"
        with open(tmp_path, "wb") as f:
            f.write(file_bytes)

        try:
            # -- Step 1: Assemble profile (outside status so it's ready) --
            profile = build_profile(
                fc=selected_fc, power=selected_power,
                propulsion=selected_propulsion,
            )
            hw_text = format_profile_for_llm(profile)

            # ===============================================================
            # LABOR ILLUSION BLOCK
            # ===============================================================
            with st.status(
                "Executing Diagnostic Pipeline…", expanded=True
            ) as status_pipe:

                progress_bar = st.progress(0, text="Starting pipeline…")

                # --- Phase 1: Assemble ---
                st.write("🔧 **Assembling Hardware Profile…**")
                comps = profile["profile"]["components"]
                st.write(
                    f"   Loaded **{profile['flight_controller']['model']}** "
                    f"({comps['flight_controller']})  |  "
                    f"{profile['power_system']['battery']['cell_count']}S "
                    f"{profile['power_system']['battery']['chemistry']}  "
                    f"({comps['power_system']})  |  "
                    f"**{profile['airframe']['type']}** "
                    f"({comps['propulsion']})"
                )
                progress_bar.progress(25, text="Hardware profile assembled")
                time.sleep(1.5)

                # --- Phase 2: Scan ---
                progress_bar.progress(30, text="Scanning telemetry for limit breaches…")
                st.write("📡 **Scanning raw telemetry for limit breaches…**")
                time.sleep(0.5)

                try:
                    anomalies = extract_anomalies(tmp_path, profile=profile)
                except ImportError as exc:
                    status_pipe.update(
                        label="Diagnostic Pipeline — Aborted",
                        state="error",
                    )
                    st.error(
                        f"**Missing dependency.** {exc}\n\n"
                        "Binary .bin/.tlog logs require pymavlink. "
                        "Install it and restart: `pip install pymavlink`"
                    )
                    st.stop()
                except RuntimeError as exc:
                    status_pipe.update(
                        label="Diagnostic Pipeline — Aborted",
                        state="error",
                    )
                    st.error(
                        f"**Unable to read log file.** {exc}\n\n"
                        "Please verify this is a valid ArduPilot .bin, "
                        ".tlog, .rlog, or .csv file."
                    )
                    st.stop()

                if not anomalies:
                    st.write("   ✅ No anomalies detected — hardware "
                             "health nominal.")
                    progress_bar.progress(100, text="Diagnostic complete — hardware nominal")
                    status_pipe.update(
                        label="Diagnostic Pipeline — Complete",
                        state="complete",
                    )
                else:
                    anomaly_labels = [a["type"] for a in anomalies]
                    st.write(
                        f"   ⚠️  **{len(anomalies)} anomaly window(s)** "
                        f"flagged:"
                    )
                    for lab in anomaly_labels:
                        st.write(f"      · `{lab}`")
                    time.sleep(1.5)

                    # --- Phase 3: Cross-correlate ---
                    progress_bar.progress(50, text="Cross-correlating sensor data…")
                    st.write("🔬 **Cross-correlating sensor data…**")
                    pair_count = len(
                        [a for a in anomaly_labels if "IMBALANCE" in a]
                    )
                    vibe_count = len(
                        [a for a in anomaly_labels if "VIBE" in a]
                    )
                    sag_count = len(
                        [a for a in anomaly_labels if "SAG" in a]
                    )
                    if pair_count and vibe_count:
                        st.write("   Correlating motor imbalance ↔ "
                                 "vibration axis…")
                    if sag_count:
                        st.write("   Correlating voltage sag ↔ current "
                                 "draw…")
                    if not (pair_count or vibe_count or sag_count):
                        st.write("   No multi-sensor correlations "
                                 "required for these anomaly types.")
                    time.sleep(1.5)

                    # --- Phase 4: Synthesize ---
                    progress_bar.progress(75, text="Synthesizing DeepSeek reports…")
                    st.write("🤖 **Synthesizing DeepSeek reports (parallel)**…")
                    import os as _os
                    _conc = _os.environ.get("DEEPSEEK_MAX_CONCURRENCY", "5")
                    st.write(f"   Processing {len(anomalies)} anomaly windows "
                             f"concurrently (max {_conc} at a time)…")
                    time.sleep(0.3)

                    system_prompt = build_system_prompt(hw_text)

                    reports = diagnose_anomalies_parallel(
                        anomalies, system_prompt, api_key=api_key
                    )

                    for idx, report in enumerate(reports):
                        label = report.get("anomaly_type", anomalies[idx]["type"])
                        conf = report.get("confidence_score", "?")
                        if conf.lower() == "inconclusive" and "API ERROR" in str(report.get("root_cause_analysis", "")):
                            st.write(f"      ❌ [{idx+1}/{len(reports)}] `{label}` — API call failed")
                        else:
                            st.write(f"      ✅ [{idx+1}/{len(reports)}] `{label}` — confidence: {conf}")

                    status_pipe.update(
                        label="Diagnostic Pipeline — Complete",
                        state="complete",
                    )
                    progress_bar.progress(100, text="Diagnostic pipeline complete")

            # ===========================================================
            # OUTPUT — render reports below the completed status block
            # ===========================================================
            st.markdown("---")

            # --- Summary metrics row ---
            high_conf = sum(
                1 for r in reports
                if r.get("confidence_score", "").lower() == "high"
            )
            low_conf = sum(
                1 for r in reports
                if r.get("confidence_score", "").lower() == "low"
            )
            inconclusive = len(reports) - high_conf - low_conf

            mcol1, mcol2, mcol3, mcol4 = st.columns(4)
            with mcol1:
                st.metric("Total Anomalies", len(reports))
            with mcol2:
                st.metric("High Confidence", high_conf)
            with mcol3:
                st.metric("Low Confidence", low_conf)
            with mcol4:
                st.metric("Inconclusive", inconclusive)

            # --- Overall Health Assessment ---
            if inconclusive > 0:
                health_emoji, health_label, health_color = (
                    "🔴", "ATTENTION REQUIRED", "#ff5252"
                )
            elif low_conf > 0:
                health_emoji, health_label, health_color = (
                    "🟠", "REVIEW ADVISED", "#ff9100"
                )
            else:
                health_emoji, health_label, health_color = (
                    "🟢", "NOMINAL — HIGH CONFIDENCE", "#00c853"
                )

            st.markdown(
                f"<h3 style='color:{health_color}; margin:0;'>"
                f"{health_emoji} {health_label}</h3>",
                unsafe_allow_html=True,
            )

            # ===========================================================
            # AT-A-GLANCE SUMMARY — checklist table + distribution chart
            # ===========================================================
            st.markdown("---")
            st.subheader("📊 At-a-Glance Summary")

            # --- Categorize anomalies ---
            def _categorize(atype):
                if atype.startswith("VCC_"):
                    return "Power Rail"
                if atype.startswith("VOLTAGE_SAG"):
                    return "Battery Sag"
                if atype.startswith("MAX_THRUST"):
                    return "Max Thrust"
                if atype.startswith("MIN_THRUST"):
                    return "Dead/Stuck Motor"
                if atype.startswith("IMBALANCE"):
                    return "Motor Imbalance"
                if atype.startswith("VIBE"):
                    return "Vibration"
                if atype.startswith("IMU_CLIP"):
                    return "IMU Clipping"
                if atype.startswith("ATT_DESYNC"):
                    return "ATT Desync"
                if atype.startswith("VCC_FLUCT"):
                    return "Power Ripple"
                return "Other"

            def _shorten(text, maxlen=80):
                if not text or text == "NOT PROVIDED BY LLM":
                    return "—"
                cleaned = text.split(". ")[0].strip()
                if len(cleaned) > maxlen:
                    cleaned = cleaned[:maxlen - 3] + "..."
                return cleaned or "—"

            # Build checklist rows
            checklist_rows = []
            category_counts = {}
            for r in reports:
                atype = r.get("anomaly_type", "?")
                cat = _categorize(atype)
                category_counts[cat] = category_counts.get(cat, 0) + 1

                conf = r.get("confidence_score", "?")
                conf_norm = conf.lower()
                if conf_norm == "high":
                    conf_badge = "🟢 High"
                elif conf_norm == "low":
                    conf_badge = "🟠 Low"
                else:
                    conf_badge = "🔴 Inconclusive"

                checklist_rows.append({
                    "#": len(checklist_rows) + 1,
                    "Category": cat,
                    "Anomaly Type": atype,
                    "Confidence": conf_badge,
                    "Root Cause": _shorten(r.get("root_cause_analysis", "")),
                    "Recommended Fix": _shorten(r.get("actionable_fix", ""), 70),
                })

            # Two-column layout: table + chart
            sum_col1, sum_col2 = st.columns([3, 2])

            with sum_col1:
                st.caption(
                    f"Checklist — {len(checklist_rows)} issue(s) identified"
                )
                st.dataframe(
                    checklist_rows,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "#": st.column_config.NumberColumn(width="small"),
                        "Category": st.column_config.TextColumn(width="small"),
                        "Anomaly Type": st.column_config.TextColumn(width="medium"),
                        "Confidence": st.column_config.TextColumn(width="small"),
                        "Root Cause": st.column_config.TextColumn(width="large"),
                        "Recommended Fix": st.column_config.TextColumn(width="large"),
                    },
                )

            with sum_col2:
                st.caption("Distribution by Category")
                if category_counts:
                    st.bar_chart(category_counts, use_container_width=True)

                # Confidence breakdown mini-display
                st.caption("Confidence Breakdown")
                conf_data = {"High": high_conf, "Low": low_conf,
                             "Inconclusive": inconclusive}
                for label, count in conf_data.items():
                    emoji = {"High": "🟢", "Low": "🟠", "Inconclusive": "🔴"}[label]
                    pct = f"{count / max(len(reports), 1) * 100:.0f}%"
                    st.markdown(
                        f"{emoji} **{label}:** {count} ({pct})"
                    )

            st.markdown("---")
            st.subheader(
                f"📋 Detailed Reports  ({len(reports)} found)"
            )

            for idx, report in enumerate(reports):
                conf = report.get(
                    "confidence_score", "Inconclusive"
                ).lower()
                if conf == "high":
                    badge_color = "green"
                    emoji = "🟢"
                elif conf == "low":
                    badge_color = "orange"
                    emoji = "🟠"
                else:
                    badge_color = "red"
                    emoji = "🔴"

                with st.expander(
                    f"{emoji} Anomaly {idx + 1}: "
                    f"**{report['primary_anomaly']}**  "
                    f"`{report['anomaly_type']}`  —  "
                    f":{badge_color}[{report['confidence_score']}]",
                    expanded=(idx == 0),
                ):
                    with st.container(border=True):
                        col1, col2 = st.columns([1, 1])
                        with col1:
                            st.markdown("**⏱ Timestamp Range**")
                            st.code(
                                report.get("timestamp_range", "N/A"),
                                language=None,
                            )
                            st.markdown("**🔍 Root Cause Analysis**")
                            st.info(
                                html.escape(
                                    report.get("root_cause_analysis", "N/A")
                                )
                            )
                        with col2:
                            st.markdown("**🛠 Actionable Fix**")
                            st.success(
                                html.escape(
                                    report.get("actionable_fix", "N/A")
                                )
                            )

                        st.markdown("**📊 Cited Evidence**")
                        evidence = report.get("cited_evidence", [])
                        if evidence:
                            for ev in evidence:
                                st.markdown(f"- `{html.escape(str(ev))}`")
                        else:
                            st.caption("No evidence citations provided.")

        except Exception as e:
            # Surface any unhandled extraction / synthesis error in the UI.
            # Redact the API key from the error message if it appears.
            err_msg = str(e)
            if api_key and api_key in err_msg:
                err_msg = err_msg.replace(api_key, "***REDACTED***")
            st.error(f"Analysis Failed: {err_msg}")
        finally:
            # Clean up the temp file — always runs, even on error.
            # The inner try/except prevents a Windows file-lock collision
            # from masking the real application exception above.
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

else:
    # -------------------------------------------------------------------
    # IDLE STATE — show an instructive placeholder
    # -------------------------------------------------------------------
    with st.container(border=True):
        st.markdown("### 🛰️ Welcome to the Hardware & Telemetry Copilot")
        st.markdown(
            "Upload an ArduPilot telemetry log (`.bin`, `.tlog`, or `.csv`), "
            "select your hardware in the sidebar, and click **Run Diagnostics** "
            "to start the automated fault analysis pipeline."
        )
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Supported Formats", "3", help=".bin, .tlog, .csv")
        with col2:
            st.metric("Component Profiles", "23")
        with col3:
            st.metric("Anomaly Detectors", "9 categories")

    with st.expander("📖 Supported formats & columns"):
        st.markdown("""
**Accepted file types:** `.bin` (Dataflash), `.tlog` (Telemetry), `.rlog` (Telemetry), `.csv`

The ingestion layer maps these native MAVLink message frames to our
internal schema:

| MAVLink Message | Columns Extracted |
|---|---|
| `BAT` | `BAT.Volt`, `BAT.Curr` |
| `POWR` | `VCC` |
| `VIBE` | `VIBE.VibeX`, `VIBE.VibeY`, `VIBE.VibeZ`, `VIBE.Clip0/1/2` |
| `ATT` | `ATT.DesRoll`, `ATT.Roll`, `ATT.DesPitch`, `ATT.Pitch` |
| `RCOU` | `RCOU.C1` – `RCOU.C14` |

The parser merges these independent message streams onto a unified
timeline via forward-fill before running anomaly detection.
        """)

    with st.expander("🔧 Active thresholds (current profile)"):
        try:
            profile = build_profile(
                fc=selected_fc,
                power=selected_power,
                propulsion=selected_propulsion,
            )
            thr = profile["thresholds"]
            pwr = profile["power_system"]
            air = profile["airframe"]

            st.json({
                "flight_controller": profile["flight_controller"]["model"],
                "battery": f"{pwr['battery']['cell_count']}S {pwr['battery']['chemistry']}",
                "airframe": f"{air['type']} ({air['motor_count']} motors)",
                "vcc_range": f"{pwr['vcc']['min_v']}–{pwr['vcc']['max_v']} V",
                "pwm_redline": f">{thr['pwm']['redline']}",
                "vibration_z_max": f"{thr['vibration']['z_max_m_s2']} m/s²",
                "imbalance": (
                    f"high >{thr['motor_imbalance']['high_threshold_pwm']}  |  "
                    f"low <{thr['motor_imbalance']['low_threshold_pwm']}"
                ),
                "voltage_sag": (
                    f"V < median × {thr['voltage_sag']['volt_drop_ratio']}  &  "
                    f"A > median × {thr['voltage_sag']['current_spike_ratio']}"
                ),
            })
        except Exception:
            st.caption("Could not load profile — check component files.")
