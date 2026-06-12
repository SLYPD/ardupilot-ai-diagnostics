"""Tests for parser — anomaly detection with profile-driven thresholds."""

import os
import sys
import pytest

# Ensure the project root is on sys.path so we can import the parser module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parser import extract_anomalies


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_csv(text: str, tmp_path) -> str:
    """Write *text* to a .csv file inside *tmp_path* and return its path."""
    csv_path = os.path.join(str(tmp_path), "test_log.csv")
    with open(csv_path, "w") as f:
        f.write(text)
    return csv_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestNoAnomalies:
    def test_clean_data_returns_empty(self, tmp_path):
        csv_text = (
            "TimeS,BAT.Volt,BAT.Curr,VCC,"
            "VIBE.VibeX,VIBE.VibeY,VIBE.VibeZ,"
            "VIBE.Clip0,ATT.DesRoll,ATT.Roll,ATT.DesPitch,ATT.Pitch,"
            "RCOU.C1,RCOU.C2,RCOU.C3,RCOU.C4,RCOU.C5,RCOU.C6\n"
        )
        for i in range(50):
            csv_text += f"{i*0.1:.1f},22.5,15.0,5.05,5.0,4.0,8.0,0,0.0,0.2,0.0,-0.1,1550,1540,1560,1530,1550,1540\n"

        path = _make_csv(csv_text, tmp_path)
        results = extract_anomalies(path)
        assert results == []


class TestVCC:
    def test_vcc_drop_detected(self, tmp_path):
        rows = ["TimeS,BAT.Volt,BAT.Curr,VCC,RCOU.C1"]
        for i in range(30):
            rows.append(f"{i*0.1:.1f},22.5,15.0,5.05,1550")
        rows[15] = "1.5,22.5,15.0,4.5,1550"  # below 4.9 min
        path = _make_csv("\n".join(rows), tmp_path)
        results = extract_anomalies(path)
        labels = [r["type"] for r in results]
        assert "VCC_DROP" in labels

    def test_vcc_over_volt_detected(self, tmp_path):
        rows = ["TimeS,BAT.Volt,BAT.Curr,VCC,RCOU.C1"]
        for i in range(30):
            rows.append(f"{i*0.1:.1f},22.5,15.0,5.05,1550")
        rows[10] = "1.0,22.5,15.0,5.5,1550"  # above 5.3 max
        path = _make_csv("\n".join(rows), tmp_path)
        results = extract_anomalies(path)
        labels = [r["type"] for r in results]
        assert "VCC_OVER_VOLT" in labels


class TestMaxThrust:
    def test_max_thrust_at_redline(self, tmp_path):
        """Motor at exactly the redline (1940) should trigger with >= check."""
        rows = ["TimeS,BAT.Volt,BAT.Curr,VCC,RCOU.C1,RCOU.C2"]
        for i in range(30):
            rows.append(f"{i*0.1:.1f},22.5,15.0,5.05,1550,1540")
        rows[10] = "1.0,22.5,15.0,5.05,1940,1540"  # exactly redline
        path = _make_csv("\n".join(rows), tmp_path)
        results = extract_anomalies(path)
        labels = [r["type"] for r in results]
        assert any("MAX_THRUST" in l for l in labels)

    def test_max_thrust_below_redline_no_trigger(self, tmp_path):
        rows = ["TimeS,BAT.Volt,BAT.Curr,VCC,RCOU.C1"]
        for i in range(30):
            # All at 1550 (well below redline of 1940)
            rows.append(f"{i*0.1:.1f},22.5,15.0,5.05,1550")
        path = _make_csv("\n".join(rows), tmp_path)
        results = extract_anomalies(path)
        labels = [r["type"] for r in results]
        assert not any("MAX_THRUST" in l for l in labels)


class TestVoltageSag:
    def test_voltage_sag_rolling_median(self, tmp_path):
        """A sharp voltage drop with current spike triggers VOLTAGE_SAG."""
        rows = ["TimeS,BAT.Volt,BAT.Curr,VCC,RCOU.C1"]
        for i in range(30):
            rows.append(f"{i*0.1:.1f},22.5,15.0,5.05,1550")
        # Sharp sag: voltage drops to 80% of recent (rolling) median
        rows[20] = "2.0,18.0,40.0,5.05,1550"  # far below 22.5 normal
        rows[21] = "2.1,18.5,45.0,5.05,1550"
        path = _make_csv("\n".join(rows), tmp_path)
        results = extract_anomalies(path)
        labels = [r["type"] for r in results]
        assert "VOLTAGE_SAG" in labels

    def test_sag_below_absolute_minimum(self, tmp_path):
        """Voltage below min_safe_voltage_v (19.8V for 6S) triggers sag."""
        rows = ["TimeS,BAT.Volt,BAT.Curr,VCC,RCOU.C1"]
        for i in range(30):
            rows.append(f"{i*0.1:.1f},22.5,15.0,5.05,1550")
        # Below 19.8V absolute minimum
        rows[15] = "1.5,19.0,15.0,5.05,1550"  # below 19.8 min safe, curr normal
        path = _make_csv("\n".join(rows), tmp_path)
        results = extract_anomalies(path)
        labels = [r["type"] for r in results]
        assert "VOLTAGE_SAG" in labels


class TestMotorImbalance:
    def test_imbalance_detected(self, tmp_path):
        rows = ["TimeS,BAT.Volt,BAT.Curr,VCC,RCOU.C1,RCOU.C4"]
        for i in range(30):
            rows.append(f"{i*0.1:.1f},22.5,15.0,5.05,1550,1550")
        # C1 high (above 1700), C4 low (below 1200) — sustained 3+ rows
        for r in (10, 11, 12):
            rows[r] = f"{r*0.1:.1f},22.5,15.0,5.05,1750,1100"
        path = _make_csv("\n".join(rows), tmp_path)
        results = extract_anomalies(path)
        labels = [r["type"] for r in results]
        assert any("IMBALANCE" in l for l in labels)

    def test_single_row_glitch_no_trigger(self, tmp_path):
        """A single-row divergence should NOT trigger with persistence filter."""
        rows = ["TimeS,BAT.Volt,BAT.Curr,VCC,RCOU.C1,RCOU.C4"]
        for i in range(30):
            rows.append(f"{i*0.1:.1f},22.5,15.0,5.05,1550,1550")
        # Only 1 row of divergence — should be filtered
        rows[10] = "1.0,22.5,15.0,5.05,1750,1100"
        path = _make_csv("\n".join(rows), tmp_path)
        results = extract_anomalies(path)
        labels = [r["type"] for r in results]
        assert not any("IMBALANCE" in l for l in labels)


class TestVibration:
    def test_vibe_z_spike_detected(self, tmp_path):
        rows = ["TimeS,BAT.Volt,BAT.Curr,VCC,VIBE.VibeZ,RCOU.C1"]
        for i in range(30):
            rows.append(f"{i*0.1:.1f},22.5,15.0,5.05,8.0,1550")
        rows[15] = "1.5,22.5,15.0,5.05,50.0,1550"  # above 45 Z max
        path = _make_csv("\n".join(rows), tmp_path)
        results = extract_anomalies(path)
        labels = [r["type"] for r in results]
        assert "VIBE_Z_SPIKE" in labels

    def test_vibe_xy_spike_detected(self, tmp_path):
        rows = ["TimeS,BAT.Volt,BAT.Curr,VCC,VIBE.VibeX,RCOU.C1"]
        for i in range(30):
            rows.append(f"{i*0.1:.1f},22.5,15.0,5.05,5.0,1550")
        rows[15] = "1.5,22.5,15.0,5.05,35.0,1550"  # above 30 XY max
        path = _make_csv("\n".join(rows), tmp_path)
        results = extract_anomalies(path)
        labels = [r["type"] for r in results]
        assert "VIBE_X_SPIKE" in labels


class TestIMUClipping:
    def test_imu_clip_detected(self, tmp_path):
        rows = [
            "TimeS,BAT.Volt,BAT.Curr,VCC,VIBE.Clip0,VIBE.Clip1,RCOU.C1"
        ]
        for i in range(30):
            rows.append(f"{i*0.1:.1f},22.5,15.0,5.05,0,0,1550")
        rows[10] = "1.0,22.5,15.0,5.05,5,0,1550"  # Clip0 > 0
        path = _make_csv("\n".join(rows), tmp_path)
        results = extract_anomalies(path)
        labels = [r["type"] for r in results]
        assert any("IMU_CLIP" in l for l in labels)


class TestATTDesync:
    def test_att_desync_roll_detected(self, tmp_path):
        rows = [
            "TimeS,BAT.Volt,BAT.Curr,VCC,"
            "ATT.DesRoll,ATT.Roll,ATT.DesPitch,ATT.Pitch,RCOU.C1"
        ]
        for i in range(30):
            rows.append(f"{i*0.1:.1f},22.5,15.0,5.05,0.0,0.2,0.0,-0.1,1550")
        # 20° divergence (above 15° default threshold)
        rows[15] = "1.5,22.5,15.0,5.05,5.0,25.0,0.0,-0.1,1550"
        path = _make_csv("\n".join(rows), tmp_path)
        results = extract_anomalies(path)
        labels = [r["type"] for r in results]
        assert "ATT_DESYNC_ROLL" in labels


class TestVCCFluctuation:
    def test_vcc_fluctuation_detected(self, tmp_path):
        rows = ["TimeS,BAT.Volt,BAT.Curr,VCC,RCOU.C1"]
        for i in range(30):
            rows.append(f"{i*0.1:.1f},22.5,15.0,5.05,1550")
        # Oscillating VCC creates >0.15V swing in 5-row window
        for j, vcc in enumerate([5.05, 4.95, 5.1, 4.9, 5.15]):
            rows[10 + j] = f"{1.0+j*0.1:.1f},22.5,15.0,{vcc},1550"
        path = _make_csv("\n".join(rows), tmp_path)
        results = extract_anomalies(path)
        labels = [r["type"] for r in results]
        assert "VCC_FLUCTUATION" in labels


class TestMinThrust:
    def test_min_thrust_detected(self, tmp_path):
        rows = ["TimeS,BAT.Volt,BAT.Curr,VCC,RCOU.C1,RCOU.C2"]
        for i in range(30):
            rows.append(f"{i*0.1:.1f},22.5,15.0,5.05,1550,1540")
        # Motor C1 at exactly min (1000)
        rows[10] = "1.0,22.5,15.0,5.05,1000,1540"
        path = _make_csv("\n".join(rows), tmp_path)
        results = extract_anomalies(path)
        labels = [r["type"] for r in results]
        assert any("MIN_THRUST" in l for l in labels)


class TestInputValidation:
    def test_empty_csv_rejected(self, tmp_path):
        path = _make_csv("", tmp_path)
        with pytest.raises(ValueError, match="empty"):
            extract_anomalies(path)

    def test_missing_columns_handled_gracefully(self, tmp_path):
        """A CSV with only TimeS should not crash — it produces zero anomalies."""
        path = _make_csv("TimeS\n0.0\n0.1\n0.2\n", tmp_path)
        results = extract_anomalies(path)
        assert results == []


class TestContextWindows:
    def test_context_window_has_correct_structure(self, tmp_path):
        rows = ["TimeS,BAT.Volt,BAT.Curr,VCC,VIBE.VibeZ,RCOU.C1"]
        for i in range(50):
            rows.append(f"{i*0.1:.1f},22.5,15.0,5.05,8.0,1550")
        rows[25] = "2.5,22.5,15.0,5.05,50.0,1550"  # VIBE_Z_SPIKE at row 25
        path = _make_csv("\n".join(rows), tmp_path)
        results = extract_anomalies(path)
        assert len(results) >= 1
        for r in results:
            assert "type" in r
            assert "timestamp" in r
            assert "data" in r
            assert isinstance(r["data"], str)
            assert len(r["data"]) > 0
