"""Unit tests for the nozzle temperature surfaces.

Every expected value here is a literal written out by hand, so the tests
judge the code rather than restate it.
"""

import pytest

import eddy_tool_calibration as etc


# --- heater names ----------------------------------------------------------


def test_the_first_tool_falls_on_klippers_unnumbered_extruder_section():
    # Klipper names the first extruder section extruder, not extruder0.
    assert etc.tool_extruder_name(None, 0) == 'extruder'


def test_a_later_tool_falls_on_the_numbered_extruder_section():
    assert etc.tool_extruder_name(None, 3) == 'extruder3'


def test_a_configured_list_names_the_heater_of_each_tool_in_order():
    names = etc.parse_tool_extruders('extruder, extruder2, heater_hot', 3)

    assert etc.tool_extruder_name(names, 0) == 'extruder'
    assert etc.tool_extruder_name(names, 1) == 'extruder2'
    assert etc.tool_extruder_name(names, 2) == 'heater_hot'


def test_a_tool_beyond_a_configured_list_is_rejected():
    names = etc.parse_tool_extruders('extruder, extruder1', None)

    with pytest.raises(ValueError, match="T2 has none"):
        etc.tool_extruder_name(names, 2)


def test_an_unset_option_leaves_every_tool_on_the_klipper_naming():
    assert etc.parse_tool_extruders(None, 4) is None


def test_a_list_shorter_than_the_tool_count_is_rejected():
    with pytest.raises(ValueError, match="T2 has no heater"):
        etc.parse_tool_extruders('extruder, extruder1', 4)


def test_a_list_holding_an_empty_entry_is_rejected():
    with pytest.raises(ValueError, match="empty entry"):
        etc.parse_tool_extruders('extruder,, extruder2', None)


# --- anchored temperature against the configured one -----------------------


def test_a_recorded_temperature_matching_the_configured_one_does_not_warn():
    # A heater held at a 150.0 C target reads a fraction either side of it,
    # so 149.8 C is the same thermal state and not a mismatch.
    assert etc.temperature_warning(1, 149.8, 150.0) is None


def test_a_recorded_temperature_a_degree_off_does_not_warn():
    # Exactly at the margin, which counts as agreement.
    assert etc.temperature_warning(1, 151.0, 150.0) is None


def test_a_recorded_temperature_past_the_margin_names_both_values():
    warning = etc.temperature_warning(2, 220.0, 150.0)

    assert "T2 was anchored at 220.0 C" in warning
    assert "calibration_temp is 150.0 C" in warning


def test_the_warning_says_the_run_measures_at_the_recorded_temperature():
    # The run reproduces the anchor's thermal state, so the warning must not
    # read as though the configured temperature were about to be used.
    warning = etc.temperature_warning(2, 220.0, 150.0)

    assert "This run measures T2 at 220.0 C" in warning
    assert "Run EDDY_CALIBRATE_Z T=2" in warning


def test_a_recorded_temperature_below_the_configured_one_warns_as_well():
    warning = etc.temperature_warning(0, 100.0, 150.0)

    assert "T0 was anchored at 100.0 C" in warning
