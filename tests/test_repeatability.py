"""Unit tests for the repeatability study's pure surfaces.

Every expected value here is a literal written out by hand, with the
derivation named beside it, so the tests judge the code rather than restate
it.
"""

import pytest

import eddy_tool_calibration as etc


# --- the repeatability and reproducibility decomposition --------------------
#
# One table is used by the tests below, chosen so every figure is exact by
# hand:
#
#   cycle 1: 10.0, 10.2, 10.4   mean 10.2
#   cycle 2: 10.6, 10.8, 11.0   mean 10.8
#
# Within each cycle the deviations from its own mean are -0.2, 0.0 and +0.2,
# so each cycle contributes 0.08 to the sum of squares. The pooled variance is
# 0.16 over 2 * (3 - 1) = 4 degrees of freedom, which is 0.04, so the pooled
# within-cycle standard deviation is exactly 0.2.
#
# The grand mean is 10.5. The variance of the two cycle means over 2 - 1 = 1
# degree of freedom is (0.09 + 0.09) / 1 = 0.18, so their standard deviation
# is sqrt(0.18) = 0.4242640687119285.
#
# The measurement's own share of that is 0.04 / 3 = 0.013333..., leaving the
# docking component 0.18 - 0.0133333... = 0.1666666..., whose square root is
# 0.4082482904638630.


def two_cycles_of_three_runs():
    return [[10.0, 10.2, 10.4], [10.6, 10.8, 11.0]]


def test_the_within_cycle_spread_pools_the_runs_of_every_cycle():
    stats = etc.repeatability_statistics(two_cycles_of_three_runs())

    assert stats['within'] == pytest.approx(0.2, abs=1e-12)


def test_the_grand_mean_covers_every_measurement_of_the_study():
    stats = etc.repeatability_statistics(two_cycles_of_three_runs())

    assert stats['mean'] == pytest.approx(10.5, abs=1e-12)


def test_the_raw_spread_of_the_cycle_means_is_reported_as_measured():
    stats = etc.repeatability_statistics(two_cycles_of_three_runs())

    assert stats['cycle_mean_spread'] == pytest.approx(
        0.4242640687119285, abs=1e-12)


def test_the_docking_component_drops_the_measurements_share_of_the_means():
    stats = etc.repeatability_statistics(two_cycles_of_three_runs())

    assert stats['between'] == pytest.approx(0.4082482904638630, abs=1e-12)
    assert stats['between_resolved'] is True


def test_the_range_spans_the_lowest_and_highest_measurement():
    stats = etc.repeatability_statistics(two_cycles_of_three_runs())

    assert stats['range'] == pytest.approx(1.0, abs=1e-12)


def test_the_largest_deviation_is_measured_from_the_grand_mean():
    stats = etc.repeatability_statistics(two_cycles_of_three_runs())

    assert stats['max_deviation'] == pytest.approx(0.5, abs=1e-12)


def test_the_largest_deviation_counts_a_measurement_below_the_mean():
    # The six measurements 9.0, 9.5, 10.0, 10.5, 10.75 and 11.0 add up to
    # 60.75, so the grand mean is 10.125. The lowest measurement sits 1.125
    # below that mean and the highest sits 0.875 above it, so the largest
    # deviation of this study is the one below the mean.
    stats = etc.repeatability_statistics(
        [[9.0, 9.5, 10.0], [10.5, 10.75, 11.0]])

    assert stats['max_deviation'] == pytest.approx(1.125, abs=1e-12)


def test_two_cycles_leave_the_between_cycle_figures_one_degree_of_freedom():
    stats = etc.repeatability_statistics(two_cycles_of_three_runs())

    assert stats['between_dof'] == 1


def test_a_single_cycle_study_reports_no_between_cycle_degrees_of_freedom():
    stats = etc.repeatability_statistics([[10.0, 10.2, 10.4]])

    assert stats['between_dof'] is None


def test_a_measurement_that_is_not_a_finite_number_is_rejected():
    # A NaN passes every ordered comparison the decomposition makes, so it
    # would come back out as a confident zero spread beside a plausible
    # range. It is refused instead, naming where it sat.
    with pytest.raises(ValueError, match="cycle 2 run 1 measured"):
        etc.repeatability_statistics(
            [[10.0, 10.2], [float('nan'), 10.4]])


def test_an_infinite_measurement_is_rejected():
    with pytest.raises(ValueError, match="not a finite number"):
        etc.repeatability_statistics(
            [[10.0, float('inf')], [10.2, 10.4]])


def test_the_study_reports_the_shape_it_was_run_in():
    stats = etc.repeatability_statistics(two_cycles_of_three_runs())

    assert stats['cycle_count'] == 2
    assert stats['run_count'] == 3


def test_cycle_means_no_further_apart_than_the_noise_resolve_no_docking():
    # Both cycles average 10.2, so the cycle means carry no variation at all
    # while the runs inside them do. Subtracting the measurement's share
    # leaves a negative variance, which is reported as zero rather than as a
    # square root of a negative number or as a spread the data cannot show.
    stats = etc.repeatability_statistics([[10.0, 10.4], [10.1, 10.3]])

    assert stats['cycle_mean_spread'] == pytest.approx(0.0, abs=1e-12)
    assert stats['between'] == 0.0
    assert stats['between_resolved'] is False


def test_a_single_cycle_study_reports_no_between_cycle_figures():
    # One cycle exercises no docking, so nothing separates a docking
    # contribution from the measurement and neither figure is reported.
    stats = etc.repeatability_statistics([[10.0, 10.2, 10.4]])

    assert stats['cycle_mean_spread'] is None
    assert stats['between'] is None
    assert stats['between_resolved'] is None
    assert stats['within'] == pytest.approx(0.2, abs=1e-12)


def test_a_single_cycle_pools_its_own_runs_over_one_less_degree_of_freedom():
    # 10.0, 10.2 and 10.4 deviate by -0.2, 0.0 and +0.2 from their mean of
    # 10.2, so the sum of squares is 0.08 over 1 * (3 - 1) = 2 degrees of
    # freedom, a variance of 0.04 and a standard deviation of exactly 0.2.
    stats = etc.repeatability_statistics([[10.0, 10.2, 10.4]])

    assert stats['within'] == pytest.approx(0.2, abs=1e-12)


def test_cycles_of_different_lengths_are_rejected():
    with pytest.raises(ValueError, match="same number of runs"):
        etc.repeatability_statistics([[10.0, 10.2], [10.6, 10.8, 11.0]])


def test_a_study_of_one_run_per_cycle_is_rejected():
    with pytest.raises(ValueError, match="at least 2 runs"):
        etc.repeatability_statistics([[10.0], [10.6]])


def test_a_study_with_no_cycles_is_rejected():
    with pytest.raises(ValueError, match="at least one cycle"):
        etc.repeatability_statistics([])


# --- summary rows ----------------------------------------------------------


def test_the_measurement_spread_row_names_the_within_cycle_figure():
    assert etc.measurement_spread_row('x', {'within': 0.0057}) == (
        "x measurement spread: 0.0057 mm")


def test_the_docking_spread_row_folds_in_its_degrees_of_freedom():
    stats = {
        'cycle_mean_spread': 0.005,
        'between': 0.0031,
        'between_resolved': True,
        'between_dof': 2,
    }

    assert etc.docking_spread_row('y', stats) == (
        "y docking spread: 0.0031 mm (2 degrees of freedom)")


def test_an_unresolved_docking_component_says_so_beside_the_zero():
    stats = {
        'cycle_mean_spread': 0.0,
        'between': 0.0,
        'between_resolved': False,
        'between_dof': 1,
        'within': 0.2236067977499790,
    }

    assert etc.docking_spread_row('y', stats) == (
        "y docking spread: 0.0000 mm (1 degrees of freedom), the cycle "
        "means differ by no more than the measurement itself")


def test_a_study_whose_measurements_never_varied_says_exactly_that():
    # Every measurement read the same value, so there is no measurement noise
    # for the cycle means to hide behind and the zero is not a masked docking.
    stats = {
        'cycle_mean_spread': 0.0,
        'between': 0.0,
        'between_resolved': False,
        'between_dof': 1,
        'within': 0.0,
    }

    assert etc.docking_spread_row('x', stats) == (
        "x docking spread: 0.0000 mm (1 degrees of freedom), the "
        "measurements did not vary at all")


def test_a_single_cycle_docking_spread_says_it_was_not_measured():
    assert etc.docking_spread_row('z', {'cycle_mean_spread': None}) == (
        "z docking spread: not measured, the study ran one cycle")


def test_the_worst_deviation_row_names_the_largest_deviation_from_the_mean():
    assert etc.worst_deviation_row('x', {'max_deviation': 0.0121}) == (
        "x worst deviation from the mean: 0.0121 mm")


def test_the_summary_lists_settings_then_a_blank_line_then_the_figures():
    stats = {
        'x': {
            'within': 0.0057, 'cycle_mean_spread': 0.005, 'between': 0.00001,
            'between_resolved': True, 'between_dof': 2,
            'max_deviation': 0.0121,
        },
        'y': {
            'within': 0.0046, 'cycle_mean_spread': 0.005, 'between': 0.0031,
            'between_resolved': True, 'between_dof': 2,
            'max_deviation': 0.0103,
        },
    }

    rows = etc.repeatability_summary_rows(
        0, 5, 3, 'through_tool', 1, False, ['x', 'y'], stats,
        [('stepper_x', 0.0125), ('stepper_y', 0.0125)],
        '/log_dir/repeatability_T0_001.csv')

    assert rows == [
        "repeatability summary:",
        "tool: T0",
        "runs per cycle: 5",
        "cycles: 3",
        "measurements: 15",
        "z descent: skipped",
        "docking between cycles: each cycle mounts T1 and remounts the "
        "measured tool",
        "",
        "x measurement spread: 0.0057 mm",
        "y measurement spread: 0.0046 mm",
        "x docking spread: 0.0000 mm (2 degrees of freedom)",
        "y docking spread: 0.0031 mm (2 degrees of freedom)",
        "x worst deviation from the mean: 0.0121 mm",
        "y worst deviation from the mean: 0.0103 mm",
        "stepper microstep distance stepper_x: 0.012500 mm",
        "stepper microstep distance stepper_y: 0.012500 mm",
        "measurement data: /log_dir/repeatability_T0_001.csv",
    ]


def test_the_summary_gives_z_the_same_three_rows_as_x_and_y_in_position():
    stats = {
        'x': {
            'within': 0.01, 'cycle_mean_spread': None, 'max_deviation': 0.02,
        },
        'y': {
            'within': 0.01, 'cycle_mean_spread': None, 'max_deviation': 0.02,
        },
        'z': {
            'within': 0.03, 'cycle_mean_spread': None, 'max_deviation': 0.04,
        },
    }

    rows = etc.repeatability_summary_rows(
        0, 3, 1, 'no_other_tool', None, True, ['x', 'y', 'z'], stats, [],
        '/log_dir/repeatability_T0_002.csv')

    assert rows == [
        "repeatability summary:",
        "tool: T0",
        "runs per cycle: 3",
        "cycles: 1",
        "measurements: 3",
        "z descent: included",
        "docking between cycles: not exercised, tool_count names no second "
        "tool to dock through",
        "",
        "x measurement spread: 0.0100 mm",
        "y measurement spread: 0.0100 mm",
        "z measurement spread: 0.0300 mm",
        "x docking spread: not measured, the study ran one cycle",
        "y docking spread: not measured, the study ran one cycle",
        "z docking spread: not measured, the study ran one cycle",
        "x worst deviation from the mean: 0.0200 mm",
        "y worst deviation from the mean: 0.0200 mm",
        "z worst deviation from the mean: 0.0400 mm",
        "measurement data: /log_dir/repeatability_T0_002.csv",
    ]


# --- the docking row --------------------------------------------------------


def test_a_missing_toolchange_gcode_is_named_as_the_reason_for_no_docking():
    assert etc.docking_row('no_toolchange_gcode', None, 1) == (
        "docking between cycles: not exercised, toolchange_gcode is not set")


def test_cycles_without_a_docking_partner_are_named_as_a_drift_measurement():
    assert etc.docking_row('no_other_tool', None, 4) == (
        "docking between cycles: not exercised, tool_count names no second "
        "tool to dock through, so the cycles measure drift over time instead")


def test_an_unhandled_docking_state_is_rejected():
    with pytest.raises(ValueError, match="unhandled docking state"):
        etc.docking_row('sometimes', 1, 1)


# --- heating before a study ------------------------------------------------


def test_a_tool_with_a_stored_reference_is_heated_to_its_own_setpoint():
    assert etc.study_heating(True, True) == 'to_anchor_temperature'


def test_a_tool_without_a_stored_reference_is_not_heated():
    assert etc.study_heating(True, False) == 'no_anchor'


def test_nothing_is_heated_with_z_calibration_off():
    assert etc.study_heating(False, True) == 'z_calibration_off'


# --- choosing the docking tool ---------------------------------------------


def test_the_docking_tool_is_the_lowest_tool_that_is_not_the_measured_one():
    assert etc.study_docking(2, 4, True) == ('through_tool', 0)


def test_the_docking_tool_skips_the_measured_tool_at_the_bottom():
    assert etc.study_docking(0, 4, True) == ('through_tool', 1)


def test_a_single_tool_machine_has_no_tool_to_dock_through():
    assert etc.study_docking(0, 1, True) == ('no_other_tool', None)


def test_a_machine_without_a_tool_count_has_no_tool_to_dock_through():
    assert etc.study_docking(0, None, True) == ('no_other_tool', None)


def test_a_machine_without_toolchange_lines_cannot_dock_at_all():
    assert etc.study_docking(1, 4, False) == ('no_toolchange_gcode', None)


# --- per-measurement progress row -------------------------------------------


def test_the_first_measurement_of_the_first_cycle_is_announced():
    assert etc.measurement_progress_row(1, 3, 1, 5) == (
        "progress: cycle 1 of 3, measurement 1 of 5")


def test_the_last_measurement_of_the_first_cycle_is_announced():
    assert etc.measurement_progress_row(1, 3, 5, 5) == (
        "progress: cycle 1 of 3, measurement 5 of 5")


def test_the_first_measurement_of_the_last_cycle_is_announced():
    assert etc.measurement_progress_row(3, 3, 1, 5) == (
        "progress: cycle 3 of 3, measurement 1 of 5")


def test_the_last_measurement_of_the_last_cycle_is_announced():
    assert etc.measurement_progress_row(3, 3, 5, 5) == (
        "progress: cycle 3 of 3, measurement 5 of 5")


# --- step distance rows ----------------------------------------------------


def test_the_step_distance_rows_name_each_stepper_they_came_from():
    assert etc.step_distance_rows(
        [('stepper_x', 0.0125), ('stepper_y', 0.003125)]) == [
        "stepper microstep distance stepper_x: 0.012500 mm",
        "stepper microstep distance stepper_y: 0.003125 mm",
    ]


def test_steppers_that_could_not_be_read_produce_no_rows():
    assert etc.step_distance_rows([]) == []


# --- log files -------------------------------------------------------------


def test_the_drift_log_header_lists_every_column_in_file_order():
    assert etc.csv_header(etc.HISTORY_COLUMNS) == (
        "timestamp,command,center_x,center_y,offset_x,offset_y,z_crossing,"
        "trigger_z,offset_z,baseline_session,setpoint_temperature,"
        "observed_temperature,samples_used\n")


def test_a_study_file_carries_the_cycle_and_run_in_front_of_the_measurement():
    assert etc.csv_header(etc.STUDY_COLUMNS) == (
        "cycle,run,timestamp,command,center_x,center_y,offset_x,offset_y,"
        "z_crossing,trigger_z,offset_z,baseline_session,setpoint_temperature,"
        "observed_temperature,samples_used\n")


def test_a_log_row_writes_each_value_at_the_precision_it_is_reported_at():
    row = etc.csv_row(etc.HISTORY_COLUMNS, {
        'timestamp': '2026-08-01T12:00:00Z',
        'command': 'EDDY_CALIBRATE_OFFSET',
        'center_x': 99.05771,
        'center_y': -40.57794,
        'offset_x': 0.02241,
        'offset_y': -0.25981,
        'z_crossing': 1.23456,
        'trigger_z': 2.34567,
        'offset_z': 0.01567,
        'baseline_session': 3,
        'setpoint_temperature': 150.0,
        'observed_temperature': 151.84,
        'samples_used': 6916,
    })

    assert row == (
        "2026-08-01T12:00:00Z,EDDY_CALIBRATE_OFFSET,99.0577,-40.5779,0.0224,"
        "-0.2598,1.2346,2.3457,0.0157,3,150.0,151.8,6916\n")


def test_a_value_that_was_not_measured_is_written_as_an_empty_field():
    row = etc.csv_row(etc.HISTORY_COLUMNS, {
        'timestamp': '2026-08-01T12:00:00Z',
        'command': 'EDDY_REPEATABILITY',
        'center_x': 99.0577,
        'center_y': -40.5779,
        'offset_x': None,
        'offset_y': None,
        'z_crossing': None,
        'trigger_z': None,
        'offset_z': None,
        'baseline_session': None,
        'setpoint_temperature': None,
        'observed_temperature': None,
        'samples_used': 3458,
    })

    assert row == (
        "2026-08-01T12:00:00Z,EDDY_REPEATABILITY,99.0577,-40.5779,,,,,,,,,"
        "3458\n")


def test_a_study_of_an_unheated_tool_still_records_the_reading_it_measured():
    # A study without a stored reference heats nothing, so the row carries no
    # setpoint and the reading it did take stands on its own.
    row = etc.csv_row(etc.HISTORY_COLUMNS, {
        'timestamp': '2026-08-01T12:00:00Z',
        'command': 'EDDY_REPEATABILITY',
        'center_x': 99.0577,
        'center_y': -40.5779,
        'offset_x': None,
        'offset_y': None,
        'z_crossing': None,
        'trigger_z': None,
        'offset_z': None,
        'baseline_session': None,
        'setpoint_temperature': None,
        'observed_temperature': 24.6,
        'samples_used': 3458,
    })

    assert row == (
        "2026-08-01T12:00:00Z,EDDY_REPEATABILITY,99.0577,-40.5779,,,,,,,,24.6,"
        "3458\n")


def test_a_log_row_missing_a_column_is_rejected():
    with pytest.raises(
            ValueError, match="missing the setpoint_temperature field"):
        etc.csv_row(etc.HISTORY_COLUMNS, {
            'timestamp': '2026-08-01T12:00:00Z',
            'command': 'EDDY_CALIBRATE_Z',
            'center_x': 1.0,
            'center_y': 2.0,
            'offset_x': None,
            'offset_y': None,
            'z_crossing': None,
            'trigger_z': 3.0,
            'offset_z': None,
            'baseline_session': None,
            'observed_temperature': 149.7,
            'samples_used': 10,
        })


def test_a_log_row_carrying_a_field_the_layout_has_no_column_for_is_rejected():
    with pytest.raises(ValueError, match="unknown field centre_x"):
        etc.csv_row((('center_x', '%.4f'),), {'centre_x': 1.0})


# --- log timestamps --------------------------------------------------------


def test_a_log_timestamp_is_written_in_utc_with_a_trailing_marker():
    # The Unix epoch is midnight on 1 January 1970 in UTC, so a timestamp
    # taken at second 0 reads as that moment wherever the printer stands.
    assert etc.log_timestamp(0) == "1970-01-01T00:00:00Z"


def test_a_log_timestamp_does_not_shift_with_the_local_time_zone():
    # 1 January 2026 falls 20454 days after the epoch (56 years of 365 days
    # plus the 14 leap days of 1972 through 2024), so midnight that day is
    # second 20454 * 86400 = 1767225600 and midday is 43200 later. A
    # timestamp rendered in local time would move that hour.
    assert etc.log_timestamp(1767268800) == "2026-01-01T12:00:00Z"


# --- appending to a log file -----------------------------------------------


TWO_COLUMNS = (('cycle', '%d'), ('center_x', '%.4f'))
THREE_COLUMNS = (('cycle', '%d'), ('center_x', '%.4f'), ('center_y', '%.4f'))


def test_a_new_log_file_is_written_its_header_first(tmp_path):
    path = str(tmp_path / "logs" / "history_T0.csv")

    etc.append_csv(path, TWO_COLUMNS, {'cycle': 1, 'center_x': 99.0577})

    assert open(path).read() == "cycle,center_x\n1,99.0577\n"


def test_an_empty_log_file_is_given_the_header_it_never_got(tmp_path):
    # An interrupted create leaves a file with no header, and a row appended
    # under nothing would be read against the wrong columns.
    path = tmp_path / "history_T0.csv"
    path.write_text("")

    etc.append_csv(str(path), TWO_COLUMNS, {'cycle': 2, 'center_x': 1.5})

    assert path.read_text() == "cycle,center_x\n2,1.5000\n"


def test_a_second_row_goes_under_the_header_already_there(tmp_path):
    path = tmp_path / "history_T0.csv"
    path.write_text("cycle,center_x\n1,99.0577\n")

    rotated = etc.append_csv(
        str(path), TWO_COLUMNS, {'cycle': 2, 'center_x': 99.0600})

    assert rotated is None
    assert path.read_text() == (
        "cycle,center_x\n1,99.0577\n2,99.0600\n")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["history_T0.csv"]


def test_a_log_file_whose_header_names_other_columns_is_rotated_aside(
        tmp_path):
    path = tmp_path / "history_T0.csv"
    path.write_text("cycle,centre_x\n1,99.0577\n")

    rotated = etc.append_csv(
        str(path), TWO_COLUMNS, {'cycle': 2, 'center_x': 1.0})

    assert rotated == str(tmp_path / "history_T0.1.csv")
    assert path.read_text() == "cycle,center_x\n2,1.0000\n"
    assert (tmp_path / "history_T0.1.csv").read_text() == (
        "cycle,centre_x\n1,99.0577\n")


def test_a_second_mismatch_rotates_to_a_name_distinct_from_the_first(
        tmp_path):
    path = tmp_path / "history_T0.csv"
    path.write_text("cycle,centre_x\n1,99.0577\n")

    etc.append_csv(str(path), TWO_COLUMNS, {'cycle': 2, 'center_x': 1.0})
    second_rotation = etc.append_csv(
        str(path), THREE_COLUMNS,
        {'cycle': 3, 'center_x': 2.0, 'center_y': 3.0})

    assert second_rotation == str(tmp_path / "history_T0.2.csv")
    assert path.read_text() == "cycle,center_x,center_y\n3,2.0000,3.0000\n"
    assert (tmp_path / "history_T0.1.csv").read_text() == (
        "cycle,centre_x\n1,99.0577\n")
    assert (tmp_path / "history_T0.2.csv").read_text() == (
        "cycle,center_x\n2,1.0000\n")


# --- what a measurement reports --------------------------------------------


def test_a_study_of_the_baseline_tool_reports_no_offsets():
    # The baseline tool's own offsets are zero by definition, so a study of it
    # leaves the offset columns empty exactly as a calibration run does.
    assert etc.reports_offsets(0, 0, True) is False


def test_a_measurement_without_a_baseline_reports_no_offsets():
    assert etc.reports_offsets(1, 0, False) is False


def test_a_measurement_of_another_tool_against_a_baseline_reports_offsets():
    assert etc.reports_offsets(1, 0, True) is True


# --- the axes a study reports ----------------------------------------------


def test_a_study_without_the_descent_reports_the_two_planar_axes():
    assert etc.study_axes(False) == ['x', 'y']


def test_a_study_with_the_descent_reports_z_last():
    assert etc.study_axes(True) == ['x', 'y', 'z']


def test_the_z_axis_of_a_study_reads_the_reconstructed_trigger_plane():
    assert etc.study_axis_field('z') == 'z_trigger'


def test_the_planar_axes_of_a_study_read_the_fitted_center():
    assert etc.study_axis_field('x') == 'x'
    assert etc.study_axis_field('y') == 'y'


def test_an_unhandled_study_axis_is_rejected():
    with pytest.raises(ValueError, match="unhandled study axis"):
        etc.study_axis_field('w')


# --- log file names --------------------------------------------------------


def test_a_tools_drift_log_is_named_after_the_tool():
    assert etc.history_filename(3) == "history_T3.csv"


def test_the_first_study_of_a_tool_takes_the_first_index():
    assert etc.next_study_filename([], 0) == "repeatability_T0_001.csv"


def test_a_study_takes_the_index_above_the_highest_one_already_written():
    existing = ["repeatability_T1_001.csv", "repeatability_T1_007.csv",
                "repeatability_T1_002.csv"]

    assert etc.next_study_filename(existing, 1) == "repeatability_T1_008.csv"


def test_another_tools_studies_do_not_move_a_tools_own_index():
    existing = ["repeatability_T0_004.csv", "repeatability_T12_009.csv"]

    assert etc.next_study_filename(existing, 1) == "repeatability_T1_001.csv"


def test_unrelated_files_in_the_directory_do_not_move_the_index():
    existing = ["eddy_scan_coarse_45_deg.csv", "history_T1.csv",
                "repeatability_T1_notes.csv", "repeatability_T1_003.csv"]

    assert etc.next_study_filename(existing, 1) == "repeatability_T1_004.csv"
