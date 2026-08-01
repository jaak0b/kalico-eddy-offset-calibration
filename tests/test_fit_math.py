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


# --- weighted quadratic solve ----------------------------------------------


def test_solves_a_hand_worked_quadratic_from_three_unit_weight_samples():
    # Arrange: y = 2x^2 - 3x + 5 sampled at x = -1, 0 and 1 with weight 1, so
    # y reads 10, 5 and 4. The power sums of those three samples, worked out
    # by hand: w = 3, wx = 0, wx2 = 2, wx3 = 0, wx4 = 2,
    # wy = 10 + 5 + 4 = 19, wxy = -10 + 0 + 4 = -6, wx2y = 10 + 0 + 4 = 14.

    # Act
    a, b, c = etc.solve_weighted_quadratic(
        3.0, 0.0, 2.0, 0.0, 2.0, 19.0, -6.0, 14.0)

    # Assert: eliminating by hand, the middle equation 2b = -6 gives b = -3,
    # and 2a + 2c = 14 with 2a + 3c = 19 give c = 5 and a = 2, the parabola
    # the samples were generated from. Every entry is exact in binary floating
    # point, so the solve is exact to round-off and the tolerance is a
    # thousand times that round-off.
    assert a == pytest.approx(2.0, abs=1e-12)
    assert b == pytest.approx(-3.0, abs=1e-12)
    assert c == pytest.approx(5.0, abs=1e-12)


def test_solves_a_hand_worked_quadratic_from_asymmetric_weighted_samples():
    # Arrange: y = x^2 - 4x + 7 sampled at x = 0, 1 and 2 with weights 1, 2
    # and 3, so y reads 7, 4 and 3. Every sample sits on one side of the
    # origin, the shape a fit window clipped by the start of a pass has. The
    # power sums, worked out by hand: w = 1 + 2 + 3 = 6, wx = 0 + 2 + 6 = 8,
    # wx2 = 0 + 2 + 12 = 14, wx3 = 0 + 2 + 24 = 26, wx4 = 0 + 2 + 48 = 50,
    # wy = 7 + 8 + 9 = 24, wxy = 0 + 8 + 18 = 26, wx2y = 0 + 8 + 36 = 44.

    # Act
    a, b, c = etc.solve_weighted_quadratic(
        6.0, 8.0, 14.0, 26.0, 50.0, 24.0, 26.0, 44.0)

    # Assert: three samples at three distinct x lie on exactly one parabola,
    # and an exact fit returns it under any positive weighting, so the seeded
    # coefficients 1, -4 and 7 come back whatever the weights are.
    assert a == pytest.approx(1.0, abs=1e-12)
    assert b == pytest.approx(-4.0, abs=1e-12)
    assert c == pytest.approx(7.0, abs=1e-12)


def test_solves_a_hand_worked_least_squares_fit_no_parabola_passes_through():
    # Arrange: five unit-weight samples at x = -2, -1, 0, 1 and 2 reading
    # y = 0, 0, 1, 0 and 0, which no parabola passes through, so the solve is
    # a genuine least-squares compromise rather than an interpolation. The
    # power sums, worked out by hand: w = 5, wx = 0, wx2 = 10, wx3 = 0,
    # wx4 = 34, wy = 1, wxy = 0, wx2y = 0.

    # Act
    a, b, c = etc.solve_weighted_quadratic(
        5.0, 0.0, 10.0, 0.0, 34.0, 1.0, 0.0, 0.0)

    # Assert: the normal equations reduce by hand to 10b = 0, so b = 0, and
    # 34a + 10c = 0 with 10a + 5c = 1 give a = -1/7 and c = 17/35, written
    # below as their decimal expansions.
    assert a == pytest.approx(-0.14285714285714285, abs=1e-12)
    assert b == pytest.approx(0.0, abs=1e-12)
    assert c == pytest.approx(0.4857142857142857, abs=1e-12)


def test_rejects_normal_equations_that_have_no_single_solution():
    # Arrange: three samples that all sit at the same x, which leaves every
    # x moment zero (w = 3, wx = wx2 = wx3 = wx4 = 0) and no parabola
    # determined. Their y sums are wy = 12, wxy = 0, wx2y = 0.

    # Act / Assert
    with pytest.raises(ValueError, match="singular"):
        etc.solve_weighted_quadratic(
            3.0, 0.0, 0.0, 0.0, 0.0, 12.0, 0.0, 0.0)


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
    # descending from 2.5 mm to 0.5 mm above it.
    # Assert: a valid geometry raises nothing, so reaching the next line is
    # the assertion.
    etc.validate_vertical_geometry(1.0, 2.5, 0.5)


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


# --- descent sample bucketing ----------------------------------------------


def test_each_descent_step_keeps_the_samples_taken_inside_its_own_window():
    # Arrange: two descent steps, the first sampling from 1.00 s to 1.10 s at
    # a measured height of 0.500 mm and the second from 1.30 s to 1.40 s at
    # 0.400 mm. Two samples land in the first window and one in the second;
    # the other three arrive before, between and after the windows, while the
    # toolhead is moving.
    windows = [(1.00, 1.10, 0.500), (1.30, 1.40, 0.400)]
    samples = [(0.90, 100.0), (1.05, 110.0), (1.09, 120.0),
               (1.20, 130.0), (1.35, 140.0), (1.90, 150.0)]

    # Act
    buckets, dropped = etc.bucket_samples_by_window(windows, samples)

    # Assert
    assert buckets == {0.500: [110.0, 120.0], 0.400: [140.0]}
    assert dropped == 3


def test_a_sample_taken_on_either_edge_of_a_window_belongs_to_that_step():
    # Arrange: one window from 2.00 s to 2.50 s, with a sample at each edge.
    windows = [(2.00, 2.50, 0.300)]
    samples = [(2.00, 10.0), (2.50, 20.0)]

    # Act
    buckets, dropped = etc.bucket_samples_by_window(windows, samples)

    # Assert: the window is closed at both ends, so both samples count.
    assert buckets == {0.300: [10.0, 20.0]}
    assert dropped == 0


def test_a_step_that_received_no_samples_is_absent_from_the_buckets():
    # Arrange: three descent steps, the middle one sampled during a gap in
    # the sensor stream. A caller counts the buckets against the steps to see
    # that gap, so the empty step must not appear at all.
    windows = [(1.00, 1.10, 0.500), (1.30, 1.40, 0.400), (1.60, 1.70, 0.300)]
    samples = [(1.05, 111.0), (1.65, 333.0)]

    # Act
    buckets, dropped = etc.bucket_samples_by_window(windows, samples)

    # Assert
    assert buckets == {0.500: [111.0], 0.300: [333.0]}
    assert dropped == 0


def test_a_descent_that_produced_no_samples_at_all_fills_no_bucket():
    # Arrange
    windows = [(1.00, 1.10, 0.500), (1.30, 1.40, 0.400)]

    # Act
    buckets, dropped = etc.bucket_samples_by_window(windows, [])

    # Assert
    assert buckets == {}
    assert dropped == 0


# --- scan pass sample window ------------------------------------------------


def test_a_scan_pass_keeps_the_samples_taken_while_the_move_ran():
    # Arrange: a scan move running from 5.00 s to 5.60 s. Two samples arrive
    # while the toolhead still settles, three during the move, and one after
    # it has stopped.
    samples = [(4.80, 100.0), (4.95, 110.0),
               (5.10, 120.0), (5.30, 130.0), (5.50, 140.0),
               (5.80, 150.0)]

    # Act
    inside, dropped = etc.samples_in_window(5.00, 5.60, samples)

    # Assert: the samples come back whole, so the caller still holds the time
    # each frequency was read at.
    assert inside == [(5.10, 120.0), (5.30, 130.0), (5.50, 140.0)]
    assert dropped == 3


def test_a_scan_sample_taken_on_either_end_of_the_move_counts():
    # Arrange: one sample at the first moment of the move and one at the last.
    samples = [(2.00, 10.0), (2.50, 20.0)]

    # Act
    inside, dropped = etc.samples_in_window(2.00, 2.50, samples)

    # Assert
    assert inside == [(2.00, 10.0), (2.50, 20.0)]
    assert dropped == 0


def test_a_scan_pass_whose_samples_all_missed_the_move_keeps_none():
    # Arrange: every sample arrived after the move had stopped, which is what
    # a pass too short for the sensor's batch period looks like.
    samples = [(9.10, 10.0), (9.20, 20.0)]

    # Act
    inside, dropped = etc.samples_in_window(5.00, 5.60, samples)

    # Assert
    assert inside == []
    assert dropped == 2


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


def test_shared_reference_is_taken_at_the_middle_of_the_measured_range():
    # Arrange: a descent measured from 1.0 mm to 4.0 mm, reading 100 Hz at
    # 1.0 mm, 80 Hz at 2.0 mm and 40 Hz at 4.0 mm.
    curve = etc.build_z_curve([(1.0, 100.0), (2.0, 80.0), (4.0, 40.0)])

    # Act
    ref_z, ref_freq = etc.z_curve_shared_reference(curve)

    # Assert: the midpoint of 1.0 to 4.0 mm is 2.5 mm, a quarter of the way
    # from the 80 Hz step to the 40 Hz step, so the frequency there is 70 Hz.
    assert ref_z == pytest.approx(2.5, abs=1e-9)
    assert ref_freq == pytest.approx(70.0, abs=1e-9)


def test_shared_reference_recovers_a_seeded_height_shift_between_two_tools():
    # Arrange: one hotend response, and the same response measured 0.300 mm
    # higher because the second tool's nozzle sits 0.300 mm shorter. The
    # 0.300 mm is the seeded truth this test recovers.
    baseline = etc.build_z_curve([(1.0, 100.0), (2.0, 80.0), (4.0, 40.0)])
    second_tool = etc.build_z_curve([(1.3, 100.0), (2.3, 80.0), (4.3, 40.0)])

    # Act
    ref_z, ref_freq = etc.z_curve_shared_reference(baseline)
    z_cross = etc.z_curve_z_at_freq(second_tool, ref_freq)

    # Assert: the baseline reference sits at 2.5 mm and 70 Hz, and the shifted
    # curve reaches 70 Hz at 2.8 mm, which is the seeded 0.300 mm higher.
    assert ref_z == pytest.approx(2.5, abs=1e-9)
    assert z_cross == pytest.approx(2.8, abs=1e-9)


def test_rejects_a_shared_reference_from_a_single_step():
    with pytest.raises(ValueError, match="at least 2 steps"):
        etc.z_curve_shared_reference([(1.0, 100.0)])


# --- fit window sizing -----------------------------------------------------


def test_fit_half_window_covers_the_window_radius_in_samples():
    # Arrange / Act: 250 samples per second at 5 mm/s gives 50 samples per mm,
    # so a 1.0 mm window radius is 50 samples.
    assert etc.fit_half_window_samples(250.0, 5.0, 1.0) == 50


def test_rejects_a_scan_speed_of_zero_when_sizing_the_fit_window():
    with pytest.raises(ValueError, match="scan speed"):
        etc.fit_half_window_samples(250.0, 0.0, 1.0)


# --- scan length sizing ----------------------------------------------------


def test_default_scan_length_for_the_btt_eddy_coil_bore():
    # Arrange / Act: the BTT Eddy Coil's documented bore is 8.0 mm.
    length = etc.default_scan_length(8.0)

    # Assert: 8.0 mm times the 1.5 bore factor is 12.0 mm.
    assert length == pytest.approx(12.0, abs=1e-9)


def test_default_scan_length_for_the_little_crab_bore():
    # Arrange / Act: the Little Crab board's bore is 2.0 mm.
    length = etc.default_scan_length(2.0)

    # Assert: 2.0 mm times the 1.5 bore factor is 3.0 mm.
    assert length == pytest.approx(3.0, abs=1e-9)
