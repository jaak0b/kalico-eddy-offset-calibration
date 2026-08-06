"""Unit tests for switch probing aggregation, Z anchoring and state storage.

Every expected value here is a literal fixed outside the implementation:
either the seed a synthetic descent curve was built from, or a value worked
out by hand from that seed and cited beside the assertion.
"""

import os

import pytest

import eddy_tool_calibration as etc


# --- switch press aggregation ----------------------------------------------


def test_the_first_press_is_discarded_and_the_median_of_three_is_taken():
    # Arrange: four presses whose first one is a warm-up that triggered a
    # whole millimetre high. The three counted presses are 2.0000, 2.1000 and
    # 2.2000 mm, whose middle value is 2.1000 mm.
    heights = [3.1000, 2.0000, 2.1000, 2.2000]

    # Act
    median, counted, spread = etc.aggregate_switch_presses(heights, 0.5000)

    # Assert
    assert median == pytest.approx(2.1000, abs=1e-9)
    assert counted == [2.0000, 2.1000, 2.2000]
    assert spread == pytest.approx(0.2000, abs=1e-9)


def test_the_median_ignores_the_order_the_counted_presses_arrived_in():
    # Arrange: the same three counted heights as above in a different order,
    # so the middle value is still 2.1000 mm.
    heights = [3.1000, 2.2000, 2.0000, 2.1000]

    # Act
    median, counted, spread = etc.aggregate_switch_presses(heights, 0.5000)

    # Assert
    assert median == pytest.approx(2.1000, abs=1e-9)
    assert counted == [2.2000, 2.0000, 2.1000]
    assert spread == pytest.approx(0.2000, abs=1e-9)


def test_rejects_counted_presses_that_disagree_beyond_the_tolerance():
    # Arrange: the counted presses span 2.0000 to 2.0900 mm, a spread of
    # 0.0900 mm, against a 0.0200 mm tolerance.
    heights = [2.5000, 2.0000, 2.0500, 2.0900]

    # Act / Assert
    with pytest.raises(ValueError, match="0.0900 mm"):
        etc.aggregate_switch_presses(heights, 0.0200)


def test_accepts_counted_presses_inside_the_tolerance():
    # Arrange: the counted presses span 2.0000 to 2.0150 mm, a spread of
    # 0.0150 mm, inside the 0.0200 mm tolerance.
    heights = [2.5000, 2.0000, 2.0100, 2.0150]

    # Act
    median, _counted, spread = etc.aggregate_switch_presses(heights, 0.0200)

    # Assert
    assert median == pytest.approx(2.0100, abs=1e-9)
    assert spread == pytest.approx(0.0150, abs=1e-9)


def test_rejects_a_press_list_that_is_not_the_fixed_press_count():
    with pytest.raises(ValueError, match="needs 4 presses"):
        etc.aggregate_switch_presses([2.0, 2.1, 2.2], 0.5)


def test_rejects_a_switch_tolerance_of_zero():
    with pytest.raises(ValueError, match="tolerance must be greater than 0"):
        etc.aggregate_switch_presses([2.0, 2.1, 2.2, 2.3], 0.0)


# --- anchor construction and evaluation ------------------------------------


def test_the_anchor_sits_at_the_curve_midpoint_above_the_trigger_plane():
    # Arrange: a descent measured from 1.0 mm to 4.0 mm reading 100 Hz at
    # 1.0 mm, 80 Hz at 2.0 mm and 40 Hz at 4.0 mm, with the switch triggering
    # at machine Z 0.4000 mm. The midpoint of 1.0 to 4.0 mm is 2.5 mm, which
    # is a quarter of the way from the 80 Hz step to the 40 Hz step, so the
    # frequency there is 70 Hz and the height above the trigger plane is
    # 2.5 - 0.4 = 2.1 mm.
    curve = etc.build_z_curve([(1.0, 100.0), (2.0, 80.0), (4.0, 40.0)])

    # Act
    anchor_height, anchor_freq = etc.switch_anchor(curve, 0.4000)

    # Assert
    assert anchor_height == pytest.approx(2.1000, abs=1e-9)
    assert anchor_freq == pytest.approx(70.0, abs=1e-9)


def test_the_stored_record_carries_the_anchor_and_the_run_that_measured_it():
    # Arrange: the same descent from 1.0 mm to 4.0 mm and the same 0.4000 mm
    # trigger plane as above, so the anchor pair is 2.1000 mm above the
    # trigger plane at 70 Hz. The run held the nozzle at a 150.0 C setpoint
    # while the heater read 149.7 C, with the sensor running the crab board's
    # 24000000.0 Hz clock on drive current 15, over a coil found at
    # X 349.8721, Y 5.0413.
    curve = etc.build_z_curve([(1.0, 100.0), (2.0, 80.0), (4.0, 40.0)])

    # Act
    record = etc.anchor_record(
        curve, 0.4000, 150.0, 149.7, 24000000.0, 15, 349.8721,
        5.0413)

    # Assert
    assert record['anchor_height'] == pytest.approx(2.1000, abs=1e-9)
    assert record['anchor_frequency'] == pytest.approx(70.0, abs=1e-9)
    assert record['trigger_z'] == pytest.approx(0.4000, abs=1e-9)
    assert record['setpoint_temperature'] == pytest.approx(150.0, abs=1e-9)
    assert record['observed_temperature'] == pytest.approx(149.7, abs=1e-9)
    assert record['curve_low_z'] == pytest.approx(1.0, abs=1e-9)
    assert record['curve_high_z'] == pytest.approx(4.0, abs=1e-9)
    assert record['center_x'] == pytest.approx(349.8721, abs=1e-9)
    assert record['center_y'] == pytest.approx(5.0413, abs=1e-9)


def test_a_stored_record_survives_the_state_file_as_it_was_built():
    # Arrange: a record built by the run rather than written out by hand, so a
    # field the encoder demands and the run never fills fails here.
    curve = etc.build_z_curve([(1.0, 100.0), (2.0, 80.0), (4.0, 40.0)])
    record = etc.anchor_record(
        curve, 0.4000, 150.0, 149.7, 24000000.0, 15, 349.8721,
        5.0413)

    # Act
    decoded = etc.decode_state(etc.encode_state({2: record}))

    # Assert
    assert decoded[2] == record


def test_the_status_readout_withholds_the_sensor_settings_of_an_anchor():
    # Arrange: a full anchor record as EDDY_CALIBRATE_Z stores it.
    record = _anchor_record()

    # Act
    published = etc.anchor_status(2, record)

    # Assert: a macro reads the anchor pair, the temperatures and the trigger
    # plane. sensor_clock, drive_current, the curve bounds and the coil center
    # are diagnostics of the run that measured the anchor.
    assert published == {
        'anchor_height': 4.2130,
        'anchor_frequency': 12345678.0,
        'setpoint_temperature': 150.0,
        'observed_temperature': 149.7,
        'trigger_z': 1.2340,
        'updated': '2026-08-01T14:03:22',
    }


def test_a_status_readout_of_an_incomplete_anchor_names_the_missing_field():
    record = _anchor_record()
    del record['trigger_z']

    with pytest.raises(ValueError, match="T2 is missing the trigger_z field"):
        etc.anchor_status(2, record)


def test_the_stored_anchor_reconstructs_the_trigger_plane_it_was_built_from():
    # Arrange: the same curve and the same 0.4000 mm trigger plane, with the
    # anchor values that were worked out by hand above.
    curve = etc.build_z_curve([(1.0, 100.0), (2.0, 80.0), (4.0, 40.0)])

    # Act
    trigger_z, crossing = etc.trigger_plane_from_anchor(curve, 2.1000, 70.0)

    # Assert: the curve reaches 70 Hz at its 2.5 mm midpoint, so the trigger
    # plane comes back as the 0.4000 mm it was built from.
    assert crossing == pytest.approx(2.5000, abs=1e-9)
    assert trigger_z == pytest.approx(0.4000, abs=1e-9)


def test_rejects_an_anchor_frequency_outside_the_freshly_measured_descent():
    # Arrange: a descent covering 60 Hz to 100 Hz, against an anchor taken at
    # 120 Hz before the coil moved.
    curve = etc.build_z_curve([(1.0, 100.0), (2.0, 80.0), (3.0, 60.0)])

    # Act / Assert: the message carries the range the descent actually
    # covered, so the size of the move is visible.
    with pytest.raises(ValueError, match="60.000 to 100.000"):
        etc.trigger_plane_from_anchor(curve, 1.0, 120.0)


# --- end to end offset arithmetic ------------------------------------------


def test_reports_the_seeded_trigger_delta_for_two_identical_hotends():
    # Arrange: tool A's descent, and tool B built as the same response
    # measured 0.3000 mm higher with its switch trigger 0.3000 mm higher too,
    # which is the identical-hotend case. The seeded truth is that B's nozzle
    # sits 0.3000 mm above A's.
    curve_a = etc.build_z_curve([(1.0, 100.0), (2.0, 80.0), (4.0, 40.0)])
    curve_b = etc.build_z_curve([(1.3, 100.0), (2.3, 80.0), (4.3, 40.0)])

    # Act: anchor both against their own trigger planes, then evaluate both
    # against the same curves in a later session.
    height_a, freq_a = etc.switch_anchor(curve_a, 0.4000)
    height_b, freq_b = etc.switch_anchor(curve_b, 0.7000)
    trigger_a, _ = etc.trigger_plane_from_anchor(curve_a, height_a, freq_a)
    trigger_b, _ = etc.trigger_plane_from_anchor(curve_b, height_b, freq_b)

    # Assert: A's anchor sits at the 2.5000 mm midpoint of its 1.0 to 4.0 mm
    # range, 2.1000 mm above its 0.4000 mm trigger plane, and B's sits at the
    # 2.8000 mm midpoint of its 1.3 to 4.3 mm range, 2.1000 mm above its
    # 0.7000 mm trigger plane. Both trigger planes come back as the values
    # they were built from, which pins the sign of the height, and the
    # reported Z offset is the seeded 0.3000 mm.
    assert height_a == pytest.approx(2.1000, abs=1e-9)
    assert height_b == pytest.approx(2.1000, abs=1e-9)
    assert trigger_a == pytest.approx(0.4000, abs=1e-9)
    assert trigger_b == pytest.approx(0.7000, abs=1e-9)
    assert trigger_b - trigger_a == pytest.approx(0.3000, abs=1e-9)


def test_reports_the_seeded_trigger_delta_for_two_different_hotends():
    # Arrange: tool A as above, and tool B whose response is shifted 0.3000 mm
    # in height and reads 10 Hz lower everywhere, the way a different nozzle
    # material would read, while its switch trigger sits 0.5000 mm above A's.
    # The seeded truth is that B's nozzle sits 0.5000 mm above A's, and the
    # curve shift is deliberately not that number.
    curve_a = etc.build_z_curve([(1.0, 100.0), (2.0, 80.0), (4.0, 40.0)])
    curve_b = etc.build_z_curve([(1.3, 90.0), (2.3, 70.0), (4.3, 30.0)])

    # Act
    height_a, freq_a = etc.switch_anchor(curve_a, 0.4000)
    height_b, freq_b = etc.switch_anchor(curve_b, 0.9000)
    trigger_a, _ = etc.trigger_plane_from_anchor(curve_a, height_a, freq_a)
    trigger_b, _ = etc.trigger_plane_from_anchor(curve_b, height_b, freq_b)

    # Assert: B's anchor sits at the 2.8 mm midpoint of its own range, where
    # its curve reads 60 Hz, 1.9000 mm above its 0.9000 mm trigger plane. The
    # reported offset is the seeded 0.5000 mm and not the 0.3000 mm the curve
    # moved by.
    assert height_b == pytest.approx(1.9000, abs=1e-9)
    assert freq_b == pytest.approx(60.0, abs=1e-9)
    assert trigger_b - trigger_a == pytest.approx(0.5000, abs=1e-9)


# --- persisted state -------------------------------------------------------


def _anchor_record():
    # sensor_clock is the crab board's 24 MHz LDC1612 reference clock in Hz
    # and drive_current the LDC1612 register value, both as the firmware
    # reports them at the moment the anchor is measured.
    return {
        'anchor_height': 4.2130,
        'anchor_frequency': 12345678.0,
        'setpoint_temperature': 150.0,
        'sensor_clock': 24000000.0,
        'drive_current': 15.0,
        'observed_temperature': 149.7,
        'trigger_z': 1.2340,
        'curve_low_z': 0.5,
        'curve_high_z': 5.0,
        'center_x': 349.8721,
        'center_y': 5.0413,
        'updated': '2026-08-01T14:03:22',
    }


def test_state_survives_a_write_and_read_round_trip():
    # Arrange
    anchors = {0: _anchor_record(), 3: _anchor_record()}

    # Act
    decoded = etc.decode_state(etc.encode_state(anchors))

    # Assert
    assert decoded == anchors


def test_a_truncated_state_file_is_rejected():
    with pytest.raises(ValueError, match="not valid JSON"):
        etc.decode_state('{"version": 1, "anchors": {"0": {')


def test_a_state_file_from_an_unknown_version_is_rejected():
    with pytest.raises(ValueError, match="version 2"):
        etc.decode_state('{"version": 2, "anchors": {}}')


def test_a_state_file_with_no_version_is_rejected():
    with pytest.raises(ValueError, match="version None"):
        etc.decode_state('{"anchors": {}}')


def test_an_unknown_field_inside_an_anchor_is_ignored():
    # Arrange: a record carrying a field a later version might add.
    text = etc.encode_state({0: _anchor_record()})
    text = text.replace('"trigger_z"', '"coil_temperature": 41.5,\n"trigger_z"')

    # Act
    decoded = etc.decode_state(text)

    # Assert: the known fields come back and the unknown one is dropped.
    assert decoded == {0: _anchor_record()}


def test_the_round_trip_keeps_the_setpoint_the_anchor_was_heated_to():
    # Arrange: an anchor taken with calibration_temp at 150.0 C, the setpoint
    # a later offset run has to hold that tool at.
    anchors = {1: _anchor_record()}

    # Act
    decoded = etc.decode_state(etc.encode_state(anchors))

    # Assert
    assert decoded[1]['setpoint_temperature'] == pytest.approx(150.0, abs=1e-9)


def test_the_round_trip_keeps_the_reading_observed_while_anchoring():
    # Arrange: the heater read 149.7 C while holding that 150.0 C setpoint,
    # and the two are separate fields rather than one.
    anchors = {1: _anchor_record()}

    # Act
    decoded = etc.decode_state(etc.encode_state(anchors))

    # Assert
    assert decoded[1]['observed_temperature'] == pytest.approx(
        149.7, abs=1e-9)


def test_a_missing_anchor_field_is_rejected():
    # Arrange: a record with no anchor frequency, which the offset math reads.
    text = ('{"version": 1, "anchors": {"0": {"anchor_height": 4.2, '
            '"setpoint_temperature": 150.0, '
            '"sensor_clock": 24000000.0, "drive_current": 15, '
            '"observed_temperature": 149.7, '
            '"trigger_z": 1.0, "curve_low_z": 0.5, '
            '"curve_high_z": 5.0, "center_x": 1.0, "center_y": 2.0, '
            '"updated": "x"}}}')

    with pytest.raises(ValueError, match="missing the anchor_frequency"):
        etc.decode_state(text)


def test_an_anchor_without_a_setpoint_is_rejected():
    # Arrange: what a state file written by a build that recorded one
    # temperature looks like. The reading it carries is a sample of the
    # heater's wander, so it cannot stand in for the setpoint a later run
    # heats to, and the record is refused instead of reinterpreted.
    text = ('{"version": 1, "anchors": {"0": {"anchor_height": 4.2, '
            '"anchor_frequency": 12345678.0, "temperature": 149.7, '
            '"sensor_clock": 24000000.0, "drive_current": 15, '
            '"observed_temperature": 149.7, "trigger_z": 1.0, '
            '"curve_low_z": 0.5, "curve_high_z": 5.0, "center_x": 1.0, '
            '"center_y": 2.0, "updated": "x"}}}')

    with pytest.raises(ValueError, match="missing the setpoint_temperature"):
        etc.decode_state(text)


def test_an_anchor_without_the_reading_observed_at_anchor_time_is_rejected():
    text = ('{"version": 1, "anchors": {"0": {"anchor_height": 4.2, '
            '"anchor_frequency": 12345678.0, "setpoint_temperature": 150.0, '
            '"sensor_clock": 24000000.0, "drive_current": 15, '
            '"trigger_z": 1.0, "curve_low_z": 0.5, "curve_high_z": 5.0, '
            '"center_x": 1.0, "center_y": 2.0, "updated": "x"}}}')

    with pytest.raises(ValueError, match="missing the observed_temperature"):
        etc.decode_state(text)


def test_an_anchor_setpoint_that_is_not_a_number_is_rejected():
    text = ('{"version": 1, "anchors": {"0": {"anchor_height": 4.2, '
            '"anchor_frequency": 12345678.0, "setpoint_temperature": "hot", '
            '"sensor_clock": 24000000.0, "drive_current": 15, '
            '"observed_temperature": 149.7, "trigger_z": 1.0, '
            '"curve_low_z": 0.5, "curve_high_z": 5.0, "center_x": 1.0, '
            '"center_y": 2.0, "updated": "x"}}}')

    with pytest.raises(ValueError, match="not a number"):
        etc.decode_state(text)


def test_the_round_trip_keeps_the_sensor_settings_the_anchor_was_taken_with():
    # Arrange: an anchor measured on the crab board's 24000000.0 Hz clock,
    # with drive current 15.
    anchors = {1: _anchor_record()}

    # Act
    decoded = etc.decode_state(etc.encode_state(anchors))

    # Assert
    assert decoded[1]['sensor_clock'] == pytest.approx(24000000.0, abs=1e-9)
    assert decoded[1]['drive_current'] == pytest.approx(15.0, abs=1e-9)


def test_an_anchor_without_the_sensor_clock_is_rejected():
    text = ('{"version": 1, "anchors": {"0": {"anchor_height": 4.2, '
            '"anchor_frequency": 12345678.0, "setpoint_temperature": 150.0, '
            '"drive_current": 15, "observed_temperature": 149.7, '
            '"trigger_z": 1.0, "curve_low_z": 0.5, "curve_high_z": 5.0, '
            '"center_x": 1.0, "center_y": 2.0, "updated": "x"}}}')

    with pytest.raises(ValueError, match="missing the sensor_clock"):
        etc.decode_state(text)


def test_an_anchor_storing_the_old_frequency_conversion_field_is_rejected():
    # Arrange: a state file written by the plugin version that fingerprinted
    # anchors with the count-to-hertz conversion rather than the clock.
    text = ('{"version": 1, "anchors": {"1": {"anchor_height": 4.2, '
            '"anchor_frequency": 12345678.0, "setpoint_temperature": 150.0, '
            '"freq_conv": 0.08940696716308594, "drive_current": 15, '
            '"observed_temperature": 149.7, "trigger_z": 1.0, '
            '"curve_low_z": 0.5, "curve_high_z": 5.0, "center_x": 1.0, '
            '"center_y": 2.0, "updated": "x"}}}')

    # Act / Assert: the refusal names the earlier version and the command,
    # not the hardware, so it reads apart from a sensor that really changed.
    with pytest.raises(ValueError) as excinfo:
        etc.decode_state(text)
    message = str(excinfo.value)
    assert "earlier plugin version" in message
    assert "frequency conversion" in message
    assert "EDDY_CALIBRATE_Z T=1" in message
    assert "sensor settings" not in message


def test_an_anchor_without_the_drive_current_is_rejected():
    text = ('{"version": 1, "anchors": {"0": {"anchor_height": 4.2, '
            '"anchor_frequency": 12345678.0, "setpoint_temperature": 150.0, '
            '"sensor_clock": 24000000.0, '
            '"observed_temperature": 149.7, "trigger_z": 1.0, '
            '"curve_low_z": 0.5, "curve_high_z": 5.0, "center_x": 1.0, '
            '"center_y": 2.0, "updated": "x"}}}')

    with pytest.raises(ValueError, match="missing the drive_current"):
        etc.decode_state(text)


def test_a_sensor_clock_that_is_not_a_number_is_rejected():
    text = ('{"version": 1, "anchors": {"0": {"anchor_height": 4.2, '
            '"anchor_frequency": 12345678.0, "setpoint_temperature": 150.0, '
            '"sensor_clock": "24MHz", "drive_current": 15, '
            '"observed_temperature": 149.7, "trigger_z": 1.0, '
            '"curve_low_z": 0.5, "curve_high_z": 5.0, "center_x": 1.0, '
            '"center_y": 2.0, "updated": "x"}}}')

    with pytest.raises(ValueError, match="not a number"):
        etc.decode_state(text)


def test_a_drive_current_that_is_not_a_number_is_rejected():
    text = ('{"version": 1, "anchors": {"0": {"anchor_height": 4.2, '
            '"anchor_frequency": 12345678.0, "setpoint_temperature": 150.0, '
            '"sensor_clock": 24000000.0, "drive_current": "default", '
            '"observed_temperature": 149.7, "trigger_z": 1.0, '
            '"curve_low_z": 0.5, "curve_high_z": 5.0, "center_x": 1.0, '
            '"center_y": 2.0, "updated": "x"}}}')

    with pytest.raises(ValueError, match="not a number"):
        etc.decode_state(text)


# --- anchor validity against the live sensor settings ----------------------


def test_an_anchor_taken_with_the_live_sensor_settings_is_accepted():
    # Arrange: the record was measured on a 24000000.0 Hz clock with drive
    # current 15, and those are the settings the firmware reports now.
    anchor = _anchor_record()

    # Act
    mismatch = etc.anchor_sensor_mismatch(0, anchor, 24000000.0, 15)

    # Assert
    assert mismatch is None


def test_an_anchor_taken_at_another_drive_current_is_refused():
    # Arrange: LDC_CALIBRATE_DRIVE_CURRENT has since put 22 in the config,
    # against the 15 the anchor was measured at.
    anchor = _anchor_record()

    # Act
    mismatch = etc.anchor_sensor_mismatch(2, anchor, 24000000.0, 22)

    # Assert: the refusal names the tool, both values and the command that
    # measures the reference again.
    assert mismatch is not None
    assert "T2" in mismatch
    assert "drive current when the Z reference was measured: 15.0" in mismatch
    assert "drive current now: 22.0" in mismatch
    assert "EDDY_CALIBRATE_Z T=2" in mismatch


def test_an_anchor_taken_on_another_sensor_clock_is_refused():
    # Arrange: the anchor was measured on the crab board's 24 MHz clock, and
    # the board in use now is a 12 MHz Eddy Coil unit.
    anchor = _anchor_record()

    # Act
    mismatch = etc.anchor_sensor_mismatch(1, anchor, 12000000.0, 15)

    # Assert: the refusal names the stored and the live clock with their
    # unit, so it reads apart from a state file an earlier version wrote.
    assert mismatch is not None
    assert "T1" in mismatch
    assert ("sensor clock when the Z reference was measured: 24000000.0 Hz"
            in mismatch)
    assert "sensor clock now: 12000000.0 Hz" in mismatch
    assert "EDDY_CALIBRATE_Z T=1" in mismatch
    assert "earlier plugin version" not in mismatch


def test_a_drive_current_that_differs_by_one_step_is_refused():
    # Arrange: the smallest change the register can carry, 15 against 16.
    anchor = _anchor_record()

    # Act
    mismatch = etc.anchor_sensor_mismatch(0, anchor, 24000000.0, 16)

    # Assert
    assert mismatch is not None


# --- anchor validity against the coil center this run found ----------------


def test_an_anchor_stored_at_the_measured_center_is_accepted():
    # Arrange: the run found the coil exactly where the anchor recorded it.
    anchor = _anchor_record()

    # Act
    mismatch = etc.anchor_center_mismatch(0, anchor, 349.8721, 5.0413)

    # Assert
    assert mismatch is None


def test_a_center_inside_the_tolerance_is_accepted():
    # Arrange: the measured center sits 0.49 mm along X from the stored
    # 349.8721, inside the 0.5 mm tolerance.
    anchor = _anchor_record()

    # Act
    mismatch = etc.anchor_center_mismatch(0, anchor, 350.3621, 5.0413)

    # Assert
    assert mismatch is None


def test_a_center_beyond_the_tolerance_is_refused():
    # Arrange: the measured center sits 0.51 mm along X from the stored
    # 349.8721, beyond the 0.5 mm tolerance.
    anchor = _anchor_record()

    # Act
    mismatch = etc.anchor_center_mismatch(3, anchor, 350.3821, 5.0413)

    # Assert: the refusal names both centers, the distance and the command
    # that measures the reference again.
    assert mismatch is not None
    assert "T3" in mismatch
    assert ("coil position when the Z reference was measured: "
            "x 349.8721, y 5.0413" in mismatch)
    assert "coil position now: x 350.3821, y 5.0413" in mismatch
    assert "distance: 0.5100 mm, more than the allowed 0.5000 mm" in mismatch
    assert "EDDY_CALIBRATE_Z T=3" in mismatch


# --- file layout -----------------------------------------------------------

# The printer config directory every configured path is read against.
CONFIG_DIR = os.path.join(os.sep, 'home', 'pi', 'printer_data', 'config')


def test_rejects_a_csv_dir_that_would_hold_the_state_file():
    with pytest.raises(ValueError, match="calibration state file"):
        etc.validate_data_dir(CONFIG_DIR, 'csv_dir', 'EddyToolCalibration')


def test_rejects_a_log_dir_that_would_hold_the_state_file():
    with pytest.raises(ValueError, match="calibration state file"):
        etc.validate_data_dir(CONFIG_DIR, 'log_dir', 'EddyToolCalibration')


def test_accepts_the_default_csv_subdirectory():
    assert etc.validate_data_dir(
        CONFIG_DIR, 'csv_dir', 'EddyToolCalibration/data') is None


def test_rejects_a_log_dir_the_scan_dumps_are_cleared_out_of():
    with pytest.raises(ValueError, match="directory csv_dir names"):
        etc.validate_log_dir(
            CONFIG_DIR, 'EddyToolCalibration/data',
            'EddyToolCalibration/data')


def test_accepts_the_default_log_and_dump_directories_side_by_side():
    assert etc.validate_log_dir(
        CONFIG_DIR, 'EddyToolCalibration/logs',
        'EddyToolCalibration/data') is None


def test_a_relative_configured_path_lands_under_the_config_directory():
    # Oracle: the state file lives in
    # /home/pi/printer_data/config/EddyToolCalibration, spelled out here
    # rather than read back off the helper.
    assert etc.resolve_dir(CONFIG_DIR, 'EddyToolCalibration/data') == (
        os.path.join(
            os.sep, 'home', 'pi', 'printer_data', 'config',
            'EddyToolCalibration', 'data'))


def test_an_absolute_configured_path_names_itself():
    absolute = os.path.join(os.sep, 'var', 'lib', 'eddy')

    assert etc.resolve_dir(CONFIG_DIR, absolute) == absolute


def test_rejects_an_absolute_csv_dir_spelling_the_state_directory():
    # The same directory written out in full rather than relative to the
    # config directory: the scan dumps would land on the state file.
    absolute = os.path.join(CONFIG_DIR, 'EddyToolCalibration')

    with pytest.raises(ValueError, match="calibration state file"):
        etc.validate_data_dir(CONFIG_DIR, 'csv_dir', absolute)


def test_rejects_a_log_dir_spelling_an_absolute_csv_dir():
    with pytest.raises(ValueError, match="directory csv_dir names"):
        etc.validate_log_dir(
            CONFIG_DIR, 'EddyToolCalibration/data',
            os.path.join(CONFIG_DIR, 'EddyToolCalibration', 'data'))


def test_rejects_a_csv_dir_reaching_the_state_directory_through_a_parent():
    with pytest.raises(ValueError, match="calibration state file"):
        etc.validate_data_dir(
            CONFIG_DIR, 'csv_dir', 'macros/../EddyToolCalibration')


def test_two_directories_that_differ_by_more_than_their_spelling_are_not_one():
    assert etc.same_directory(
        CONFIG_DIR, 'EddyToolCalibration/logs',
        os.path.join(CONFIG_DIR, 'EddyToolCalibration', 'data')) is False


# --- the rows every readout shows an anchor as -----------------------------


def test_an_anchor_height_is_shown_to_four_decimals():
    assert etc.anchor_height_row(4.2) == (
        "anchor height above trigger plane: 4.2000 mm")


def test_an_anchor_frequency_is_shown_to_three_decimals():
    # Arrange / Act: 12345678.5678 Hz rounds to 12345678.568 Hz at three
    # decimals.
    row = etc.anchor_frequency_row(12345678.5678)

    assert row == "anchor frequency: 12345678.568 Hz"


def test_a_record_missing_a_field_names_it():
    with pytest.raises(ValueError, match="T3 is missing the trigger_z field"):
        etc.require_anchor_field(3, {'anchor_height': 4.2}, 'trigger_z')


def test_an_anchor_is_shown_height_first_and_frequency_second():
    # Every readout that shows an anchor shows the pair in this order.
    assert etc.anchor_rows(_anchor_record()) == [
        "anchor height above trigger plane: 4.2130 mm",
        "anchor frequency: 12345678.000 Hz",
    ]


# --- the refusal a missing reference is reported with ----------------------


def test_the_missing_reference_message_names_the_command_for_each_tool():
    assert etc.missing_anchor_message([1, 3]) == (
        "Run EDDY_CALIBRATE_Z T=1, EDDY_CALIBRATE_Z T=3 first, mounting each "
        "of those tools in turn. The Z reference for T1, T3 is missing, and "
        "calibrate_z is True, so a Z offset cannot be measured without it.")


def test_the_missing_reference_message_names_a_single_tool_on_its_own():
    assert etc.missing_anchor_message([2]) == (
        "Run EDDY_CALIBRATE_Z T=2 first, mounting each of those tools in "
        "turn. The Z reference for T2 is missing, and calibrate_z is True, "
        "so a Z offset cannot be measured without it.")


def test_a_missing_reference_message_naming_no_tool_is_rejected():
    with pytest.raises(ValueError, match="at least one tool number"):
        etc.missing_anchor_message([])


# --- the anchors a macro reads through the status document -----------------


def test_the_status_document_keys_every_anchor_by_its_tool_number():
    document = etc.status_document(
        {2: _anchor_record()}, {}, None, 0, 0, None, 4, True)

    assert document['anchors'] == {
        '2': {
            'anchor_height': 4.2130,
            'anchor_frequency': 12345678.0,
            'setpoint_temperature': 150.0,
            'observed_temperature': 149.7,
            'trigger_z': 1.2340,
            'updated': '2026-08-01T14:03:22',
        },
    }


def test_the_status_document_names_an_incomplete_anchor_by_its_tool():
    record = _anchor_record()
    del record['updated']

    with pytest.raises(ValueError, match="T2 is missing the updated field"):
        etc.status_document({2: record}, {}, None, 0, 0, None, 4, True)
