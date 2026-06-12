"""Tests for template_builder — component loading, deep merge, and safety."""

import pytest
from template_builder import (
    build_profile,
    deep_merge,
    list_all_components,
    _validate_component_name,
    _load_json,
    DEFAULT_FC,
    DEFAULT_POWER,
    DEFAULT_PROPULSION,
)


class TestComponentNameValidation:
    def test_valid_names_pass(self):
        _validate_component_name("pixhawk_6c")
        _validate_component_name("6s_lipo")
        _validate_component_name("pwm_standard")
        _validate_component_name("dshot600")
        _validate_component_name("cube_orange")

    def test_path_traversal_rejected(self):
        with pytest.raises(ValueError):
            _validate_component_name("../../etc/passwd")
        with pytest.raises(ValueError):
            _validate_component_name("..\\windows\\system32")
        with pytest.raises(ValueError):
            _validate_component_name("../evil")

    def test_special_chars_rejected(self):
        with pytest.raises(ValueError):
            _validate_component_name("foo; rm -rf /")
        with pytest.raises(ValueError):
            _validate_component_name("foo|bar")
        with pytest.raises(ValueError):
            _validate_component_name("foo$bar")

    def test_empty_string_rejected(self):
        with pytest.raises(ValueError):
            _validate_component_name("")


class TestDeepMerge:
    def test_scalar_override(self):
        result = deep_merge({"a": 1, "b": 2}, {"a": 99})
        assert result == {"a": 99, "b": 2}

    def test_list_replacement(self):
        result = deep_merge({"items": [1, 2, 3]}, {"items": [4, 5]})
        assert result == {"items": [4, 5]}

    def test_nested_dict_merge(self):
        base = {"outer": {"inner_a": 1, "inner_b": 2}}
        override = {"outer": {"inner_a": 99, "inner_c": 3}}
        result = deep_merge(base, override)
        assert result == {"outer": {"inner_a": 99, "inner_b": 2, "inner_c": 3}}

    def test_override_adds_new_top_level_keys(self):
        result = deep_merge({"a": 1}, {"b": 2})
        assert result == {"a": 1, "b": 2}

    def test_base_keys_preserved_when_not_in_override(self):
        result = deep_merge({"only_in_base": [1, 2]}, {"new_key": "value"})
        assert result == {"only_in_base": [1, 2], "new_key": "value"}


class TestBuildProfile:
    def test_default_profile_loads(self):
        profile = build_profile()
        assert "profile" in profile
        assert "airframe" in profile
        assert "flight_controller" in profile
        assert "power_system" in profile
        assert "thresholds" in profile
        assert "context_window" in profile

    def test_profile_id_is_composite(self):
        profile = build_profile(fc="cube_orange", power="12s_lipo",
                                propulsion="dshot600")
        assert profile["profile"]["profile_id"] == "cube_orange__12s_lipo__dshot600"

    def test_custom_components_load(self):
        profile = build_profile(fc="cube_orange", power="4s_lipo",
                                propulsion="dshot600")
        assert profile["flight_controller"]["model"] == "Hex Cube Orange+"
        assert profile["power_system"]["battery"]["cell_count"] == 4
        assert profile["airframe"]["motor_count"] == 8

    def test_components_source_recorded(self):
        profile = build_profile(fc="cube_orange", power="6s_lipo",
                                propulsion="pwm_standard")
        comps = profile["profile"]["components"]
        assert comps["flight_controller"] == "cube_orange"
        assert comps["power_system"] == "6s_lipo"
        assert comps["propulsion"] == "pwm_standard"


class TestListAllComponents:
    def test_returns_three_categories(self):
        components = list_all_components()
        assert "fc" in components
        assert "power" in components
        assert "propulsion" in components

    def test_defaults_are_present(self):
        components = list_all_components()
        assert DEFAULT_FC in components["fc"]
        assert DEFAULT_POWER in components["power"]
        assert DEFAULT_PROPULSION in components["propulsion"]


class TestLoadJson:
    def test_loads_valid_component(self):
        data = _load_json("flight_controllers", "pixhawk_6c")
        assert data["flight_controller"]["model"] == "Holybro Pixhawk 6C Mini"

    def test_invalid_name_rejected(self):
        with pytest.raises(ValueError):
            _load_json("flight_controllers", "../../etc/passwd")

    def test_missing_file_raises_filenotfound(self):
        with pytest.raises(FileNotFoundError):
            _load_json("flight_controllers", "nonexistent_fc_xyz")
