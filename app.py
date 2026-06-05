"""
Streamlit UI — Hardware & Telemetry Copilot
============================================
Drag-and-drop ArduPilot telemetry CSVs, select hardware components,
and receive structured DeepSeek diagnostic reports.
"""
import os
import time
import uuid
from pathlib import Path
import streamlit as st

# -- backend imports (from the existing pipeline) --
from template_builder import (
    build_profile,
    list_all_components,
    DEFAULT_FC,
    DEFAULT_POWER,
    DEFAULT_PROPULSION,
)
from parser import extract_anomalies
from main import (
    format_profile_for_llm,
    build_system_prompt,
    call_diagnostic_api,
    validate_diagnostic_report,
)

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
    /* Dark sidebar */
    [data-testid="stSidebar"] {
        background-color: #0e1117;
    }
    /* Monospace for telemetry data */
    .stExpander pre, .telemetry-table {
        font-family: 'Cascadia Code', 'Fira Code', 'Consolas', monospace;
    }
    /* Confidence badges */
    .confidence-high   { color: #00c853; font-weight: 700; }
    .confidence-low    { color: #ff9100; font-weight: 700; }
    .confidence-inconclusive { color: #ff5252; font-weight: 700; }
    /* Report card evidence list */
    .evidence-list { padding-left: 1.2rem; }
    .evidence-list li { margin-bottom: 0.3rem; }
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

# Default index for each dropdown
fc_idx = fc_options.index(DEFAULT_FC) if DEFAULT_FC in fc_options else 0
power_idx = power_options.index(DEFAULT_POWER) if DEFAULT_POWER in power_options else 0
prop_idx = propulsion_options.index(DEFAULT_PROPULSION) if DEFAULT_PROPULSION in propulsion_options else 0

selected_fc         = st.sidebar.selectbox("Flight Controller", fc_options, index=fc_idx)
selected_power      = st.sidebar.selectbox("Power System", power_options, index=power_idx)
selected_propulsion = st.sidebar.selectbox("Propulsion", propulsion_options, index=prop_idx)

# Divider + component metadata
st.sidebar.markdown("---")
st.sidebar.caption(f"**Profile:** {selected_fc}__{selected_power}__{selected_propulsion}")

# API key input
st.sidebar.markdown("---")
api_key = st.sidebar.text_input(
    "DeepSeek API Key",
    type="password",
    placeholder="sk-...",
    help="Your key is never stored — it is only used for this session.",
)
if not api_key:
    st.sidebar.caption("Enter an API key to enable LLM diagnostics.")
else:
    st.sidebar.caption("Key ready — LLM diagnostics enabled.")

st.sidebar.markdown("---")
st.sidebar.markdown(
    "[📖 Project docs](https://github.com)"
)

# ---------------------------------------------------------------------------
# MAIN AREA — title + upload
# ---------------------------------------------------------------------------
st.title("🛰️ Hardware & Telemetry Copilot")
st.caption("Post-mortem diagnostic engine for ArduPilot / MAVLink telemetry logs — native .bin, .tlog, and .csv support.")

st.markdown("---")

uploaded_file = st.file_uploader(
    "📤 Upload a telemetry log",
    type=["csv", "bin", "tlog"],
    help="Accepts ArduPilot .bin (Dataflash), .tlog (Telemetry), or .csv logs.",
)

# ---------------------------------------------------------------------------
# RUN DIAGNOSTICS — the labor illusion
# ---------------------------------------------------------------------------
if st.button("▶ Run Diagnostics", type="primary", disabled=(uploaded_file is None)):
    if uploaded_file is None:
        st.warning("Please upload a telemetry log file first.")
    elif not api_key:
        st.warning("Please enter your DeepSeek API key in the sidebar.")
    else:
        # Write uploaded bytes to a uniquely-named temp file.
        # Standard open() avoids tempfile.NamedTemporaryFile's OS-level
        # locking behaviour on Windows — the handle is released as soon
        # as the with-block exits.
        orig_suffix = Path(uploaded_file.name).suffix or ".csv"
        tmp_path = f"temp_flight_{uuid.uuid4().hex}{orig_suffix}"
        with open(tmp_path, "wb") as f:
            f.write(uploaded_file.getbuffer())
        # ^^ file handle closed; tmp_path is on disk and unlocked

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
                time.sleep(1.5)

                # --- Phase 2: Scan ---
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
                        ".tlog, or .csv file."
                    )
                    st.stop()

                if not anomalies:
                    st.write("   ✅ No anomalies detected — hardware "
                             "health nominal.")
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
                    st.write("🤖 **Synthesizing DeepSeek report…**")
                    time.sleep(0.3)

                    reports = []
                    system_prompt = build_system_prompt(hw_text)

                    for idx, anomaly in enumerate(anomalies):
                        label = anomaly["type"]
                        st.write(
                            f"   ↳ [{idx + 1}/{len(anomalies)}] "
                            f"Analyzing `{label}`…"
                        )

                        try:
                            raw = call_diagnostic_api(
                                system_prompt, anomaly, api_key=api_key
                            )
                            report = validate_diagnostic_report(
                                raw, label
                            )
                            reports.append(report)
                            st.write(
                                f"      ✅ Complete  (confidence: "
                                f"{report.get('confidence_score','?')})"
                            )
                        except Exception as exc:
                            reports.append({
                                "timestamp_range": "?",
                                "primary_anomaly": label,
                                "cited_evidence": [],
                                "root_cause_analysis": (
                                    f"API ERROR: {exc}"
                                ),
                                "actionable_fix": (
                                    "Retry the diagnostic."
                                ),
                                "confidence_score": "Inconclusive",
                                "anomaly_type": label,
                            })
                            st.write(
                                f"      ❌ API call failed: {exc}"
                            )

                        if idx < len(anomalies) - 1:
                            time.sleep(1.0)

                    status_pipe.update(
                        label="Diagnostic Pipeline — Complete",
                        state="complete",
                    )

            # ===========================================================
            # OUTPUT — render reports below the completed status block
            # ===========================================================
            st.markdown("---")
            st.subheader(
                f"📋 Diagnostic Reports  ({len(reports)} found)"
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
                    col1, col2 = st.columns([1, 1])
                    with col1:
                        st.markdown("**⏱ Timestamp Range**")
                        st.code(
                            report.get("timestamp_range", "N/A"),
                            language=None,
                        )
                        st.markdown("**🔍 Root Cause Analysis**")
                        st.info(
                            report.get("root_cause_analysis", "N/A")
                        )
                    with col2:
                        st.markdown("**🛠 Actionable Fix**")
                        st.success(
                            report.get("actionable_fix", "N/A")
                        )

                    st.markdown("**📊 Cited Evidence**")
                    evidence = report.get("cited_evidence", [])
                    if evidence:
                        for ev in evidence:
                            st.markdown(f"- `{ev}`")
                    else:
                        st.caption("No evidence citations provided.")

        except Exception as e:
            # Surface any unhandled extraction / synthesis error in the UI
            st.error(f"Analysis Failed: {e}")
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
    st.info(
        "👆 Upload an ArduPilot telemetry log (.bin, .tlog, or .csv) and "
        "select your hardware configuration in the sidebar, then click "
        "**Run Diagnostics**."
    )

    with st.expander("📖 Supported formats & columns"):
        st.markdown("""
**Accepted file types:** `.bin` (Dataflash), `.tlog` (Telemetry), `.csv`

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
