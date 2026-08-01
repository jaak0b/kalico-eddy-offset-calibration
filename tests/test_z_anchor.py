"""Unit tests for switch probing aggregation, Z anchoring and state storage.

Every expected value here is a literal fixed outside the implementation:
either the seed a synthetic descent curve was built from, or a value worked
out by hand from that seed and cited beside the assertion.
"""

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

    # Assert: the reported Z offset is the seeded 0.3000 mm.
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
    return {
        'anchor_height': 4.2130,
        'anchor_frequency': 12345678.0,
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
    record = _anchor_record()
    record['coil_temperature'] = 41.5
    text = etc.encode_state({0: _anchor_record()})
    text = text.replace('"trigger_z"', '"coil_temperature": 41.5,\n"trigger_z"')

    # Act
    decoded = etc.decode_state(text)

    # Assert: the known fields come back and the unknown one is dropped.
    assert decoded == {0: _anchor_record()}


def test_a_missing_anchor_field_is_rejected():
    # Arrange: a record with no anchor frequency, which the offset math reads.
    text = ('{"version": 1, "anchors": {"0": {"anchor_height": 4.2, '
            '"trigger_z": 1.0, "curve_low_z": 0.5, "curve_high_z": 5.0, '
            '"center_x": 1.0, "center_y": 2.0, "updated": "x"}}}')

    with pytest.raises(ValueError, match="missing the anchor_frequency"):
        etc.decode_state(text)


# --- file layout -----------------------------------------------------------


def test_rejects_a_csv_dir_that_would_hold_the_state_file():
    with pytest.raises(ValueError, match="calibration state file"):
        etc.validate_csv_dir('EddyToolCalibration')


def test_accepts_the_default_csv_subdirectory():
    assert etc.validate_csv_dir('EddyToolCalibration/data') is None
