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


def test_the_summary_rows_name_which_spread_is_the_measurement():
    stats = {
        'cycle_count': 2,
        'run_count': 3,
        'mean': 10.5,
        'within': 0.2,
        'cycle_mean_spread': 0.4242640687119285,
        'between': 0.4082482904638630,
        'between_resolved': True,
        'between_dof': 1,
        'range': 1.0,
        'max_deviation': 0.5,
    }

    assert etc.repeatability_rows('x', stats) == [
        "x mean: 10.5000 mm",
        "x within-cycle spread (the measurement): 0.2000 mm",
        "x spread of the cycle means (the docking plus the measurement's "
        "share of a cycle mean): 0.4243 mm",
        "x between-cycle spread (the docking and any drift between cycles): "
        "0.4082 mm",
        "x between-cycle degrees of freedom: 1",
        "x range: 1.0000 mm",
        "x largest deviation from the mean: 0.5000 mm",
    ]


def test_an_unresolved_docking_component_says_so_beside_the_zero():
    stats = {
        'cycle_count': 2,
        'run_count': 2,
        'mean': 10.2,
        'within': 0.2236067977499790,
        'cycle_mean_spread': 0.0,
        'between': 0.0,
        'between_resolved': False,
        'between_dof': 1,
        'range': 0.4,
        'max_deviation': 0.2,
    }
    rows = etc.repeatability_rows('y', stats)

    assert rows[3] == (
        "y between-cycle spread (the docking and any drift between cycles): "
        "0.0000 mm, the cycle means differ by no more than the measurement "
        "itself")


def test_a_study_whose_measurements_never_varied_says_exactly_that():
    # Every measurement read the same value, so there is no measurement noise
    # for the cycle means to hide behind and the zero is not a masked docking.
    stats = {
        'cycle_count': 2,
        'run_count': 2,
        'mean': 10.2,
        'within': 0.0,
        'cycle_mean_spread': 0.0,
        'between': 0.0,
        'between_resolved': False,
        'between_dof': 1,
        'range': 0.0,
        'max_deviation': 0.0,
    }
    rows = etc.repeatability_rows('x', stats)

    assert rows[3] == (
        "x between-cycle spread (the docking and any drift between cycles): "
        "0.0000 mm, the measurements did not vary at all")


def test_a_single_cycle_summary_says_the_docking_was_not_measured():
    stats = {
        'cycle_count': 1,
        'run_count': 3,
        'mean': 10.2,
        'within': 0.2,
        'cycle_mean_spread': None,
        'between': None,
        'between_resolved': None,
        'between_dof': None,
        'range': 0.4,
        'max_deviation': 0.2,
    }

    assert etc.repeatability_rows('z', stats) == [
        "z mean: 10.2000 mm",
        "z within-cycle spread (the measurement): 0.2000 mm",
        "z between-cycle spread (the docking): not measured, the study ran "
        "one cycle",
        "z range: 0.4000 mm",
        "z largest deviation from the mean: 0.2000 mm",
    ]


def test_a_cycle_reports_its_own_mean_range_and_standard_deviation():
    # 10.0, 10.2 and 10.4 average 10.2 and span 0.4. Their deviations from
    # the mean are -0.2, 0.0 and +0.2, so the sum of squares is 0.08 over
    # 3 - 1 = 2 degrees of freedom, a variance of 0.04 and a standard
    # deviation of exactly 0.2. That is the estimator the closing summary
    # reports, so the same three runs read the same in both places.
    assert etc.cycle_progress_rows(2, [('x', [10.0, 10.2, 10.4])]) == [
        "cycle 2 x mean: 10.2000 mm",
        "cycle 2 x range: 0.4000 mm",
        "cycle 2 x standard deviation: 0.2000 mm",
    ]


def test_a_cycle_of_one_run_cannot_be_summarised():
    with pytest.raises(ValueError, match="at least 2 runs"):
        etc.cycle_progress_rows(1, [('y', [10.0])])


# --- the study plan --------------------------------------------------------


def test_the_plan_names_the_docking_tool_and_the_measurement_count():
    rows = etc.study_plan_rows(
        1, 10, 3, False, 'through_tool', 0, 'z_calibration_off', None, 396.0)

    assert rows == [
        "repeatability study:",
        "tool: T1",
        "runs per cycle: 10",
        "cycles: 3",
        "measurements: 30",
        "z descent: skipped",
        "nozzle heating: none, calibrate_z is False",
        "docking between cycles: each cycle mounts T0 and remounts the "
        "measured tool",
        "estimated run time: 396 s (6.6 min), a floor that counts the scan "
        "and descent moves only, without the toolchanges or the heating",
    ]


def test_the_plan_leaves_out_a_run_time_it_could_not_estimate():
    rows = etc.study_plan_rows(
        0, 5, 1, True, 'no_other_tool', None, 'to_anchor_temperature', 150.0,
        None)

    assert rows == [
        "repeatability study:",
        "tool: T0",
        "runs per cycle: 5",
        "cycles: 1",
        "measurements: 5",
        "z descent: included",
        "nozzle heating: held at 150.0 C, the temperature the tool's Z "
        "reference was measured at",
        "docking between cycles: not exercised, tool_count names no second "
        "tool to dock through",
    ]


def test_a_plan_of_one_run_per_cycle_is_rejected():
    with pytest.raises(ValueError, match="at least 2 runs"):
        etc.study_plan_rows(
            0, 1, 2, False, 'through_tool', 1, 'z_calibration_off', None,
            None)


def test_a_plan_of_no_cycles_is_rejected():
    with pytest.raises(ValueError, match="at least 1 cycle"):
        etc.study_plan_rows(
            0, 5, 0, False, 'through_tool', 1, 'z_calibration_off', None,
            None)


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


def test_a_tool_with_a_stored_reference_is_heated_to_its_own_temperature():
    assert etc.study_heating(True, True) == 'to_anchor_temperature'


def test_a_tool_without_a_stored_reference_is_not_heated():
    assert etc.study_heating(True, False) == 'no_anchor'


def test_nothing_is_heated_with_z_calibration_off():
    assert etc.study_heating(False, True) == 'z_calibration_off'


def test_an_unheated_tool_says_its_spread_is_not_comparable():
    assert etc.heating_row('no_anchor', None) == (
        "nozzle heating: none, the tool has no stored Z reference, so this "
        "spread is not comparable with an offset run's")


def test_an_unhandled_heating_state_is_rejected():
    with pytest.raises(ValueError, match="unhandled heating state"):
        etc.heating_row('lukewarm', 150.0)


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


# --- run time estimate -----------------------------------------------------
#
# Every figure below is worked out by hand from the documented collection
# times: a pass settles 0.050 s, runs its scan, and collects 0.200 s past the
# end of the move, and a descent step lifts by the 0.500 mm approach hop and
# dwells 0.200 s, with the descent bracketed by two 1.000 s settle dwells and
# the same 0.200 s tail. A pass drains the motion queue four times and a
# descent twice, and a drained queue costs 0.250 s before the next move
# begins.


def test_the_estimate_counts_every_pass_of_every_scan_round():
    # A 4 mm pass at 4 mm/s takes 1.0 s, the legs down to the scan plane and
    # back cover 2 * 2 mm at 10 mm/s for 0.4 s, the settle and tail add
    # 0.25 s, and the four queue restarts add 1.0 s, so a pass is 2.65 s.
    # Two rounds of four passes is 21.2 s.
    seconds = etc.measurement_time_estimate(
        2, 4, 4.0, 4.0, 2.0, 10.0, 10, 0.5, False)

    assert seconds == pytest.approx(21.2, abs=1e-9)


def test_the_estimate_adds_every_descent_step_when_the_descent_runs():
    # Each of the 10 steps lifts 0.5 mm less the 0.5 mm step already
    # descended, drops the 0.5 mm hop at 10 mm/s for 0.05 s, and dwells
    # 0.200 s, so the steps take 2.5 s. The two 1.0 s settle dwells, the
    # 0.2 s tail and the two 0.25 s queue restarts bring the descent to
    # 5.2 s, on top of the 21.2 s of scanning.
    seconds = etc.measurement_time_estimate(
        2, 4, 4.0, 4.0, 2.0, 10.0, 10, 0.5, True)

    assert seconds == pytest.approx(26.4, abs=1e-9)


def test_a_descent_step_finer_than_the_hop_still_pays_for_the_lift():
    # At the default z_step of 0.05 mm each step lifts 0.5 - 0.05 = 0.45 mm
    # and drops the full 0.5 mm hop, so it travels 0.95 mm at 10 mm/s for
    # 0.095 s and dwells 0.200 s, a step of 0.295 s. Ten steps take 2.95 s,
    # and the 2.7 s of settle dwells, tail and queue restarts bring the
    # descent to 5.65 s, on top of the 21.2 s of scanning.
    seconds = etc.measurement_time_estimate(
        2, 4, 4.0, 4.0, 2.0, 10.0, 10, 0.05, True)

    assert seconds == pytest.approx(26.85, abs=1e-9)


def test_an_estimate_without_a_scan_round_is_rejected():
    with pytest.raises(ValueError, match="at least one scan round"):
        etc.measurement_time_estimate(0, 4, 4.0, 4.0, 2.0, 10.0, 10, 0.5,
                                      False)


def test_an_estimate_without_a_scan_pass_is_rejected():
    with pytest.raises(ValueError, match="at least one pass"):
        etc.measurement_time_estimate(2, 0, 4.0, 4.0, 2.0, 10.0, 10, 0.5,
                                      False)


def test_an_estimate_at_a_standstill_scan_speed_is_rejected():
    with pytest.raises(ValueError, match="must be greater than 0"):
        etc.measurement_time_estimate(2, 4, 4.0, 0.0, 2.0, 10.0, 10, 0.5,
                                      False)


def test_an_estimate_at_a_standstill_z_speed_is_rejected():
    with pytest.raises(ValueError, match="must be greater than 0"):
        etc.measurement_time_estimate(2, 4, 4.0, 4.0, 2.0, 0.0, 10, 0.5,
                                      False)


def test_an_estimate_of_a_scan_pass_of_no_length_is_rejected():
    with pytest.raises(ValueError, match="scan length must be greater than 0"):
        etc.measurement_time_estimate(2, 4, 0.0, 4.0, 2.0, 10.0, 10, 0.5,
                                      False)


def test_an_estimate_of_a_descent_without_steps_is_rejected():
    with pytest.raises(ValueError, match="at least one step"):
        etc.measurement_time_estimate(2, 4, 4.0, 4.0, 2.0, 10.0, 0, 0.5, True)


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
        "trigger_z,offset_z,baseline_session,temperature,samples_used\n")


def test_a_study_file_carries_the_cycle_and_run_in_front_of_the_measurement():
    assert etc.csv_header(etc.STUDY_COLUMNS) == (
        "cycle,run,timestamp,command,center_x,center_y,offset_x,offset_y,"
        "z_crossing,trigger_z,offset_z,baseline_session,temperature,"
        "samples_used\n")


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
        'temperature': 150.04,
        'samples_used': 6916,
    })

    assert row == (
        "2026-08-01T12:00:00Z,EDDY_CALIBRATE_OFFSET,99.0577,-40.5779,0.0224,"
        "-0.2598,1.2346,2.3457,0.0157,3,150.0,6916\n")


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
        'temperature': None,
        'samples_used': 3458,
    })

    assert row == (
        "2026-08-01T12:00:00Z,EDDY_REPEATABILITY,99.0577,-40.5779,,,,,,,,"
        "3458\n")


def test_a_log_row_missing_a_column_is_rejected():
    with pytest.raises(ValueError, match="missing the temperature field"):
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

    etc.append_csv(str(path), TWO_COLUMNS, {'cycle': 2, 'center_x': 99.0600})

    assert path.read_text() == (
        "cycle,center_x\n1,99.0577\n2,99.0600\n")


def test_a_log_file_whose_header_names_other_columns_is_refused(tmp_path):
    path = tmp_path / "history_T0.csv"
    path.write_text("cycle,centre_x\n1,99.0577\n")

    with pytest.raises(ValueError, match="Move that file aside"):
        etc.append_csv(str(path), TWO_COLUMNS, {'cycle': 2, 'center_x': 1.0})


def test_a_refused_log_file_is_left_exactly_as_it_was(tmp_path):
    path = tmp_path / "history_T0.csv"
    path.write_text("cycle,centre_x\n1,99.0577\n")

    with pytest.raises(ValueError):
        etc.append_csv(str(path), TWO_COLUMNS, {'cycle': 2, 'center_x': 1.0})

    assert path.read_text() == "cycle,centre_x\n1,99.0577\n"


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
