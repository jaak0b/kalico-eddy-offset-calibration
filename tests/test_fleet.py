"""Unit tests for the fleet run's pure surfaces.

Every expected value here is a literal written out by hand, so the tests
judge the code rather than restate it.
"""

import pytest

import eddy_tool_calibration as etc


# --- fleet order -----------------------------------------------------------


def test_a_four_tool_machine_covers_t0_through_t3_in_order():
    assert etc.fleet_tool_order(4) == [0, 1, 2, 3]


def test_a_single_tool_machine_covers_only_the_baseline_tool():
    assert etc.fleet_tool_order(1) == [0]


def test_a_fleet_run_without_a_tool_count_is_rejected():
    with pytest.raises(ValueError, match="tool count is not set"):
        etc.fleet_tool_order(None)


def test_a_fleet_run_of_no_tools_is_rejected():
    with pytest.raises(ValueError, match="at least 1"):
        etc.fleet_tool_order(0)


# --- the T= tool list ------------------------------------------------------


def test_a_t_list_is_read_in_the_order_it_was_written():
    assert etc.parse_tool_list('0,2,1', 4, 16) == [0, 2, 1]


def test_a_single_t_value_reads_as_a_one_tool_list():
    assert etc.parse_tool_list('3', 4, 16) == [3]


def test_spaces_around_the_entries_of_a_t_list_are_allowed():
    assert etc.parse_tool_list(' 0 , 1 ', 4, 16) == [0, 1]


def test_an_omitted_t_reads_as_the_whole_fleet():
    # A run without T= covers every tool, which is the same list a full T=
    # would have named.
    assert etc.parse_tool_list(None, 3, 16) == [0, 1, 2]


def test_a_t_list_naming_a_tool_twice_is_rejected():
    with pytest.raises(ValueError, match="names T1 twice"):
        etc.parse_tool_list('0,1,1', 4, 16)


def test_a_t_list_beyond_the_configured_tool_count_is_rejected():
    with pytest.raises(ValueError, match="tool_count is 4"):
        etc.parse_tool_list('0,4', 4, 16)


def test_a_t_list_beyond_the_accepted_tools_is_rejected_without_a_tool_count():
    with pytest.raises(ValueError, match="T0 through T15"):
        etc.parse_tool_list('16', None, 16)


def test_a_negative_tool_number_is_rejected():
    with pytest.raises(ValueError, match="names T-1"):
        etc.parse_tool_list('-1', 4, 16)


def test_a_t_list_entry_that_is_not_a_number_is_rejected():
    with pytest.raises(ValueError, match="'x1'"):
        etc.parse_tool_list('0,x1', 4, 16)


def test_a_fractional_tool_number_is_rejected():
    with pytest.raises(ValueError, match="'1.5'"):
        etc.parse_tool_list('1.5', 4, 16)


def test_a_t_list_with_a_trailing_comma_is_rejected():
    with pytest.raises(ValueError, match="empty entry"):
        etc.parse_tool_list('0,1,', 4, 16)


def test_a_t_parameter_with_no_tool_number_is_rejected():
    with pytest.raises(ValueError, match="carries no tool number"):
        etc.parse_tool_list('  ', 4, 16)


# --- baseline ordering -----------------------------------------------------


def test_the_baseline_tool_is_moved_to_the_front_of_a_list():
    assert etc.baseline_first([2, 0, 1], 0) == [0, 2, 1]


def test_the_other_tools_keep_the_order_they_were_listed_in():
    assert etc.baseline_first([3, 1, 0, 2], 0) == [0, 3, 1, 2]


def test_a_list_without_the_baseline_tool_is_left_alone():
    assert etc.baseline_first([3, 1, 2], 0) == [3, 1, 2]


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
        "summary of all measured tools:",
        "T0: baseline tool, offsets zero by definition",
        "T1: offset x: -0.0431, offset y: +0.0122, offset z: +0.0157",
    ]


def test_the_summary_leaves_out_z_when_no_descent_ran():
    entries = [
        {'tool': 0, 'offsets': None},
        {'tool': 2, 'offsets': {'x': 1.5, 'y': -2.25, 'z': None}},
    ]

    assert etc.fleet_summary_rows(entries) == [
        "summary of all measured tools:",
        "T0: baseline tool, offsets zero by definition",
        "T2: offset x: +1.5000, offset y: -2.2500",
    ]


def test_an_empty_fleet_summary_is_rejected():
    with pytest.raises(ValueError, match="at least one measured tool"):
        etc.fleet_summary_rows([])


# --- the rows every readout shows a center and an offset as ----------------


def test_a_center_is_shown_to_four_decimals_under_the_label_it_is_given():
    # Arrange / Act: 12.3456789 rounds to 12.3457 at four decimals, and a
    # figure with fewer digits is padded out to four.
    rows = etc.center_rows(12.3456789, -4.2, 'coil center')

    assert rows == ["coil center x: 12.3457", "coil center y: -4.2000"]


def test_the_locate_readout_names_the_config_options_it_measured():
    # Arrange / Act: 101.20834 rounds to 101.2083 at four decimals, and a
    # figure with fewer digits is padded out to four.
    rows = etc.new_center_rows(101.20834, -18.7)

    assert rows == ["new coil_x: 101.2083", "new coil_y: -18.7000"]


def test_the_staged_center_carries_the_decimals_the_readout_prints():
    # Arrange / Act: the pairs SAVE_CONFIG writes, rounded to the four
    # decimals every center row shows.
    settings = etc.coil_center_settings(101.20834, -18.69591)

    assert settings == (('coil_x', "101.2083"), ('coil_y', "-18.6959"))


def test_an_offset_is_shown_signed_to_four_decimals():
    rows = etc.offset_rows({'x': 0.05, 'y': -0.12, 'z': 0.0157})

    assert rows == [
        "offset x: +0.0500",
        "offset y: -0.1200",
        "offset z: +0.0157",
    ]


def test_an_unmeasured_z_offset_leaves_its_row_out():
    rows = etc.offset_rows({'x': 0.05, 'y': -0.12, 'z': None})

    assert rows == ["offset x: +0.0500", "offset y: -0.1200"]


# --- offsets against the session baseline ----------------------------------
#
# The baseline sits at x 100.0000, y -40.0000 and a trigger plane of 1.0000,
# and T1 is measured 0.0500 to the right of it, 0.2000 in front of it and
# 0.0300 above it. Every expected offset below is that seeded difference.

BASELINE = {'tool': 0, 'x': 100.0, 'y': -40.0, 'z_curve': None,
            'z_trigger': 1.0}


def _measured(x, y, z_trigger, session_id=4):
    return {
        'x': x,
        'y': y,
        'z_curve': None,
        'z_crossing': 2.5,
        'z_trigger': z_trigger,
        'setpoint_temperature': 150.0,
        'observed_temperature': 149.7,
        'agg': {'samples_used': 6916},
        'session_id': session_id,
        'measured_time': 1234.5,
    }


def test_a_measured_tool_reports_its_difference_from_the_baseline():
    offsets = etc.measured_offsets(
        1, 0, _measured(100.05, -40.2, 1.03), BASELINE, True)

    assert offsets['x'] == pytest.approx(0.05, abs=1e-12)
    assert offsets['y'] == pytest.approx(-0.2, abs=1e-12)
    assert offsets['z'] == pytest.approx(0.03, abs=1e-12)


def test_a_measurement_without_a_descent_reports_no_z_offset():
    # A zero here would read as a measured offset of zero.
    offsets = etc.measured_offsets(
        1, 0, _measured(100.05, -40.2, 1.03), BASELINE, False)

    assert offsets['z'] is None


def test_the_baseline_tool_reports_no_offsets_of_its_own():
    assert etc.measured_offsets(
        0, 0, _measured(100.0, -40.0, 1.0), BASELINE, True) is None


def test_a_tool_measured_without_a_session_baseline_reports_no_offsets():
    assert etc.measured_offsets(
        1, 0, _measured(100.05, -40.2, 1.03), None, True) is None


# --- the status document a macro reads -------------------------------------


def _status(results, baseline=BASELINE, session_id=4, calibrate_z=True):
    return etc.status_document(
        {}, results, baseline, 0, session_id, 1, 2, calibrate_z)


def test_the_status_document_publishes_a_measured_tool_in_full():
    document = _status({1: _measured(100.05, -40.2, 1.03)})

    assert set(document['tools']) == {'1'}
    published = document['tools']['1']
    assert published['offset_x'] == pytest.approx(0.05, abs=1e-12)
    assert published['offset_y'] == pytest.approx(-0.2, abs=1e-12)
    assert published['offset_z'] == pytest.approx(0.03, abs=1e-12)
    assert published['session_id'] == 4
    assert published['center_x'] == 100.05
    assert published['center_y'] == -40.2
    assert published['z_crossing'] == 2.5
    assert published['measured_time'] == 1234.5


def test_the_status_document_names_the_session_and_the_baseline_tool():
    document = _status({1: _measured(100.05, -40.2, 1.03)})

    assert document['calibrate_z'] is True
    assert document['tool_count'] == 2
    assert document['baseline_tool'] == 0
    assert document['session_id'] == 4
    assert document['last_tool'] == 1
    assert document['anchors'] == {}


def test_the_baseline_tools_own_entry_publishes_empty_offsets():
    document = _status({0: _measured(100.0, -40.0, 1.0)})

    assert document['tools']['0']['offset_x'] is None
    assert document['tools']['0']['offset_y'] is None
    assert document['tools']['0']['offset_z'] is None
    assert document['tools']['0']['center_x'] == 100.0


def test_a_measurement_from_an_earlier_session_is_not_published():
    document = _status({1: _measured(100.05, -40.2, 1.03, session_id=3)})

    assert document['tools'] == {}


def test_a_measurement_whose_baseline_was_cleared_is_not_published():
    # An anchor run of the baseline tool drops the session baseline, and the
    # results measured against it have to go with it: left published they
    # would carry empty offsets that read as a measurement of zero.
    document = _status({1: _measured(100.05, -40.2, 1.03)}, baseline=None)

    assert document['tools'] == {}
    assert document['baseline_tool'] is None


def test_a_status_document_without_z_calibration_publishes_no_z_offset():
    document = _status(
        {1: _measured(100.05, -40.2, 1.03)}, calibrate_z=False)

    assert document['calibrate_z'] is False
    assert document['tools']['1']['offset_z'] is None
    assert document['tools']['1']['offset_x'] == pytest.approx(
        0.05, abs=1e-12)


def test_a_session_that_measured_nothing_publishes_no_tools():
    document = _status({}, baseline=None)

    assert document['tools'] == {}
    assert document['anchors'] == {}
    assert document['baseline_tool'] is None
