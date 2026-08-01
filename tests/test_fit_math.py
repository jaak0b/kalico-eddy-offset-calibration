"""Unit tests for the framework-agnostic fit and geometry math.

Every expected value below is either the literal seed a synthetic fixture was
built from, or a value hand-calculated once outside the test with its
derivation cited beside the literal. No expectation is computed by the
production code.

Fixture geometry used by the response-curve tests: 401 samples spanning
x = 0.00 to 4.00 mm at a 0.01 mm sample spacing, y held at 0, a bell-shaped
response of width 0.3 mm, and a fit window of 100 samples either side with a
Gaussian weight sigma of 50 samples.
"""

import math
import random

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import eddy_tool_calibration as etc

SAMPLE_STEP = 0.01
SAMPLE_COUNT = 401
RESPONSE_WIDTH = 0.3
HALF_WINDOW = 100
SIGMA = 50.0
EDGE_MARGIN = 0.15
VERTEX_LIMIT = 0.5


def build_response(center, amplitude, baseline, noise=0.0, seed=0):
    """Synthetic bell response centered on a known position (the oracle)."""
    rng = random.Random(seed)
    xs = [i * SAMPLE_STEP for i in range(SAMPLE_COUNT)]
    ys = [0.0] * SAMPLE_COUNT
    freqs = []
    for x in xs:
        gap = x - center
        value = baseline + amplitude * math.exp(
            -(gap * gap) / (2.0 * RESPONSE_WIDTH * RESPONSE_WIDTH))
        if noise:
            value += rng.gauss(0.0, noise)
        freqs.append(value)
    return xs, ys, freqs


# --- seed recovery ---------------------------------------------------------


def test_recovers_the_center_of_a_noiseless_peak():
    # Arrange: response peaking at exactly x = 2.375 mm.
    xs, ys, freqs = build_response(2.375, 50000.0, 10000000.0)

    # Act
    result = etc.fit_scan_pass(
        xs, ys, freqs, HALF_WINDOW, SIGMA, EDGE_MARGIN, VERTEX_LIMIT)

    # Assert: 2.375 mm is the seed the fixture was generated from. It sits
    # exactly between two samples, the worst case for discretization, so the
    # tolerance is one fifth of the 0.01 mm sample spacing.
    assert result['peak_type'] == 'peak'
    assert result['peak_x'] == pytest.approx(2.375, abs=0.002)


def test_recovers_the_center_of_a_noiseless_valley():
    # Arrange: same seed center, response inverted into a valley.
    xs, ys, freqs = build_response(2.375, -50000.0, 10000000.0)

    # Act
    result = etc.fit_scan_pass(
        xs, ys, freqs, HALF_WINDOW, SIGMA, EDGE_MARGIN, VERTEX_LIMIT)

    # Assert: seed center 2.375, tolerance as above.
    assert result['peak_type'] == 'valley'
    assert result['peak_x'] == pytest.approx(2.375, abs=0.002)


def test_recovers_the_center_of_a_noisy_peak():
    # Arrange: 50 Hz Gaussian sensor noise on a 50 kHz response, fixed seed.
    xs, ys, freqs = build_response(
        2.375, 50000.0, 10000000.0, noise=50.0, seed=1234)

    # Act
    result = etc.fit_scan_pass(
        xs, ys, freqs, HALF_WINDOW, SIGMA, EDGE_MARGIN, VERTEX_LIMIT)

    # Assert: seed center 2.375. At a noise-to-amplitude ratio of 1:1000 the
    # tolerance is half the 0.01 mm sample spacing.
    assert result['peak_x'] == pytest.approx(2.375, abs=0.005)


def test_recovers_the_center_of_a_noisy_valley():
    # Arrange
    xs, ys, freqs = build_response(
        2.375, -50000.0, 10000000.0, noise=50.0, seed=1234)

    # Act
    result = etc.fit_scan_pass(
        xs, ys, freqs, HALF_WINDOW, SIGMA, EDGE_MARGIN, VERTEX_LIMIT)

    # Assert: seed center 2.375, tolerance as in the noisy peak case.
    assert result['peak_x'] == pytest.approx(2.375, abs=0.005)


def test_mirroring_the_response_reflects_the_recovered_center():
    # Arrange: reversing the sample order mirrors the response about the
    # midpoint of the 0.00 to 4.00 mm span, so a peak at 2.375 mm lands at
    # 4.000 - 2.375 = 1.625 mm.
    xs, ys, freqs = build_response(2.375, 50000.0, 10000000.0)
    mirrored = list(reversed(freqs))

    # Act
    result = etc.fit_scan_pass(
        xs, ys, mirrored, HALF_WINDOW, SIGMA, EDGE_MARGIN, VERTEX_LIMIT)

    # Assert
    assert result['peak_x'] == pytest.approx(1.625, abs=0.002)


@settings(deadline=None, max_examples=25)
@given(amplitude=st.floats(min_value=1000.0, max_value=1000000.0),
       baseline=st.floats(min_value=1000000.0, max_value=50000000.0))
def test_recovered_center_is_invariant_under_amplitude_and_offset(
        amplitude, baseline):
    # Arrange: the same seed center under any response amplitude and any
    # constant frequency offset.
    xs, ys, freqs = build_response(2.375, amplitude, baseline)

    # Act
    result = etc.fit_scan_pass(
        xs, ys, freqs, HALF_WINDOW, SIGMA, EDGE_MARGIN, VERTEX_LIMIT)

    # Assert: seed center 2.375, tolerance as in the noiseless case.
    assert result['peak_x'] == pytest.approx(2.375, abs=0.002)


# --- rejection paths -------------------------------------------------------


def test_rejects_a_pass_with_too_few_samples():
    with pytest.raises(ValueError, match="at least 3 samples"):
        etc.fit_scan_pass(
            [0.0, 0.1], [0.0, 0.0], [1.0, 2.0], HALF_WINDOW, SIGMA,
            EDGE_MARGIN, VERTEX_LIMIT)


def test_rejects_an_extremum_on_the_edge_of_the_search_window():
    # Arrange: 100 samples, a 15% edge margin, so the search window starts at
    # sample 15. The single high sample sits exactly there.
    freqs = [0.0] * 100
    freqs[15] = 1.0

    # Act / Assert
    with pytest.raises(ValueError, match="edge of the search window"):
        etc.find_extremum_index(freqs, 'peak', EDGE_MARGIN)


def test_rejects_a_fitted_vertex_outside_the_window():
    # Arrange: a downward parabola whose true vertex sits at sample 60, fitted
    # in a 10 sample window around sample 20. The vertex is 40 samples away,
    # past the 5 sample limit (10 * 0.5).
    freqs = [-((i - 60.0) ** 2) for i in range(41)]

    # Act / Assert
    with pytest.raises(ValueError, match="past the"):
        etc.fit_vertex_offset(freqs, 20, 10, 5.0, 'peak', VERTEX_LIMIT)


def test_rejects_a_fit_whose_curvature_contradicts_the_peak_type():
    # Arrange: an upward parabola presented as a peak.
    freqs = [(i - 20.0) ** 2 for i in range(41)]

    # Act / Assert
    with pytest.raises(ValueError, match="opens upward"):
        etc.fit_vertex_offset(freqs, 20, 10, 5.0, 'peak', VERTEX_LIMIT)


def test_rejects_an_unknown_peak_type():
    with pytest.raises(ValueError, match="unhandled peak type"):
        etc.find_extremum_index([1.0, 2.0, 1.0] * 40, 'plateau', EDGE_MARGIN)


def test_rejects_a_flat_fit_window():
    freqs = [1000.0] * 41

    with pytest.raises(ValueError, match="no curvature"):
        etc.fit_vertex_offset(freqs, 20, 10, 5.0, 'peak', VERTEX_LIMIT)


def test_recovers_the_vertex_when_the_fit_window_is_clipped_at_the_pass_start():
    # Arrange: an exact downward parabola with its vertex seeded at sample
    # 6.4, fitted around sample 3 with a 10 sample half window. The window is
    # clipped by the start of the pass and so covers samples 0 to 13, which is
    # asymmetric about sample 3 (7 samples left of it are missing).
    freqs = [-((i - 6.4) ** 2) for i in range(41)]

    # Act
    offset = etc.fit_vertex_offset(freqs, 3, 10, 5.0, 'peak', VERTEX_LIMIT)

    # Assert: the seeded vertex sits 6.4 - 3 = 3.4 samples past the fit index.
    # The data is an exact quadratic, so a correct solve of the normal
    # equations returns it to floating-point round-off; the tolerance is a
    # thousand times that round-off. Upstream's determinant expansion, which
    # is only valid for a symmetric window, returns roughly 7.84 here.
    assert offset == pytest.approx(3.4, abs=1e-12)


def test_recovers_the_vertex_when_the_fit_window_is_symmetric():
    # Arrange: the same exact parabola with its vertex seeded at sample 20.25,
    # fitted around sample 20 so the 10 sample half window is symmetric.
    freqs = [-((i - 20.25) ** 2) for i in range(41)]

    # Act
    offset = etc.fit_vertex_offset(freqs, 20, 10, 5.0, 'peak', VERTEX_LIMIT)

    # Assert: the seeded vertex sits 20.25 - 20 = 0.25 samples past the fit
    # index, recovered to floating-point round-off as above.
    assert offset == pytest.approx(0.25, abs=1e-12)


def test_rejects_a_pass_with_no_response_contrast():
    # Arrange: a flat pass, so the middle band and the edges read the same and
    # the response has neither a peak nor a valley.
    freqs = [12345.0] * 100

    # Act / Assert
    with pytest.raises(ValueError, match="no response contrast"):
        etc.detect_peak_type(freqs, EDGE_MARGIN)


# --- scan angle normalization ----------------------------------------------


def test_normalizes_scan_angles_into_a_single_turn():
    # Arrange / Act
    angles = etc.normalize_scan_angles([-45.0, 405.0], pair_scans=False)

    # Assert: -45 wraps to 315 and 405 wraps to 45.
    assert angles == [315.0, 45.0]


def test_rejects_a_scan_angle_listed_twice_in_different_turns():
    with pytest.raises(ValueError, match="listed twice"):
        etc.normalize_scan_angles([45.0, 405.0], pair_scans=False)


def test_rejects_opposite_scan_angles_when_pairing_already_adds_them():
    with pytest.raises(ValueError, match="are opposites"):
        etc.normalize_scan_angles([45.0, 225.0], pair_scans=True)


def test_keeps_opposite_scan_angles_when_pairing_is_off():
    # Arrange / Act: without pair_scans nothing adds the opposites, so an
    # explicit opposed pair is a legitimate configuration.
    angles = etc.normalize_scan_angles([45.0, 225.0], pair_scans=False)

    # Assert
    assert angles == [45.0, 225.0]


def test_pairing_expands_each_angle_with_its_opposite():
    # Arrange / Act
    angles = etc.expand_scan_angles([45.0, 135.0], pair_scans=True)

    # Assert: the opposite of 45 is 225 and the opposite of 135 is 315.
    assert angles == [45.0, 135.0, 225.0, 315.0]


# --- Z descent targets -----------------------------------------------------


def test_descent_targets_end_exactly_at_z_stop():
    # Arrange / Act: a 5.0 mm to 4.5 mm descent in 0.1 mm steps.
    targets = etc.z_descent_targets(5.0, 4.5, 0.1)

    # Assert: six heights, the last one exactly z_stop.
    assert len(targets) == 6
    assert targets[0] == pytest.approx(5.0, abs=1e-12)
    assert targets[-1] == 4.5


def test_rejects_a_descent_span_that_is_not_a_whole_number_of_steps():
    with pytest.raises(ValueError, match="whole number"):
        etc.z_descent_targets(5.0, 0.5, 0.04)


def test_rejects_a_descent_whose_stop_lies_above_its_start():
    with pytest.raises(ValueError, match="must lie below"):
        etc.z_descent_targets(0.5, 5.0, 0.05)


# --- vertical geometry convention ------------------------------------------
#
# scan_height, z_start and z_stop are heights above the coil top face, so
# 0 mm is the nozzle touching the face. Every literal below is a position
# relative to that face, read off the convention itself, not off the code.


def test_accepts_a_scan_plane_and_descent_that_clear_the_coil_top_face():
    # Arrange / Act: the shipped defaults, scanning 1.0 mm above the face and
    # descending from 5.0 mm to 0.5 mm above it.
    # Assert: a valid geometry raises nothing, so reaching the next line is
    # the assertion.
    etc.validate_vertical_geometry(1.0, 5.0, 0.5)


def test_rejects_a_descent_that_stops_at_the_coil_top_face():
    # 0.0 mm is the nozzle touching the face, so a descent ending there is a
    # collision rather than a measurement.
    with pytest.raises(ValueError, match="z_stop"):
        etc.validate_vertical_geometry(1.0, 5.0, 0.0)


def test_rejects_a_descent_that_stops_below_the_coil_top_face():
    # A negative height is inside the coil.
    with pytest.raises(ValueError, match="z_stop"):
        etc.validate_vertical_geometry(1.0, 5.0, -0.5)


def test_rejects_a_scan_plane_at_the_coil_top_face():
    with pytest.raises(
            ValueError, match="scan_height .* not above the coil top face"):
        etc.validate_vertical_geometry(0.0, 5.0, 0.5)


def test_rejects_a_scan_plane_below_the_coil_top_face():
    with pytest.raises(
            ValueError, match="scan_height .* not above the coil top face"):
        etc.validate_vertical_geometry(-1.0, 5.0, 0.5)


def test_rejects_a_scan_plane_level_with_the_descent_start():
    # The scan plane has to sit inside the descent range, so 5.0 mm is not a
    # valid scan height for a descent that starts at 5.0 mm.
    with pytest.raises(ValueError, match="must lie below z_start"):
        etc.validate_vertical_geometry(5.0, 5.0, 0.5)


def test_rejects_a_scan_plane_above_the_descent_start():
    with pytest.raises(ValueError, match="must lie below z_start"):
        etc.validate_vertical_geometry(6.0, 5.0, 0.5)


# --- pair averaging --------------------------------------------------------


def test_pair_averaging_cancels_a_constant_latency_shift():
    # Arrange: true center (10.0, 20.0). A constant transport latency shifts
    # every fitted peak 0.05 mm along its own direction of travel. The 45
    # degree unit vector is (0.7071067811865476, 0.7071067811865476), so the
    # shift is 0.05 * 0.7071067811865476 = 0.035355339059327376 mm per axis.
    peaks = [
        (45.0, 10.035355339059327, 20.035355339059327),
        (225.0, 9.964644660940673, 19.964644660940673),
        (135.0, 9.964644660940673, 20.035355339059327),
        (315.0, 10.035355339059327, 19.964644660940673),
    ]

    # Act
    tx, ty = etc.solve_center_lsq(etc.average_paired_projections(peaks))

    # Assert: the constructed true center, with the latency cancelled.
    assert tx == pytest.approx(10.0, abs=1e-9)
    assert ty == pytest.approx(20.0, abs=1e-9)


def test_unpaired_projection_keeps_the_latency_shift():
    # Arrange: the same 45 degree pass without its reverse partner.
    peaks = [
        (45.0, 10.035355339059327, 20.035355339059327),
        (135.0, 9.964644660940673, 20.035355339059327),
    ]

    # Act
    tx, ty = etc.solve_center_lsq(etc.average_paired_projections(peaks))

    # Assert: with no pair to cancel it, the 0.05 mm travel-direction shift
    # survives: the 45 degree pass lands 0.035355339059327376 mm past the
    # center on both axes and the 135 degree pass lands the same distance past
    # it along its own axis, giving (10.0, 20.070710678118655).
    assert tx == pytest.approx(10.0, abs=1e-9)
    assert ty == pytest.approx(20.070710678118655, abs=1e-9)


# --- center reconstruction -------------------------------------------------


def test_reconstructs_a_center_from_the_default_45_and_135_passes():
    # Arrange: true center (3.0, -2.0). Projection onto the 45 degree axis is
    # (3.0 - 2.0) * 0.7071067811865476 = 0.7071067811865476; onto the 135
    # degree axis it is (-3.0 - 2.0) * 0.7071067811865476 = -3.535533905932738.
    projections = [(45.0, 0.7071067811865476), (135.0, -3.535533905932738)]

    # Act
    tx, ty = etc.solve_center_lsq(projections)

    # Assert
    assert tx == pytest.approx(3.0, abs=1e-9)
    assert ty == pytest.approx(-2.0, abs=1e-9)


def test_reconstructs_a_center_from_three_scan_angles():
    # Arrange: true center (5.0, 1.0). Projections onto the 0, 60 and 120
    # degree axes, with cos 60 = 0.5 and sin 60 = 0.8660254037844386:
    #   0 deg:   5.0
    #   60 deg:  5.0 * 0.5 + 1.0 * 0.8660254037844386 = 3.3660254037844386
    #   120 deg: 5.0 * -0.5 + 1.0 * 0.8660254037844386 = -1.6339745962155614
    projections = [
        (0.0, 5.0),
        (60.0, 3.3660254037844386),
        (120.0, -1.6339745962155614),
    ]

    # Act
    tx, ty = etc.solve_center_lsq(projections)

    # Assert
    assert tx == pytest.approx(5.0, abs=1e-9)
    assert ty == pytest.approx(1.0, abs=1e-9)


def test_rejects_scan_angles_that_share_a_single_axis():
    # Arrange: two passes along the same 45 degree axis constrain one
    # direction only.
    projections = [(45.0, 1.0), (225.0, -1.0)]

    # Act / Assert
    with pytest.raises(ValueError, match="cannot reconstruct both axes"):
        etc.solve_center_lsq(projections)


def test_projects_each_pass_onto_its_own_axis():
    # Arrange: a peak at (3.0, -2.0) found by a 45 degree pass projects to
    # (3.0 - 2.0) * 0.7071067811865476 = 0.7071067811865476.
    peaks = [(45.0, 3.0, -2.0)]

    # Act
    projections = etc.project_peaks(peaks)

    # Assert
    assert projections[0][0] == 45.0
    assert projections[0][1] == pytest.approx(0.7071067811865476, abs=1e-12)


# --- scan geometry ---------------------------------------------------------


def test_scan_endpoints_along_x():
    # Arrange / Act
    start_x, start_y, end_x, end_y = etc.scan_endpoints(10.0, 20.0, 0.0, 4.0)

    # Assert: a 4 mm pass through (10, 20) along X+ runs 8 to 12 at y = 20.
    assert (start_x, start_y) == pytest.approx((8.0, 20.0), abs=1e-9)
    assert (end_x, end_y) == pytest.approx((12.0, 20.0), abs=1e-9)


def test_scan_endpoints_along_y():
    # Arrange / Act
    start_x, start_y, end_x, end_y = etc.scan_endpoints(10.0, 20.0, 90.0, 4.0)

    # Assert: a 4 mm pass through (10, 20) along Y+ runs 18 to 22 at x = 10.
    assert (start_x, start_y) == pytest.approx((10.0, 18.0), abs=1e-9)
    assert (end_x, end_y) == pytest.approx((10.0, 22.0), abs=1e-9)


def test_scan_endpoints_at_45_degrees():
    # Arrange / Act
    start_x, start_y, end_x, end_y = etc.scan_endpoints(10.0, 20.0, 45.0, 4.0)

    # Assert: half the 4 mm length along the 45 degree unit vector is
    # 2.0 * 0.7071067811865476 = 1.4142135623730951 mm on each axis.
    assert (start_x, start_y) == pytest.approx(
        (8.585786437626905, 18.585786437626905), abs=1e-9)
    assert (end_x, end_y) == pytest.approx(
        (11.414213562373095, 21.414213562373095), abs=1e-9)


def test_rejects_a_scan_length_of_zero():
    with pytest.raises(ValueError, match="scan length"):
        etc.scan_endpoints(10.0, 20.0, 45.0, 0.0)


# --- Z curve ---------------------------------------------------------------


def test_z_curve_crossing_interpolates_between_two_steps():
    # Arrange: a descent curve reading 100 Hz at 1.0 mm, 80 Hz at 2.0 mm and
    # 60 Hz at 3.0 mm.
    curve = etc.build_z_curve([(1.0, 100.0), (2.0, 80.0), (3.0, 60.0)])

    # Act
    z = etc.z_curve_z_at_freq(curve, 90.0)

    # Assert: 90 Hz sits halfway between the 100 Hz and 80 Hz steps, which are
    # 1.0 mm apart, so the crossing is at 1.0 + 0.5 = 1.5 mm.
    assert z == pytest.approx(1.5, abs=1e-9)


def test_z_curve_frequency_at_a_height_interpolates_between_two_steps():
    # Arrange: the same curve.
    curve = etc.build_z_curve([(1.0, 100.0), (2.0, 80.0), (3.0, 60.0)])

    # Act
    freq = etc.z_curve_freq_at(curve, 2.5)

    # Assert: 2.5 mm sits halfway between the 80 Hz and 60 Hz steps, so the
    # frequency there is 70 Hz.
    assert freq == pytest.approx(70.0, abs=1e-9)


def test_z_curve_returns_the_endpoints_of_the_measured_range():
    # Arrange
    curve = etc.build_z_curve([(1.0, 100.0), (2.0, 80.0), (3.0, 60.0)])

    # Assert: sorted by ascending height, frequency falling as the nozzle
    # rises.
    assert curve[0] == (1.0, 100.0)
    assert curve[-1] == (3.0, 60.0)


def test_rejects_a_z_curve_whose_frequency_does_not_rise_every_step_down():
    with pytest.raises(ValueError, match="does not increase at every step"):
        etc.build_z_curve([(1.0, 100.0), (2.0, 110.0), (3.0, 60.0)])


def test_rejects_a_z_curve_with_too_few_steps():
    with pytest.raises(ValueError, match="at least 3 steps"):
        etc.build_z_curve([(1.0, 100.0), (2.0, 80.0)])


def test_rejects_a_reference_frequency_outside_the_measured_curve():
    curve = etc.build_z_curve([(1.0, 100.0), (2.0, 80.0), (3.0, 60.0)])

    with pytest.raises(ValueError, match="outside the measured Z curve"):
        etc.z_curve_z_at_freq(curve, 120.0)


def test_rejects_a_height_outside_the_measured_curve():
    curve = etc.build_z_curve([(1.0, 100.0), (2.0, 80.0), (3.0, 60.0)])

    with pytest.raises(ValueError, match="outside the measured Z curve"):
        etc.z_curve_freq_at(curve, 0.5)


# --- fit window sizing -----------------------------------------------------


def test_fit_half_window_covers_the_window_radius_in_samples():
    # Arrange / Act: 250 samples per second at 5 mm/s gives 50 samples per mm,
    # so a 1.0 mm window radius is 50 samples.
    assert etc.fit_half_window_samples(250.0, 5.0, 1.0) == 50


def test_rejects_a_scan_speed_of_zero_when_sizing_the_fit_window():
    with pytest.raises(ValueError, match="scan speed"):
        etc.fit_half_window_samples(250.0, 0.0, 1.0)
