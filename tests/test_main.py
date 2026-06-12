"""Tests for main — parallel diagnostic utility."""

import os
import sys
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import diagnose_anomalies_parallel, _diagnose_one


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_anomaly(atype: str = "VOLTAGE_SAG", timestamp: int = 42) -> dict:
    return {
        "type": atype,
        "timestamp": timestamp,
        "data": "TimeS,BAT.Volt\n1.0,14.5\n",
    }

VALID_REPORT = {
    "timestamp_range": "0s — 5s",
    "primary_anomaly": "VOLTAGE_SAG",
    "cited_evidence": ["1.0 BAT.Volt=14.5"],
    "root_cause_analysis": "Battery dip during punch-out.",
    "actionable_fix": "Check battery IR.",
    "confidence_score": "High",
    "anomaly_type": "VOLTAGE_SAG",
}

ERROR_REPORT = {
    "timestamp_range": "?",
    "primary_anomaly": "VOLTAGE_SAG",
    "cited_evidence": [],
    "root_cause_analysis": "API ERROR after retries.",
    "actionable_fix": "Retry the diagnostic.",
    "confidence_score": "Inconclusive",
    "anomaly_type": "VOLTAGE_SAG",
}


# ---------------------------------------------------------------------------
# _diagnose_one
# ---------------------------------------------------------------------------

class TestDiagnoseOne:
    def test_returns_valid_report_on_success(self):
        with mock.patch("main.call_diagnostic_api") as mock_call:
            mock_call.return_value = '{"confidence_score":"High"}'
            with mock.patch("main.validate_diagnostic_report") as mock_validate:
                mock_validate.return_value = VALID_REPORT
                result = _diagnose_one(_make_anomaly(), "prompt", api_key="sk-test")
                assert result["anomaly_type"] == "VOLTAGE_SAG"

    def test_retries_on_failure(self):
        with mock.patch("main.call_diagnostic_api") as mock_call:
            mock_call.side_effect = [
                Exception("timeout"),
                Exception("timeout"),
                '{"confidence_score":"Low"}',
            ]
            with mock.patch("main.validate_diagnostic_report") as mock_validate:
                mock_validate.return_value = {
                    **VALID_REPORT, "confidence_score": "Low",
                }
                result = _diagnose_one(_make_anomaly(), "prompt", api_key="sk-test")
                assert result["confidence_score"] == "Low"
                assert mock_call.call_count == 3

    def test_returns_error_placeholder_after_all_retries(self):
        with mock.patch("main.call_diagnostic_api") as mock_call:
            mock_call.side_effect = Exception("timeout")
            result = _diagnose_one(_make_anomaly(), "prompt", api_key="sk-test")
            assert result["confidence_score"] == "Inconclusive"
            assert "API ERROR" in result["root_cause_analysis"]
            assert mock_call.call_count == 3


# ---------------------------------------------------------------------------
# diagnose_anomalies_parallel
# ---------------------------------------------------------------------------

class TestDiagnoseAnomaliesParallel:
    def test_empty_list_returns_empty(self):
        result = diagnose_anomalies_parallel([], "prompt")
        assert result == []

    def test_result_count_matches_input(self):
        anomalies = [_make_anomaly(f"TEST_{i}") for i in range(5)]
        with mock.patch("main.call_diagnostic_api") as mock_call:
            mock_call.return_value = '{"confidence_score":"High"}'
            with mock.patch("main.validate_diagnostic_report") as mock_validate:
                mock_validate.return_value = VALID_REPORT
                result = diagnose_anomalies_parallel(anomalies, "prompt")
                assert len(result) == 5

    def test_results_preserve_input_order(self):
        anomalies = [
            _make_anomaly("FIRST"),
            _make_anomaly("SECOND"),
            _make_anomaly("THIRD"),
        ]
        with mock.patch("main.call_diagnostic_api") as mock_call:
            mock_call.return_value = '{"confidence_score":"High"}'
            with mock.patch("main.validate_diagnostic_report") as mock_validate:
                def _side_effect(raw, label):
                    return {**VALID_REPORT, "anomaly_type": label, "primary_anomaly": label}
                mock_validate.side_effect = _side_effect
                result = diagnose_anomalies_parallel(anomalies, "prompt")
                types = [r["anomaly_type"] for r in result]
                assert types == ["FIRST", "SECOND", "THIRD"]

    def test_one_failure_does_not_crash_batch(self):
        # Anomaly #2 will always fail (its _diagnose_one worker exhausts
        # all 3 retries), but the other two will succeed.
        anomalies = [
            _make_anomaly("TEST_0"),
            _make_anomaly("TEST_FAIL"),
            _make_anomaly("TEST_2"),
        ]

        def _flaky_call(prompt, anomaly, api_key=None):
            if anomaly.get("type") == "TEST_FAIL":
                raise Exception("simulated persistent API failure")
            return '{"confidence_score":"High"}'

        with mock.patch("main.call_diagnostic_api") as mock_call:
            mock_call.side_effect = _flaky_call
            with mock.patch("main.validate_diagnostic_report") as mock_validate:
                mock_validate.return_value = VALID_REPORT
                result = diagnose_anomalies_parallel(anomalies, "prompt")
                assert len(result) == 3
                errors = [
                    r for r in result
                    if r.get("confidence_score") == "Inconclusive"
                ]
                assert len(errors) == 1
                assert errors[0]["anomaly_type"] == "TEST_FAIL"

    def test_single_anomaly_works(self):
        anomalies = [_make_anomaly()]
        with mock.patch("main.call_diagnostic_api") as mock_call:
            mock_call.return_value = '{"confidence_score":"High"}'
            with mock.patch("main.validate_diagnostic_report") as mock_validate:
                mock_validate.return_value = VALID_REPORT
                result = diagnose_anomalies_parallel(anomalies, "prompt")
                assert len(result) == 1
                assert result[0]["anomaly_type"] == "VOLTAGE_SAG"
