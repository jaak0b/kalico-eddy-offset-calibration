"""Unit tests for the fleet run's pure surfaces.

Every expected value here is a literal written out by hand, so the tests
judge the code rather than restate it.
"""

import pytest

import eddy_tool_calibration as etc


# --- sweep order -----------------------------------------------------------


def test_a_four_tool_machine_sweeps_t0_through_t3_in_order():
    assert etc.sweep_tool_order(4) == [0, 1, 2, 3]


def test_a_single_tool_machine_sweeps_only_the_baseline_tool():
    assert etc.sweep_tool_order(1) == [0]


def test_a_sweep_without_a_tool_count_is_rejected():
    with pytest.raises(ValueError, match="tool count is not set"):
        etc.sweep_tool_order(None)


def test_a_sweep_of_no_tools_is_rejected():
    with pytest.raises(ValueError, match="at least 1"):
        etc.sweep_tool_order(0)


# --- apply template context ------------------------------------------------


def test_the_apply_context_carries_the_tool_and_both_planar_offsets():
    context = etc.offset_template_context(
        2, {'x': -0.0431, 'y': 0.0122, 'z': None}, False)

    assert context == {'tool': 2, 'offset_x': -0.0431, 'offset_y': 0.0122}


def test_the_apply_context_omits_offset_z_without_z_calibration():
    # A Z offset that was never measured must not reach the applying lines as
    # a zero, so the name is absent rather than present and harmless looking.
    context = etc.offset_template_context(
        1, {'x': 0.1, 'y': 0.2, 'z': None}, False)

    assert 'offset_z' not in context


def test_the_apply_context_carries_offset_z_with_z_calibration():
    context = etc.offset_template_context(
        3, {'x': 0.1, 'y': -0.2, 'z': 0.0157}, True)

    assert context == {'tool': 3, 'offset_x': 0.1, 'offset_y': -0.2,
                       'offset_z': 0.0157}


def test_an_unmeasured_z_offset_under_z_calibration_is_rejected():
    with pytest.raises(ValueError, match="T1 was not measured"):
        etc.offset_template_context(1, {'x': 0.1, 'y': 0.2, 'z': None}, True)


# --- fleet summary ---------------------------------------------------------


def test_the_summary_names_the_baseline_tool_and_every_measured_offset():
    entries = [
        {'tool': 0, 'offsets': None},
        {'tool': 1, 'offsets': {'x': -0.0431, 'y': 0.0122, 'z': 0.0157}},
    ]

    assert etc.fleet_summary_rows(entries) == [
        "fleet summary:",
        "T0: baseline tool, offsets zero by definition",
        "T1: offset x: -0.0431, offset y: +0.0122, offset z: +0.0157",
    ]


def test_the_summary_leaves_out_z_when_no_descent_ran():
    entries = [
        {'tool': 0, 'offsets': None},
        {'tool': 2, 'offsets': {'x': 1.5, 'y': -2.25, 'z': None}},
    ]

    assert etc.fleet_summary_rows(entries) == [
        "fleet summary:",
        "T0: baseline tool, offsets zero by definition",
        "T2: offset x: +1.5000, offset y: -2.2500",
    ]


def test_an_empty_fleet_summary_is_rejected():
    with pytest.raises(ValueError, match="at least one measured tool"):
        etc.fleet_summary_rows([])
