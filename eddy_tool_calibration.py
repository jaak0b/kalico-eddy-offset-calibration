# Kalico plugin: per-tool XYZ nozzle offset calibration using a bed-mounted
# LDC1612 eddy-current sensor board.
#
# Ported from chengxg's tool_eddy_calibration (GPLv3), kept unmodified in
# reference/tool_eddy_calibration.py for algorithm provenance. This file is a
# derivative work and is distributed under the GNU GPLv3; see LICENSE.
#
# Copyright (C) 2026 Jakob
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the
# Free Software Foundation, either version 3 of the License, or (at your
# option) any later version. See LICENSE for the full text.

"""Kalico klippy plugin module: EDDY_QUERY, EDDY_LOCATE, EDDY_CALIBRATE_TOOL,
EDDY_SET_BASELINE and EDDY_SET_Z_REF gcode commands for eddy-current based
per-tool nozzle offset calibration. See docs/design.md for the full design
and config schema.

Constraint: this module must import cleanly on a machine without klippy
installed (unit tests run standalone). Any import of klippy modules
(klippy.extras.ldc1612, motion_report, etc.) must happen inside a function or
method body, never at module scope.
"""

import math
import os

# ---------------------------------------------------------------------------
# Framework-agnostic math.
#
# Everything in this section takes plain numbers and lists, imports nothing
# from klippy, and raises ValueError with a specific message on invalid input.
# The klippy-facing layer below is the only place that converts those into
# gcode errors. See tests/ for the standalone unit tests.
# ---------------------------------------------------------------------------

# Provenance: upstream tool_eddy_calibration drops samples below 1 MHz as
# invalid startup or noise readings. The threshold sits well below any real
# coil resonance of an LDC1612 eddy board, so it separates garbage from
# signal rather than trimming real data. Exposed as the freq_min config
# option; this is the default.
FREQ_MIN_DEFAULT = 1000000.0

# Provenance: upstream peak-type auto-detection compares the average of the
# scan edges against the average of the middle band, taken as 35% to 65% of
# the pass. The band only has to sit clear of the edges for the comparison to
# have a defined sign, so it is kept as the upstream convention.
PEAK_TYPE_CENTER_LOW_FRACTION = 0.35
PEAK_TYPE_CENTER_HIGH_FRACTION = 0.65

# Provenance: upstream treats the weighted normal equations as singular below
# 1e-10 and a fitted quadratic term below 1e-10 as flat. Both are numerical
# guards on a division, not tuning.
FIT_DET_EPSILON = 1e-10
FIT_CURVATURE_EPSILON = 1e-10

# Provenance: upstream treats the least-squares normal equations of the center
# reconstruction as singular below 1e-12. Numerical guard on a division.
LSQ_DET_EPSILON = 1e-12

PEAK_TYPES = ('peak', 'valley')


def detect_peak_type(freqs, edge_margin):
    """Return 'peak' or 'valley' for a scan pass.

    Ported from upstream's auto-detection: the response is a peak when the
    middle band of the pass reads higher than its two edges, a valley when it
    reads lower. edge_margin is the fraction of the pass treated as edge.
    """
    n = len(freqs)
    if n < 3:
        raise ValueError(
            "peak-type detection needs at least 3 samples, got %d" % (n,))
    if not 0.0 < edge_margin < 0.5:
        raise ValueError(
            "edge margin must be between 0 and 0.5, got %r" % (edge_margin,))
    edge_count = max(1, int(round(n * edge_margin)))
    edge = list(freqs[:edge_count]) + list(freqs[n - edge_count:])
    low = int(round(n * PEAK_TYPE_CENTER_LOW_FRACTION))
    high = max(low + 1, int(round(n * PEAK_TYPE_CENTER_HIGH_FRACTION)))
    center = list(freqs[low:high])
    if not center:
        raise ValueError(
            "peak-type detection found no samples in the middle band of a "
            "%d sample pass" % (n,))
    edge_avg = sum(edge) / len(edge)
    center_avg = sum(center) / len(center)
    if center_avg > edge_avg:
        return 'peak'
    if center_avg < edge_avg:
        return 'valley'
    raise ValueError(
        "the pass shows no response contrast, its middle band and its edges "
        "both average %.3f, so the scan may not cross the coil" % (edge_avg,))


def find_extremum_index(freqs, peak_type, edge_margin):
    """Return the index of the response extremum, excluding the pass edges.

    Raises ValueError when the extremum lands on the edge of the search
    window, which means the scan did not cross the coil center.
    """
    n = len(freqs)
    if n < 3:
        raise ValueError(
            "extremum search needs at least 3 samples, got %d" % (n,))
    if not 0.0 < edge_margin < 0.5:
        raise ValueError(
            "edge margin must be between 0 and 0.5, got %r" % (edge_margin,))
    margin = max(1, int(round(n * edge_margin)))
    lo = margin
    hi = n - margin
    if hi - lo < 3:
        raise ValueError(
            "extremum search window holds %d samples after trimming %d edge "
            "samples from a %d sample pass" % (max(0, hi - lo), margin, n))
    window = list(freqs[lo:hi])
    if peak_type == 'peak':
        best = max(window)
    elif peak_type == 'valley':
        best = min(window)
    else:
        raise ValueError(
            "unhandled peak type %r, expected one of %r"
            % (peak_type, PEAK_TYPES))
    best_idx = lo + window.index(best)
    if best_idx == lo or best_idx == hi - 1:
        raise ValueError(
            "response extremum lies on the edge of the search window at "
            "sample %d of %d" % (best_idx, n))
    return best_idx


def fit_vertex_offset(freqs, peak_idx, half_window, sigma, peak_type,
                      vertex_limit):
    """Gaussian-weighted quadratic least-squares fit around peak_idx.

    Ported from upstream's _refine_peak_position. Returns the vertex position
    as a fractional sample offset relative to peak_idx. Upstream clamped a
    vertex that fell outside the window; a clamp turns a failed fit into a
    plausible looking number, so this raises instead.
    """
    n = len(freqs)
    if peak_idx < 0 or peak_idx >= n:
        raise ValueError(
            "fit index %d lies outside the %d sample pass" % (peak_idx, n))
    if half_window < 1:
        raise ValueError(
            "fit half window must be at least 1 sample, got %r"
            % (half_window,))
    if sigma <= 0.0:
        raise ValueError(
            "fit sigma must be greater than 0, got %r" % (sigma,))
    if peak_type not in PEAK_TYPES:
        raise ValueError(
            "unhandled peak type %r, expected one of %r"
            % (peak_type, PEAK_TYPES))
    start_idx = max(0, peak_idx - half_window)
    end_idx = min(n, peak_idx + half_window + 1)
    if end_idx - start_idx < 3:
        raise ValueError(
            "fit window holds %d samples, the quadratic fit needs at least 3"
            % (end_idx - start_idx,))
    xs = [float(i - peak_idx) for i in range(start_idx, end_idx)]
    ys = [float(freqs[i]) for i in range(start_idx, end_idx)]
    ws = [math.exp(-(x * x) / (2.0 * sigma * sigma)) for x in xs]
    w = sum(ws)
    wx = sum(wi * x for wi, x in zip(ws, xs))
    wx2 = sum(wi * x * x for wi, x in zip(ws, xs))
    wx3 = sum(wi * x ** 3 for wi, x in zip(ws, xs))
    wx4 = sum(wi * x ** 4 for wi, x in zip(ws, xs))
    wy = sum(wi * y for wi, y in zip(ws, ys))
    wxy = sum(wi * x * y for wi, x, y in zip(ws, xs, ys))
    wx2y = sum(wi * x * x * y for wi, x, y in zip(ws, xs, ys))
    # Cramer's rule on the weighted normal equations
    #   [[wx4, wx3, wx2], [wx3, wx2, wx], [wx2, wx, w]] * [a, b, c]
    #       = [wx2y, wxy, wy]
    # Every determinant below is the cofactor expansion along the first row of
    # the matrix with the matching column replaced by the right-hand side.
    #
    # Deliberate deviation from upstream: upstream's det_b expansion is wrong.
    # It reduces to the correct value only when the fit window is symmetric
    # about the extremum sample (wx = wx3 = 0), and a window clipped by the
    # start or the end of a pass is not symmetric, so upstream returns a
    # meaningless vertex there. The expansion below is the correct one.
    det = (wx4 * (wx2 * w - wx * wx)
           - wx3 * (wx3 * w - wx2 * wx)
           + wx2 * (wx3 * wx - wx2 * wx2))
    if abs(det) < FIT_DET_EPSILON:
        raise ValueError(
            "quadratic fit normal equations are singular, the response in "
            "the fit window carries no curvature")
    det_a = (wx2y * (wx2 * w - wx * wx)
             - wx3 * (wxy * w - wy * wx)
             + wx2 * (wxy * wx - wy * wx2))
    det_b = (wx4 * (wxy * w - wx * wy)
             - wx2y * (wx3 * w - wx2 * wx)
             + wx2 * (wx3 * wy - wx2 * wxy))
    a = det_a / det
    b = det_b / det
    if abs(a) < FIT_CURVATURE_EPSILON:
        raise ValueError(
            "quadratic fit is flat, the response in the fit window carries "
            "no curvature")
    if peak_type == 'peak' and a > 0.0:
        raise ValueError(
            "quadratic fit opens upward around a detected peak, the fit "
            "window does not hold the response extremum")
    if peak_type == 'valley' and a < 0.0:
        raise ValueError(
            "quadratic fit opens downward around a detected valley, the fit "
            "window does not hold the response extremum")
    x_peak = -b / (2.0 * a)
    max_offset = half_window * vertex_limit
    if abs(x_peak) > max_offset:
        raise ValueError(
            "fitted vertex sits %.2f samples away from the extremum sample, "
            "past the %.2f sample limit" % (abs(x_peak), max_offset))
    return x_peak


def interpolate_position(xs, ys, index):
    """Linear interpolation of a scan position at a fractional sample index."""
    n = len(xs)
    if n < 2 or len(ys) != n:
        raise ValueError(
            "position interpolation needs at least 2 matching x and y "
            "samples, got %d and %d" % (n, len(ys)))
    if index < 0.0 or index > n - 1:
        raise ValueError(
            "interpolation index %.3f lies outside the %d sample pass"
            % (index, n))
    floor_idx = min(int(index), n - 2)
    t = index - floor_idx
    x = xs[floor_idx] + t * (xs[floor_idx + 1] - xs[floor_idx])
    y = ys[floor_idx] + t * (ys[floor_idx + 1] - ys[floor_idx])
    return x, y


def fit_scan_pass(xs, ys, freqs, half_window, sigma, edge_margin,
                  vertex_limit):
    """Fit the response extremum of one directional scan pass.

    This is the whole estimator for one pass and the single swappable unit:
    peak-type detection, extremum search, Gaussian-weighted quadratic fit,
    and interpolation of the fitted vertex back onto the scan path. Returns
    a dict with peak_x, peak_y, peak_type, extremum_index, vertex_offset and
    sample_count.
    """
    n = len(freqs)
    if len(xs) != n or len(ys) != n:
        raise ValueError(
            "scan pass has %d frequency samples but %d x and %d y positions"
            % (n, len(xs), len(ys)))
    peak_type = detect_peak_type(freqs, edge_margin)
    peak_idx = find_extremum_index(freqs, peak_type, edge_margin)
    offset = fit_vertex_offset(
        freqs, peak_idx, half_window, sigma, peak_type, vertex_limit)
    peak_x, peak_y = interpolate_position(xs, ys, peak_idx + offset)
    return {
        'peak_x': peak_x,
        'peak_y': peak_y,
        'peak_type': peak_type,
        'extremum_index': peak_idx,
        'vertex_offset': offset,
        'sample_count': n,
    }


def project_peaks(peaks):
    """Project each pass peak onto its own scan axis.

    peaks is a list of (angle_deg, peak_x, peak_y). Returns a list of
    (angle_deg, projection).
    """
    if not peaks:
        raise ValueError("center reconstruction needs at least one scan pass")
    out = []
    for angle_deg, px, py in peaks:
        rad = math.radians(angle_deg)
        out.append((angle_deg, px * math.cos(rad) + py * math.sin(rad)))
    return out


def average_paired_projections(peaks):
    """Average each scan axis' forward and reverse peak projections.

    Ported from upstream's paired-averaging mode: both peaks of an opposed
    pair are projected onto the forward direction's axis and averaged, which
    cancels the constant position bias that transport latency and backlash
    add along the direction of travel. A pass without an opposite is kept
    unpaired. peaks is a list of (angle_deg, peak_x, peak_y); returns a list
    of (angle_deg, projection).
    """
    if not peaks:
        raise ValueError("center reconstruction needs at least one scan pass")
    by_angle = {}
    for angle_deg, px, py in peaks:
        by_angle[angle_deg % 360.0] = (px, py)
    out = []
    used = set()
    for angle in sorted(by_angle):
        if angle in used:
            continue
        px, py = by_angle[angle]
        rad = math.radians(angle)
        cos_a = math.cos(rad)
        sin_a = math.sin(rad)
        proj = px * cos_a + py * sin_a
        opposite = (angle + 180.0) % 360.0
        if opposite in by_angle and opposite not in used:
            ox, oy = by_angle[opposite]
            proj = (proj + ox * cos_a + oy * sin_a) / 2.0
            used.add(opposite)
        used.add(angle)
        out.append((angle, proj))
    return out


def solve_center_lsq(projections):
    """Least-squares reconstruction of a center from axis projections.

    projections is a list of (angle_deg, projection). Solves the overdetermined
    system tx*cos(t) + ty*sin(t) = projection by normal equations. Raises
    ValueError when the angle set cannot constrain both axes.
    """
    if not projections:
        raise ValueError("center reconstruction needs at least one scan pass")
    a11 = a12 = a22 = c1 = c2 = 0.0
    for angle_deg, proj in projections:
        rad = math.radians(angle_deg)
        c = math.cos(rad)
        s = math.sin(rad)
        a11 += c * c
        a12 += c * s
        a22 += s * s
        c1 += c * proj
        c2 += s * proj
    det = a11 * a22 - a12 * a12
    if abs(det) < LSQ_DET_EPSILON:
        raise ValueError(
            "scan angles %s cannot reconstruct both axes, they are parallel "
            "or nearly parallel"
            % (", ".join("%.1f" % (a,) for a, _ in projections),))
    tx = (a22 * c1 - a12 * c2) / det
    ty = (a11 * c2 - a12 * c1) / det
    return tx, ty


def scan_endpoints(center_x, center_y, angle_deg, length):
    """Start and end point of a scan of the given length through a center.

    0 degrees runs along X+, 90 degrees along Y+. Returns
    (start_x, start_y, end_x, end_y).
    """
    if length <= 0.0:
        raise ValueError(
            "scan length must be greater than 0, got %r" % (length,))
    rad = math.radians(angle_deg)
    dx = math.cos(rad) * length / 2.0
    dy = math.sin(rad) * length / 2.0
    return (center_x - dx, center_y - dy, center_x + dx, center_y + dy)


def normalize_scan_angles(angles, pair_scans):
    """Normalize configured scan angles to [0, 360) and reject duplicates.

    Two passes along the same direction add no information and would enter the
    pair-averaging map twice, so a repeated angle is an error rather than a
    silently dropped pass. With pair_scans enabled the opposite of every
    configured angle is scanned as well, so a configured pair of opposites is
    the same duplicate one step later and is rejected here too.
    """
    if not angles:
        raise ValueError("at least one scan angle is required")
    out = []
    for angle in angles:
        normalized = float(angle) % 360.0
        if normalized in out:
            raise ValueError(
                "scan angle %.1f degrees is listed twice" % (normalized,))
        if pair_scans and (normalized + 180.0) % 360.0 in out:
            raise ValueError(
                "scan angles %.1f and %.1f degrees are opposites, and "
                "pair_scans already scans the opposite of every angle"
                % ((normalized + 180.0) % 360.0, normalized))
        out.append(normalized)
    return out


def expand_scan_angles(angles, pair_scans):
    """Full pass list for a normalized angle set, adding opposites if paired."""
    out = list(angles)
    if pair_scans:
        for angle in list(angles):
            out.append((angle + 180.0) % 360.0)
    return out


def z_descent_targets(z_start, z_stop, z_step):
    """Descent heights from z_start down to z_stop inclusive.

    The descent has to end exactly at z_stop, so the span must be a whole
    number of steps. Raises ValueError when it is not, rather than truncating
    and stopping short.
    """
    if z_step <= 0.0:
        raise ValueError("z_step must be greater than 0, got %r" % (z_step,))
    if z_stop >= z_start:
        raise ValueError(
            "z_stop %.4f mm must lie below z_start %.4f mm" % (z_stop, z_start))
    span = z_start - z_stop
    steps = int(round(span / z_step))
    if abs(span - steps * z_step) > 1e-9:
        raise ValueError(
            "the %.4f mm span from z_start to z_stop is not a whole number of "
            "%.4f mm steps" % (span, z_step))
    return [z_start - i * z_step for i in range(steps)] + [float(z_stop)]


def build_z_curve(points):
    """Validate and order a frequency-vs-Z descent curve.

    points is a list of (z, frequency). Returns the list sorted by ascending
    Z. Ported from probe_eddy_current's calibration validation: frequency must
    increase strictly at every step down, otherwise the descent did not
    measure a usable response.
    """
    if len(points) < 3:
        raise ValueError(
            "Z curve needs at least 3 steps, got %d" % (len(points),))
    curve = sorted((float(z), float(f)) for z, f in points)
    for i in range(1, len(curve)):
        if curve[i][0] == curve[i - 1][0]:
            raise ValueError(
                "Z curve holds two steps at the same height %.4f mm"
                % (curve[i][0],))
        if curve[i][1] >= curve[i - 1][1]:
            raise ValueError(
                "Z curve frequency does not increase at every step down: "
                "%.3f Hz at %.4f mm is not below %.3f Hz at %.4f mm"
                % (curve[i][1], curve[i][0], curve[i - 1][1], curve[i - 1][0]))
    return curve


def z_curve_freq_at(curve, z):
    """Linear interpolation of the curve frequency at a height."""
    if len(curve) < 2:
        raise ValueError(
            "Z curve needs at least 2 steps to interpolate, got %d"
            % (len(curve),))
    if z < curve[0][0] or z > curve[-1][0]:
        raise ValueError(
            "height %.4f mm lies outside the measured Z curve %.4f to "
            "%.4f mm" % (z, curve[0][0], curve[-1][0]))
    for i in range(1, len(curve)):
        z1, f1 = curve[i - 1]
        z2, f2 = curve[i]
        if z <= z2:
            return f1 + (f2 - f1) * (z - z1) / (z2 - z1)
    raise ValueError(
        "height %.4f mm was not bracketed by the measured Z curve" % (z,))


def z_curve_z_at_freq(curve, freq):
    """Linear interpolation of the height at which the curve crosses freq."""
    if len(curve) < 2:
        raise ValueError(
            "Z curve needs at least 2 steps to interpolate, got %d"
            % (len(curve),))
    high_freq = curve[0][1]
    low_freq = curve[-1][1]
    if freq > high_freq or freq < low_freq:
        raise ValueError(
            "reference frequency %.3f Hz lies outside the measured Z curve "
            "%.3f to %.3f Hz" % (freq, low_freq, high_freq))
    for i in range(1, len(curve)):
        z1, f1 = curve[i - 1]
        z2, f2 = curve[i]
        if freq >= f2:
            return z1 + (z2 - z1) * (freq - f1) / (f2 - f1)
    raise ValueError(
        "reference frequency %.3f Hz was not bracketed by the measured Z "
        "curve" % (freq,))


def fit_half_window_samples(sample_rate, scan_speed, window_radius):
    """Fit half window in samples for a scan speed and window radius in mm."""
    if sample_rate <= 0.0:
        raise ValueError(
            "sensor sample rate must be greater than 0, got %r"
            % (sample_rate,))
    if scan_speed <= 0.0:
        raise ValueError(
            "scan speed must be greater than 0, got %r" % (scan_speed,))
    if window_radius <= 0.0:
        raise ValueError(
            "fit window radius must be greater than 0, got %r"
            % (window_radius,))
    return max(1, int(sample_rate / scan_speed * window_radius))


def spread(values):
    """Minimum, maximum and population standard deviation of a sample set."""
    if not values:
        raise ValueError("spread needs at least one value")
    n = len(values)
    avg = sum(values) / n
    var = sum((v - avg) ** 2 for v in values) / n
    return min(values), max(values), math.sqrt(var)


# ---------------------------------------------------------------------------
# Klippy-facing layer.
# ---------------------------------------------------------------------------

# Tool indices accepted by T= and by the z_ref_t<n> config options.
MAX_TOOLS = 16

# Provenance: probe_eddy_current's calibration moves. Each step settles for
# 0.050 s before its 0.100 s sample window, dwells 0.200 s in place, is
# approached from 0.500 mm above, and the whole descent is bracketed by a
# 1.0 s dwell so the sample stream settles before and after it.
SAMPLE_SETTLE_TIME = 0.050
SAMPLE_WINDOW_TIME = 0.100
STEP_DWELL_TIME = 0.200
Z_APPROACH_HOP = 0.500
DESCENT_SETTLE_DWELL = 1.000

# Chosen value, not from probe_eddy_current: the bulk sensor delivers 0.100 s
# batches, so collection runs two batch periods past the end of a move to be
# sure the batch carrying the last in-move samples has arrived.
COLLECT_TAIL_TIME = 0.200

# Chosen value, not from probe_eddy_current: half a second at the sensor's
# 250 Hz sample rate gives about 125 samples, enough for a meaningful spread
# without making a wiring check feel slow. Exposed as the query_time config
# option; this is the default.
QUERY_COLLECT_TIME_DEFAULT = 0.500

# Chosen value: the coarse locate pass has to cover the uncertainty in the
# configured coil position, which is much larger than the coil itself, so the
# default locate length is three times the regular scan length.
LOCATE_SCAN_LENGTH_FACTOR = 3.0


class EddyToolCalibration:
    """Klippy extra: EDDY_QUERY / EDDY_LOCATE / EDDY_CALIBRATE_TOOL /
    EDDY_SET_BASELINE / EDDY_SET_Z_REF gcode commands.
    """

    def __init__(self, config):
        self.printer = config.get_printer()
        self.name = config.get_name()

        # Geometry.
        self.coil_x = config.getfloat('coil_x', 350.0)
        self.coil_y = config.getfloat('coil_y', 5.0)
        self.coil_inner_diameter = config.getfloat(
            'coil_inner_diameter', 2.0, above=0.0)
        # Machine Z of the coil top face, the origin every scan height and
        # descent height in this plugin is measured from.
        self.coil_z = config.getfloat('coil_z', 0.0)
        self.scan_height = config.getfloat('scan_height', 1.0)
        self.scan_safe_z = config.getfloat('scan_safe_z', 2.0, above=0.0)
        self.z_start = config.getfloat('z_start', 5.0)
        self.z_stop = config.getfloat('z_stop', 0.5)
        self.z_step = config.getfloat('z_step', 0.05, above=0.0)
        try:
            self.z_targets = z_descent_targets(
                self.z_start, self.z_stop, self.z_step)
        except ValueError as e:
            raise config.error(
                "%s: %s. Raise z_stop, lower z_start, or pick a z_step that "
                "divides the span." % (self.name, e))

        # Scan tuning.
        self.scan_speed = config.getfloat('scan_speed', 4.0, above=0.0)
        self.scan_length = config.getfloat('scan_length', 4.0, above=0.0)
        self.locate_scan_length = config.getfloat(
            'locate_scan_length',
            self.scan_length * LOCATE_SCAN_LENGTH_FACTOR, above=0.0)
        self.travel_speed = config.getfloat('travel_speed', 100.0, above=0.0)
        self.z_speed = config.getfloat('z_speed', 10.0, above=0.0)
        self.pair_scans = config.getboolean('pair_scans', True)
        try:
            self.scan_angles = normalize_scan_angles(
                [float(a) for a in
                 config.get('scan_angles', '45, 135').split(',')],
                self.pair_scans)
        except ValueError as e:
            raise config.error("%s: scan_angles: %s" % (self.name, e))
        self.samples_min = config.getint('samples_min', 100, minval=3)
        self.save_csv = config.getboolean('save_csv', False)
        self.query_time = config.getfloat(
            'query_time', QUERY_COLLECT_TIME_DEFAULT, above=0.0)

        # Fit tuning. The window radius defaults to the coil inner radius, so
        # the fit sees exactly the sample span the coil bore responds over.
        # Deliberate deviation from upstream, which shrinks the bore by a
        # further 0.5 mm before halving it; that shrink is unexplained and an
        # unexplained constant is not carried over.
        self.fit_window_radius = config.getfloat(
            'fit_window_radius', self.coil_inner_diameter / 2.0, above=0.0)
        # Upstream's convention: a Gaussian weight whose standard deviation is
        # half the fit window.
        self.fit_sigma_fraction = config.getfloat(
            'fit_sigma_fraction', 0.5, above=0.0)
        self.fit_vertex_limit = config.getfloat(
            'fit_vertex_limit', 0.5, above=0.0)
        self.edge_margin = config.getfloat(
            'edge_margin', 0.15, above=0.0, below=0.5)
        self.freq_min = config.getfloat(
            'freq_min', FREQ_MIN_DEFAULT, minval=0.0)

        # Per-tool Z anchors persisted in config as "z_ref_t<n>: <z>:<freq>".
        self.z_refs = {}
        for tool in range(MAX_TOOLS):
            raw = config.get('z_ref_t%d' % (tool,), None)
            if raw is None:
                continue
            parts = raw.split(':')
            if len(parts) != 2:
                raise config.error(
                    "%s: z_ref_t%d must read \"<z>:<frequency>\""
                    % (self.name, tool))
            try:
                self.z_refs[tool] = (float(parts[0]), float(parts[1]))
            except ValueError:
                raise config.error(
                    "%s: z_ref_t%d must read \"<z>:<frequency>\""
                    % (self.name, tool))

        # Session state.
        self.center = None
        self.baseline = None
        self.results = {}

        # The LDC1612 driver reads its i2c options, frequency and
        # reg_drive_current straight off the config section handed to it, and
        # our schema uses those same option names, so our own section is
        # passed through without a wrapper. It also registers
        # LDC_CALIBRATE_DRIVE_CURRENT CHIP=eddy_tool_calibration for us.
        from klippy.extras import ldc1612
        self.sensor = ldc1612.LDC1612(config)

        gcode = self.printer.lookup_object('gcode')
        gcode.register_command(
            'EDDY_QUERY', self.cmd_EDDY_QUERY,
            desc=self.cmd_EDDY_QUERY_help)
        gcode.register_command(
            'EDDY_LOCATE', self.cmd_EDDY_LOCATE,
            desc=self.cmd_EDDY_LOCATE_help)
        gcode.register_command(
            'EDDY_CALIBRATE_TOOL', self.cmd_EDDY_CALIBRATE_TOOL,
            desc=self.cmd_EDDY_CALIBRATE_TOOL_help)
        gcode.register_command(
            'EDDY_SET_BASELINE', self.cmd_EDDY_SET_BASELINE,
            desc=self.cmd_EDDY_SET_BASELINE_help)
        gcode.register_command(
            'EDDY_SET_Z_REF', self.cmd_EDDY_SET_Z_REF,
            desc=self.cmd_EDDY_SET_Z_REF_help)

    # -- helpers ----------------------------------------------------------

    def _ensure_homed(self, gcmd):
        toolhead = self.printer.lookup_object('toolhead')
        homed = toolhead.get_status(
            self.printer.get_reactor().monotonic())['homed_axes']
        missing = [a for a in 'xyz' if a not in homed]
        if missing:
            raise gcmd.error(
                "Home the printer first. Axes %s are not homed."
                % (" ".join(a.upper() for a in missing),))

    def _get_trapq(self, gcmd):
        motion_report = self.printer.lookup_object('motion_report', None)
        if motion_report is None:
            raise gcmd.error(
                "Run this command after the printer has finished starting "
                "up. The motion report is registered by the printer's "
                "steppers, so it is missing only before startup completes or "
                "when no steppers are configured.")
        dump = motion_report.trapqs.get('toolhead')
        if dump is None:
            raise gcmd.error(
                "The toolhead motion queue is not available yet. Run this "
                "command after the printer has finished starting up.")
        return dump

    def _tool_index(self, gcmd):
        return gcmd.get_int('T', 0, minval=0, maxval=MAX_TOOLS - 1)

    def _report_sensor_health(self, gcmd, stats):
        if stats['errors']:
            raise gcmd.error(
                "Run LDC_CALIBRATE_DRIVE_CURRENT CHIP=%s and retry. The "
                "sensor reported %d sample errors during the measurement."
                % (self.name.split()[-1], stats['errors']))
        if stats['overflows']:
            gcmd.respond_info(
                "sensor buffer overflows: %d" % (stats['overflows'],))

    def _new_collection(self):
        """Start counting the samples and faults of one collection.

        Both the LDC1612 error count and the bulk reader's overflow count are
        cumulative for the life of the sensor, so a collection owns only the
        difference between the counts at its end and at its start. The
        baselines are read here, before the client is added, because the first
        delivered batch already carries a batch period of counts.
        """
        return {
            'base_errors': self.sensor.last_error_count,
            'base_overflows': self.sensor.ffreader.get_last_overflows(),
            'raw_count': 0,
            'dropped_low_freq': 0,
            'dropped_no_position': 0,
            'dropped_outside_move': 0,
            'errors': 0,
            'overflows': 0,
        }

    def _close_collection(self, stats):
        """Finish a collection, converting the cumulative counts to its own."""
        stats['errors'] = self.sensor.last_error_count - stats['base_errors']
        stats['overflows'] = (
            self.sensor.ffreader.get_last_overflows() - stats['base_overflows'])
        return stats

    def _sample_drop_rows(self, stats):
        """Labeled diagnostic rows for the samples a collection discarded."""
        return [
            "raw samples: %d" % (stats['raw_count'],),
            "dropped below freq_min: %d" % (stats['dropped_low_freq'],),
            "dropped without a position: %d"
            % (stats['dropped_no_position'],),
            "dropped outside the move: %d"
            % (stats['dropped_outside_move'],),
        ]

    def _no_sample_cause(self, stats):
        """Name the likely cause when a collection yielded nothing usable.

        The three filters a sample can die in have three different causes, so
        the message names whichever one consumed the samples.
        """
        if stats['raw_count'] == 0:
            return ("Check the sensor wiring and the I2C bus configuration. "
                    "The sensor delivered no samples at all.")
        if stats['dropped_no_position'] > stats['dropped_low_freq']:
            return ("Re-run the command after homing, and leave the printer "
                    "idle while it runs. The toolhead motion queue did not "
                    "cover the sample timestamps.")
        if stats['dropped_low_freq'] > 0:
            return ("Lower freq_min, or run LDC_CALIBRATE_DRIVE_CURRENT "
                    "CHIP=%s. Every sample read below freq_min, so the coil "
                    "may not be resonating." % (self.name.split()[-1],))
        return ("Lower scan_speed or raise scan_length. Samples arrived but "
                "none of them fell inside the scan move's time window.")

    def _descent_gap_cause(self, stats):
        """Name the likely cause when a descent left some steps without data."""
        if stats['raw_count'] == 0:
            return ("Check the sensor wiring and the I2C bus configuration. "
                    "The sensor delivered no samples during the descent.")
        if stats['dropped_low_freq'] == stats['raw_count']:
            return ("Lower freq_min, or run LDC_CALIBRATE_DRIVE_CURRENT "
                    "CHIP=%s. Every descent sample read below freq_min."
                    % (self.name.split()[-1],))
        return ("Raise z_step above the printer's Z resolution. Steps finer "
                "than the kinematics can resolve land on the same height and "
                "collapse into one measurement.")

    def _move(self, x, y, z, z_speed):
        """Travel to (x, y, z), never crossing the coil below the target Z.

        XY always travels at travel_speed and the Z leg at z_speed. A move to
        a higher Z raises Z first; a move to a lower Z travels in XY first, so
        the nozzle never approaches at a height below where it is going.
        """
        toolhead = self.printer.lookup_object('toolhead')
        if z > toolhead.get_position()[2]:
            toolhead.manual_move([None, None, z], z_speed)
            toolhead.manual_move([x, y, None], self.travel_speed)
        else:
            toolhead.manual_move([x, y, None], self.travel_speed)
            toolhead.manual_move([None, None, z], z_speed)
        toolhead.wait_moves()

    # -- sample collection ------------------------------------------------

    def _collect_stationary(self, duration):
        """Collect samples without moving. Returns (freqs, stats)."""
        reactor = self.printer.get_reactor()
        toolhead = self.printer.lookup_object('toolhead')
        freqs = []
        stats = self._new_collection()
        state = {'running': True}

        def handle_batch(msg):
            if not state['running']:
                return False
            if not msg:
                return True
            for print_time, freq, dummy_z in msg['data']:
                stats['raw_count'] += 1
                if freq < self.freq_min:
                    stats['dropped_low_freq'] += 1
                    continue
                freqs.append(freq)
            return True

        self.sensor.add_client(handle_batch)
        try:
            toolhead.wait_moves()
            reactor.pause(reactor.monotonic() + duration)
        finally:
            state['running'] = False
        return freqs, self._close_collection(stats)

    def _collect_scan(self, gcmd, start_x, start_y, end_x, end_y, scan_z):
        """Run one directional scan pass and return its mapped samples.

        Returns (samples, stats) where samples is a list of
        (print_time, frequency, x, y). Positions come from the toolhead
        motion queue at each sample's own timestamp, so acceleration ramps
        map correctly.
        """
        reactor = self.printer.get_reactor()
        toolhead = self.printer.lookup_object('toolhead')
        dump = self._get_trapq(gcmd)
        collected = []
        stats = self._new_collection()
        state = {'running': True}

        def handle_batch(msg):
            if not state['running']:
                return False
            if not msg:
                return True
            for print_time, freq, dummy_z in msg['data']:
                stats['raw_count'] += 1
                if freq < self.freq_min:
                    stats['dropped_low_freq'] += 1
                    continue
                pos, velocity = dump.get_trapq_position(print_time)
                if pos is None:
                    stats['dropped_no_position'] += 1
                    continue
                collected.append((print_time, freq, pos[0], pos[1]))
            return True

        safe_z = scan_z + self.scan_safe_z
        self._move(start_x, start_y, safe_z, self.z_speed)
        self._move(start_x, start_y, scan_z, self.z_speed)
        self.sensor.add_client(handle_batch)
        try:
            reactor.pause(reactor.monotonic() + SAMPLE_SETTLE_TIME)
            move_start = toolhead.get_last_move_time()
            toolhead.manual_move([end_x, end_y, None], self.scan_speed)
            # The scan move's end time is read while the move is still
            # queued, so it is the move's own end. Reading it after
            # wait_moves() would return a time padded into the future by the
            # toolhead's buffer, admitting stationary samples into the pass.
            move_end = toolhead.get_last_move_time()
            toolhead.wait_moves()
            reactor.pause(reactor.monotonic() + COLLECT_TAIL_TIME)
        finally:
            state['running'] = False
        toolhead.manual_move([None, None, safe_z], self.z_speed)
        toolhead.wait_moves()
        samples = [s for s in collected if move_start <= s[0] <= move_end]
        stats['dropped_outside_move'] = len(collected) - len(samples)
        return samples, self._close_collection(stats)

    def _scan_pass(self, gcmd, center_x, center_y, angle_deg, length, scan_z,
                   label):
        start_x, start_y, end_x, end_y = scan_endpoints(
            center_x, center_y, angle_deg, length)
        samples, stats = self._collect_scan(
            gcmd, start_x, start_y, end_x, end_y, scan_z)
        self._report_sensor_health(gcmd, stats)
        if not samples:
            raise gcmd.error(
                "The %s pass produced no usable samples. %s\n%s"
                % (label, self._no_sample_cause(stats),
                   "\n".join(self._sample_drop_rows(stats))))
        if len(samples) < self.samples_min:
            raise gcmd.error(
                "The %s pass returned %d samples, below the configured "
                "samples_min of %d. Lower scan_speed or raise scan_length."
                % (label, len(samples), self.samples_min))
        if self.save_csv:
            self._save_csv(gcmd, label, samples)
        xs = [s[2] for s in samples]
        ys = [s[3] for s in samples]
        freqs = [s[1] for s in samples]
        half_window = fit_half_window_samples(
            self.sensor.data_rate, self.scan_speed, self.fit_window_radius)
        try:
            result = fit_scan_pass(
                xs, ys, freqs, half_window,
                half_window * self.fit_sigma_fraction, self.edge_margin,
                self.fit_vertex_limit)
        except ValueError as e:
            raise gcmd.error(
                "The %s pass did not yield a usable response fit: %s. Run "
                "EDDY_LOCATE to refine the coil center, and check that no "
                "metal sits near the coil." % (label, e))
        return result, stats

    def _save_csv(self, gcmd, label, samples):
        config_file = self.printer.get_start_args()['config_file']
        directory = os.path.dirname(os.path.abspath(config_file))
        path = os.path.join(
            directory, "eddy_scan_%s.csv" % (label.replace(' ', '_'),))
        try:
            with open(path, 'w') as f:
                f.write("print_time,frequency,x,y\n")
                for print_time, freq, x, y in samples:
                    f.write("%.6f,%.3f,%.4f,%.4f\n" % (print_time, freq, x, y))
        except IOError as e:
            raise gcmd.error(
                "Could not write the scan data to %s: %s. Set save_csv to "
                "False or fix the directory permissions." % (path, e))
        gcmd.respond_info("scan data: %s" % (path,))

    # -- measurement ------------------------------------------------------

    def _measure_center(self, gcmd, center_x, center_y, length, label):
        """One full multi-direction XY measurement around a center estimate."""
        angles = expand_scan_angles(self.scan_angles, self.pair_scans)
        scan_z = self.coil_z + self.scan_height
        peaks = []
        for angle in angles:
            result, stats = self._scan_pass(
                gcmd, center_x, center_y, angle, length, scan_z,
                "%s %.0f deg" % (label, angle))
            rows = [
                "pass angle: %.1f deg" % (angle,),
                "response type: %s" % (result['peak_type'],),
                "samples: %d" % (result['sample_count'],),
                "extremum sample: %d" % (result['extremum_index'],),
                "vertex offset: %+.3f samples" % (result['vertex_offset'],),
                "peak x: %.4f" % (result['peak_x'],),
                "peak y: %.4f" % (result['peak_y'],),
            ]
            rows.extend(self._sample_drop_rows(stats))
            gcmd.respond_info("\n".join(rows))
            peaks.append((angle, result['peak_x'], result['peak_y']))
        if self.pair_scans:
            projections = average_paired_projections(peaks)
        else:
            projections = project_peaks(peaks)
        try:
            return solve_center_lsq(projections)
        except ValueError as e:
            raise gcmd.error(
                "The scan directions did not reconstruct a center: %s. Set "
                "scan_angles to two directions at least 30 degrees apart."
                % (e,))

    def _measure_xy(self, gcmd):
        center_x, center_y = self.center if self.center else (
            self.coil_x, self.coil_y)
        first_x, first_y = self._measure_center(
            gcmd, center_x, center_y, self.scan_length, "coarse")
        gcmd.respond_info(
            "first pass center x: %.4f\nfirst pass center y: %.4f"
            % (first_x, first_y))
        # One iteration re-centered on the first result.
        return self._measure_center(
            gcmd, first_x, first_y, self.scan_length, "refine")

    def _measure_z_curve(self, gcmd, center_x, center_y):
        """Stepwise descent over the coil center, returning the Z curve.

        Ported from probe_eddy_current's calibration moves: every step is
        approached from above, samples are bucketed by an explicit per-step
        time window, and the height stored is the real kinematic position.
        """
        reactor = self.printer.get_reactor()
        toolhead = self.printer.lookup_object('toolhead')
        kin = toolhead.get_kinematics()
        msgs = []
        stats = self._new_collection()
        state = {'running': True}

        def handle_batch(msg):
            if not state['running']:
                return False
            if not msg:
                return True
            msgs.append(msg)
            return True

        self._move(center_x, center_y, self.z_start + Z_APPROACH_HOP,
                   self.z_speed)
        self.sensor.add_client(handle_batch)
        times = []
        try:
            toolhead.dwell(DESCENT_SETTLE_DWELL)
            for target in self.z_targets:
                toolhead.manual_move(
                    [None, None, target + Z_APPROACH_HOP], self.z_speed)
                toolhead.manual_move([None, None, target], self.z_speed)
                start_query_time = (
                    toolhead.get_last_move_time() + SAMPLE_SETTLE_TIME)
                end_query_time = start_query_time + SAMPLE_WINDOW_TIME
                toolhead.dwell(STEP_DWELL_TIME)
                toolhead.flush_step_generation()
                kin_spos = {
                    s.get_name(): s.get_commanded_position()
                    for s in kin.get_steppers()
                }
                kin_pos = kin.calc_position(kin_spos)
                times.append((start_query_time, end_query_time, kin_pos[2]))
            toolhead.dwell(DESCENT_SETTLE_DWELL)
            toolhead.wait_moves()
            reactor.pause(reactor.monotonic() + COLLECT_TAIL_TIME)
        finally:
            state['running'] = False
        self._move(center_x, center_y, self.z_start + Z_APPROACH_HOP,
                   self.z_speed)
        self._close_collection(stats)
        self._report_sensor_health(gcmd, stats)
        buckets = {}
        step = 0
        for msg in msgs:
            for query_time, freq, dummy_z in msg['data']:
                stats['raw_count'] += 1
                if freq < self.freq_min:
                    stats['dropped_low_freq'] += 1
                    continue
                while step < len(times) and query_time > times[step][1]:
                    step += 1
                if step < len(times) and query_time >= times[step][0]:
                    buckets.setdefault(times[step][2], []).append(freq)
                else:
                    stats['dropped_outside_move'] += 1
        if len(buckets) != len(times):
            raise gcmd.error(
                "The descent produced sensor data for %d of %d steps. %s\n%s"
                % (len(buckets), len(times),
                   self._descent_gap_cause(stats),
                   "\n".join(self._sample_drop_rows(stats))))
        points = [(z, sum(freqs) / len(freqs))
                  for z, freqs in buckets.items()]
        try:
            return build_z_curve(points)
        except ValueError as e:
            raise gcmd.error(
                "The Z descent did not produce a usable curve: %s. Lower "
                "z_start so the descent stays inside the sensor's range."
                % (e,))

    # -- commands ---------------------------------------------------------

    cmd_EDDY_QUERY_help = (
        "Print the current eddy sensor frequency reading, for a wiring "
        "sanity check.")

    def cmd_EDDY_QUERY(self, gcmd):
        freqs, stats = self._collect_stationary(self.query_time)
        if not freqs:
            raise gcmd.error(
                "The sensor returned no usable samples. %s\n%s"
                % (self._no_sample_cause(stats),
                   "\n".join(self._sample_drop_rows(stats))))
        self._report_sensor_health(gcmd, stats)
        low, high, std = spread(freqs)
        rows = [
            "samples: %d" % (len(freqs),),
            "frequency mean: %.3f Hz" % (sum(freqs) / len(freqs),),
            "frequency min: %.3f Hz" % (low,),
            "frequency max: %.3f Hz" % (high,),
            "frequency stddev: %.3f Hz" % (std,),
        ]
        rows.extend(self._sample_drop_rows(stats))
        gcmd.respond_info("\n".join(rows))

    cmd_EDDY_LOCATE_help = (
        "Coarse raster scan over the configured coil position to find and "
        "store the refined coil center for this session.")

    def cmd_EDDY_LOCATE(self, gcmd):
        self._ensure_homed(gcmd)
        coarse_x, coarse_y = self._measure_center(
            gcmd, self.coil_x, self.coil_y, self.locate_scan_length,
            "locate coarse")
        gcmd.respond_info(
            "coarse center x: %.4f\ncoarse center y: %.4f"
            % (coarse_x, coarse_y))
        refined_x, refined_y = self._measure_center(
            gcmd, coarse_x, coarse_y, self.scan_length, "locate refine")
        self.center = (refined_x, refined_y)
        gcmd.respond_info(
            "coil center x: %.4f\n"
            "coil center y: %.4f\n"
            "config coil_x: %.4f\n"
            "config coil_y: %.4f"
            % (refined_x, refined_y, self.coil_x, self.coil_y))

    cmd_EDDY_CALIBRATE_TOOL_help = (
        "Run the full XY and Z eddy-current measurement for the mounted "
        "tool and print its offsets relative to the T0 baseline.")

    def cmd_EDDY_CALIBRATE_TOOL(self, gcmd):
        self._ensure_homed(gcmd)
        tool = self._tool_index(gcmd)
        result = self._run_tool_measurement(gcmd, tool)
        self._report_tool_result(gcmd, tool, result)

    def _run_tool_measurement(self, gcmd, tool):
        center_x, center_y = self._measure_xy(gcmd)
        curve = self._measure_z_curve(gcmd, center_x, center_y)
        result = {
            'x': center_x,
            'y': center_y,
            'z_curve': curve,
        }
        self.results[tool] = result
        return result

    def _report_tool_result(self, gcmd, tool, result):
        curve = result['z_curve']
        rows = [
            "tool: T%d" % (tool,),
            "center x: %.4f" % (result['x'],),
            "center y: %.4f" % (result['y'],),
            "z curve steps: %d" % (len(curve),),
            "z curve range: %.4f to %.4f mm" % (curve[0][0], curve[-1][0]),
            "z curve frequency range: %.3f to %.3f Hz"
            % (curve[-1][1], curve[0][1]),
        ]
        z_cross = None
        if tool in self.z_refs:
            ref_z, ref_freq = self.z_refs[tool]
            try:
                z_cross = z_curve_z_at_freq(curve, ref_freq)
            except ValueError as e:
                raise gcmd.error(
                    "The Z reference for T%d does not fall inside this "
                    "descent: %s. Re-run EDDY_SET_Z_REF T=%d for this tool."
                    % (tool, e, tool))
            result['z_cross'] = z_cross
            rows.append("z reference frequency: %.3f Hz" % (ref_freq,))
            rows.append("z crossing: %.4f mm" % (z_cross,))
            rows.append("z vs anchor: %+.4f mm" % (z_cross - ref_z,))
        else:
            rows.append(
                "z crossing: not available, run EDDY_SET_Z_REF T=%d Z=<mm>"
                % (tool,))
        if self.baseline is not None:
            base = self.baseline
            rows.append("baseline tool: T%d" % (base['tool'],))
            rows.append("offset x: %+.4f" % (result['x'] - base['x'],))
            rows.append("offset y: %+.4f" % (result['y'] - base['y'],))
            if z_cross is not None and base.get('z_cross') is not None:
                rows.append(
                    "offset z: %+.4f" % (z_cross - base['z_cross'],))
            else:
                rows.append(
                    "offset z: not available, both tools need a Z reference")
        else:
            rows.append(
                "offsets: not available, run EDDY_SET_BASELINE on the "
                "reference tool")
        gcmd.respond_info("\n".join(rows))

    cmd_EDDY_SET_BASELINE_help = (
        "Declare the currently mounted tool as the T0 baseline for this "
        "session.")

    def cmd_EDDY_SET_BASELINE(self, gcmd):
        self._ensure_homed(gcmd)
        tool = self._tool_index(gcmd)
        # A baseline is always a fresh measurement, so the baseline and the
        # tools compared against it come from the same machinery.
        result = self._run_tool_measurement(gcmd, tool)
        # _report_tool_result owns the reference-frequency crossing, so the
        # baseline stores the value that readout produced instead of
        # evaluating the curve a second time.
        self._report_tool_result(gcmd, tool, result)
        self.baseline = {
            'tool': tool,
            'x': result['x'],
            'y': result['y'],
            'z_cross': result.get('z_cross'),
        }
        gcmd.respond_info("baseline tool: T%d" % (tool,))

    cmd_EDDY_SET_Z_REF_help = (
        "Bind the current tool's measured frequency curve to a real Z "
        "offset obtained by another method (Z= parameter).")

    def cmd_EDDY_SET_Z_REF(self, gcmd):
        tool = self._tool_index(gcmd)
        z = gcmd.get_float('Z')
        result = self.results.get(tool)
        if result is None:
            raise gcmd.error(
                "Run EDDY_CALIBRATE_TOOL T=%d first. The anchor binds a "
                "measured descent curve to your Z measurement." % (tool,))
        try:
            ref_freq = z_curve_freq_at(result['z_curve'], z)
        except ValueError as e:
            raise gcmd.error(
                "Z=%.4f does not fall inside the measured descent: %s. "
                "Widen z_start and z_stop to cover it." % (z, e))
        self.z_refs[tool] = (z, ref_freq)
        gcmd.respond_info(
            "tool: T%d\n"
            "z anchor: %.4f mm\n"
            "z reference frequency: %.3f Hz\n"
            "config z_ref_t%d: %.4f:%.3f"
            % (tool, z, ref_freq, tool, z, ref_freq))


def load_config(config):
    return EddyToolCalibration(config)
