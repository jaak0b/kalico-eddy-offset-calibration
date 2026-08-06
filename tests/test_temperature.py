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


# --- anchored setpoint against the configured one --------------------------


def test_an_anchor_taken_at_the_configured_setpoint_does_not_warn():
    # Both values are setpoints, so a calibration_temp nobody changed since
    # the tool was anchored matches the anchored one exactly.
    assert etc.temperature_warning(1, 150.0, 150.0) is None


def test_a_setpoint_the_owner_moved_by_half_a_degree_warns():
    # The old margin absorbed a change this size, which hid a config change
    # the run does not follow.
    warning = etc.temperature_warning(1, 150.0, 150.5)

    assert ("the Z reference for T1 was measured with calibration_temp "
            "at 150.0 C" in warning)


def test_the_warning_names_the_anchored_setpoint_and_the_configured_one():
    warning = etc.temperature_warning(2, 220.0, 150.0)

    assert ("the Z reference for T2 was measured with calibration_temp "
            "at 220.0 C" in warning)
    assert "calibration_temp is now 150.0 C" in warning


def test_the_warning_says_the_run_heats_to_the_anchored_setpoint():
    # The run reproduces the anchor's thermal state, so the warning must not
    # read as though the configured setpoint were about to be used.
    warning = etc.temperature_warning(2, 220.0, 150.0)

    assert "This run heats T2 to 220.0 C" in warning
    assert "Run EDDY_CALIBRATE_Z T=2" in warning


def test_a_setpoint_below_the_configured_one_warns_as_well():
    warning = etc.temperature_warning(0, 100.0, 150.0)

    assert ("the Z reference for T0 was measured with calibration_temp "
            "at 100.0 C" in warning)


# --- the band a preheat waits for ------------------------------------------


def test_a_nozzle_above_its_setpoint_but_inside_the_band_counts_as_there():
    # 151.8 C sits 1.8 C above a 150.0 C setpoint, inside a 2.0 C band, which
    # is the reading a hotend under PID control drifts to on its own.
    assert etc.temperature_in_band(151.8, 150.0, 2.0) is True


def test_a_nozzle_below_its_setpoint_but_inside_the_band_counts_as_there():
    assert etc.temperature_in_band(148.4, 150.0, 2.0) is True


def test_a_nozzle_exactly_on_the_edge_of_the_band_counts_as_there():
    assert etc.temperature_in_band(152.0, 150.0, 2.0) is True


def test_a_nozzle_just_outside_the_band_is_waited_for():
    assert etc.temperature_in_band(152.1, 150.0, 2.0) is False


def test_a_cold_nozzle_is_waited_for():
    assert etc.temperature_in_band(24.6, 150.0, 2.0) is False


def test_a_narrower_band_holds_a_reading_the_wider_one_accepted():
    # The same 151.8 C reading, judged against a 1.0 C band.
    assert etc.temperature_in_band(151.8, 150.0, 1.0) is False


# --- the rows a preheat prints before it waits ------------------------------


def test_the_preheat_plan_names_each_tool_its_heater_and_both_temperatures():
    rows = etc.preheat_plan_rows(
        [(0, 'extruder', 24.6, 150.0), (1, 'extruder1', 151.8, 150.0)],
        2.0, 30.0)

    assert rows == [
        "heating every listed tool before measuring:",
        "T0 heater extruder: 24.6 C now, 150.0 C target",
        "T1 heater extruder1: 151.8 C now, 150.0 C target",
        "temperature band: 2.0 C either side of the target temperature",
        "settle time after reaching the band: 30.0 s",
        "The command waits for every listed tool to read inside its band, "
        "including a tool that has to cool into it.",
    ]


def test_a_preheat_plan_covering_no_tools_is_rejected():
    with pytest.raises(ValueError, match="at least one tool"):
        etc.preheat_plan_rows([], 2.0, 30.0)


# --- the rows a measurement shows its temperatures as ----------------------


def test_a_setpoint_is_shown_to_one_decimal_under_the_label_it_is_given():
    assert etc.setpoint_temperature_row('anchor', 150.0) == (
        "anchor temperature setpoint: 150.0 C")


def test_a_target_is_shown_to_one_decimal_under_the_label_it_is_given():
    assert etc.target_temperature_row('nozzle', 150.0) == (
        "nozzle target temperature: 150.0 C")


def test_a_reading_is_shown_to_one_decimal_under_the_label_it_is_given():
    # Arrange / Act: 149.72 C rounds to 149.7 C at one decimal.
    row = etc.observed_temperature_row('nozzle', 149.72)

    assert row == "nozzle temperature observed: 149.7 C"
