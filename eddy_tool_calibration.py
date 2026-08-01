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

"""Kalico klippy plugin module: EDDY_QUERY, EDDY_LOCATE, EDDY_CALIBRATE_Z
and EDDY_CALIBRATE_OFFSET gcode commands for eddy-current based per-tool
nozzle offset calibration. See docs/design.md and docs/z-probe-design.md for
the full design and config schema.

Constraint: this module must import cleanly on a machine without klippy
installed (unit tests run standalone). Any import of klippy modules
(klippy.extras.ldc1612, motion_report, etc.) must happen inside a function or
method body, never at module scope.
"""

import contextlib
import json
import logging
import math
import os
import tempfile
import time

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


def validate_vertical_geometry(scan_height, z_start, z_stop):
    """Check the configured heights against the coil-relative convention.

    scan_height, z_start and z_stop are all heights above the coil top face,
    where 0 mm is the nozzle touching the face. The descent therefore has to
    stop before it reaches the face, and the XY scan plane has to sit below
    the top of the descent so both measurements share one range. Raises
    ValueError naming the option at fault. The z_start above z_stop ordering
    is not checked here: z_descent_targets owns the descent list and already
    rejects it.
    """
    if z_stop <= 0.0:
        raise ValueError(
            "z_stop %.4f mm is not above the coil top face, where 0 mm is "
            "the nozzle touching the face" % (z_stop,))
    if scan_height <= 0.0:
        raise ValueError(
            "scan_height %.4f mm is not above the coil top face, where 0 mm "
            "is the nozzle touching the face" % (scan_height,))
    if scan_height >= z_start:
        raise ValueError(
            "scan_height %.4f mm must lie below z_start %.4f mm so the XY "
            "scan plane sits inside the descent range"
            % (scan_height, z_start))


def z_descent_targets(z_start, z_stop, z_step):
    """Descent heights from z_start down to z_stop inclusive.

    Works in whatever vertical frame the caller uses; the plugin hands it the
    configured heights above the coil top face and converts each returned
    target to machine Z when it commands the move.

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


def z_curve_shared_reference(curve):
    """Reference height and frequency at the middle of a curve's Z range.

    Returns (z, frequency) at the midpoint by height of the measured range.
    The midpoint is the height furthest from both ends of the descent, so a
    reference taken there leaves the widest margin on either side for another
    tool's curve to still bracket that frequency.
    """
    if len(curve) < 2:
        raise ValueError(
            "Z curve needs at least 2 steps to take a reference, got %d"
            % (len(curve),))
    mid_z = (curve[0][0] + curve[-1][0]) / 2.0
    return mid_z, z_curve_freq_at(curve, mid_z)


# --- contact switch anchoring ----------------------------------------------

# The switch is pressed four times and the first press is discarded. A first
# press on a cold or unseated switch travels differently from the rest, so it
# is a warm-up rather than a measurement, and it is dropped by its position in
# the sequence rather than by any test on its value. The remaining three give
# an unambiguous median with no even-count rule to pick.
SWITCH_PRESS_COUNT = 4
SWITCH_PRESS_DISCARDED = 1


def aggregate_switch_presses(heights, tolerance):
    """Median trigger height of the counted presses of one switch probing.

    heights holds the trigger height of every press in the order it was made.
    Returns (median, counted, spread), where counted holds the presses that
    were kept. Raises ValueError when the counted presses disagree by more
    than tolerance: a switch that cannot repeat inside its tolerance has a
    mechanical cause that another press does not fix.
    """
    if len(heights) != SWITCH_PRESS_COUNT:
        raise ValueError(
            "switch probing needs %d presses, got %d"
            % (SWITCH_PRESS_COUNT, len(heights)))
    if tolerance <= 0.0:
        raise ValueError(
            "switch probe tolerance must be greater than 0, got %r"
            % (tolerance,))
    counted = [float(h) for h in heights[SWITCH_PRESS_DISCARDED:]]
    ordered = sorted(counted)
    median = ordered[len(ordered) // 2]
    press_spread = ordered[-1] - ordered[0]
    if press_spread > tolerance:
        raise ValueError(
            "the counted presses triggered at %s mm, a spread of %.4f mm, "
            "above the %.4f mm tolerance"
            % (", ".join("%.4f" % (h,) for h in counted), press_spread,
               tolerance))
    return median, counted, press_spread


def switch_anchor(curve, trigger_z):
    """Anchor height above a trigger plane, and the frequency there.

    The anchor is taken at the midpoint by height of the tool's own measured
    descent, the height furthest from both ends of that descent, so a later
    descent keeps the widest margin on either side to still bracket the anchor
    frequency. What is returned is the height above the switch trigger plane
    rather than a machine Z, so the switch's own height cancels out of every
    comparison between two tools.
    """
    anchor_z, anchor_freq = z_curve_shared_reference(curve)
    return anchor_z - float(trigger_z), anchor_freq


def trigger_plane_from_anchor(curve, anchor_height, anchor_frequency):
    """Machine Z of the trigger plane a stored anchor implies for a curve.

    Finds the height at which a freshly measured curve reaches the stored
    anchor frequency, then subtracts the stored height above the trigger
    plane. Returns (trigger_z, crossing_z). Raises ValueError when the stored
    frequency lies outside the measured curve.
    """
    crossing = z_curve_z_at_freq(curve, anchor_frequency)
    return crossing - float(anchor_height), crossing


# --- persisted calibration state -------------------------------------------

# Directory next to the printer config that holds everything this plugin
# writes, and the state file inside it.
STATE_DIR = 'EddyToolCalibration'
STATE_FILENAME = 'calibration_state.json'

# The only document version this build reads or writes.
STATE_VERSION = 1

# The two fields the offset math reads. The rest of an anchor record is
# diagnostic: it lets a stale anchor be recognised after the coil or the
# switch moves, and is never fed back into a measurement.
ANCHOR_NUMBER_FIELDS = (
    'anchor_height', 'anchor_frequency', 'trigger_z', 'curve_low_z',
    'curve_high_z', 'center_x', 'center_y',
)
ANCHOR_TEXT_FIELDS = ('updated',)


def encode_state(anchors):
    """Serialise per-tool anchors to the state file's JSON document.

    anchors maps an integer tool number to an anchor record. Tool numbers
    become decimal strings because JSON object keys are strings.
    """
    document = {'version': STATE_VERSION, 'anchors': {}}
    for tool in sorted(anchors):
        record = anchors[tool]
        entry = {}
        for field in ANCHOR_NUMBER_FIELDS:
            if field not in record:
                raise ValueError(
                    "the anchor for T%d is missing the %s field"
                    % (tool, field))
            entry[field] = float(record[field])
        for field in ANCHOR_TEXT_FIELDS:
            if field not in record:
                raise ValueError(
                    "the anchor for T%d is missing the %s field"
                    % (tool, field))
            entry[field] = str(record[field])
        document['anchors'][str(tool)] = entry
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def decode_state(text):
    """Parse a state document into anchors keyed by integer tool number.

    Fields inside an anchor that this build does not know are ignored, so a
    later version can add record fields without stranding an older build. An
    unrecognised top-level version is not ignored: the document's layout is
    what the version names, and reading it as version 1 would misread it.
    """
    try:
        document = json.loads(text)
    except ValueError as e:
        raise ValueError("the state file is not valid JSON: %s" % (e,))
    if not isinstance(document, dict):
        raise ValueError(
            "the state file holds a %s where a JSON object was expected"
            % (type(document).__name__,))
    version = document.get('version')
    if version != STATE_VERSION:
        raise ValueError(
            "the state file carries version %r, and this plugin reads "
            "version %d" % (version, STATE_VERSION))
    raw_anchors = document.get('anchors', {})
    if not isinstance(raw_anchors, dict):
        raise ValueError(
            "the state file's anchors entry holds a %s where a JSON object "
            "was expected" % (type(raw_anchors).__name__,))
    anchors = {}
    for key, record in raw_anchors.items():
        try:
            tool = int(key)
        except ValueError:
            raise ValueError(
                "the state file holds the anchor key %r, which is not a tool "
                "number" % (key,))
        if not isinstance(record, dict):
            raise ValueError(
                "the anchor for T%d holds a %s where a JSON object was "
                "expected" % (tool, type(record).__name__))
        entry = {}
        for field in ANCHOR_NUMBER_FIELDS:
            if field not in record:
                raise ValueError(
                    "the anchor for T%d is missing the %s field"
                    % (tool, field))
            try:
                entry[field] = float(record[field])
            except (TypeError, ValueError):
                raise ValueError(
                    "the anchor for T%d holds %r in its %s field, which is "
                    "not a number" % (tool, record[field], field))
        for field in ANCHOR_TEXT_FIELDS:
            if field not in record:
                raise ValueError(
                    "the anchor for T%d is missing the %s field"
                    % (tool, field))
            entry[field] = str(record[field])
        anchors[tool] = entry
    return anchors


def sweep_tool_order(tool_count):
    """Tool numbers a fleet run visits, in the order it visits them.

    Tools are numbered from zero upward with no holes, so the order is simply
    T0 through T(tool_count-1). The baseline tool comes first, which is what
    lets a fleet offset run satisfy the baseline rule on its own.
    """
    if tool_count is None:
        raise ValueError(
            "the tool count is not set, so the tools a fleet run covers are "
            "not known")
    count = int(tool_count)
    if count < 1:
        raise ValueError(
            "the tool count must be at least 1, got %r" % (tool_count,))
    return list(range(count))


def offset_template_context(tool, offsets, calibrate_z):
    """Names an apply_offsets_gcode template is rendered with.

    With calibrate_z False no descent ran, so offset_z is left out of the
    context rather than passed as zero: a template that applies a Z offset
    that was never measured would move the nozzle on the strength of a number
    nothing produced.
    """
    context = {
        'tool': int(tool),
        'offset_x': float(offsets['x']),
        'offset_y': float(offsets['y']),
    }
    if calibrate_z:
        if offsets['z'] is None:
            raise ValueError(
                "the Z offset of T%d was not measured, and Z calibration is "
                "on" % (int(tool),))
        context['offset_z'] = float(offsets['z'])
    return context


def fleet_summary_rows(entries):
    """Labeled per-tool rows closing a fleet run.

    entries is a list of dicts holding a tool number and either its offsets
    or None for the baseline tool, in the order the tools were measured.
    """
    if not entries:
        raise ValueError("a fleet summary needs at least one measured tool")
    rows = ["fleet summary:"]
    for entry in entries:
        tool = int(entry['tool'])
        offsets = entry['offsets']
        if offsets is None:
            rows.append(
                "T%d: baseline tool, offsets zero by definition" % (tool,))
            continue
        parts = ["offset x: %+.4f" % (offsets['x'],),
                 "offset y: %+.4f" % (offsets['y'],)]
        if offsets['z'] is not None:
            parts.append("offset z: %+.4f" % (offsets['z'],))
        rows.append("T%d: %s" % (tool, ", ".join(parts)))
    return rows


def validate_csv_dir(csv_dir):
    """Reject a scan dump directory that would sit on the state file's own.

    The dumps are cleared out from time to time, and the saved references must
    not go with them, so the two never share a directory.
    """
    if os.path.normpath(csv_dir) == os.path.normpath(STATE_DIR):
        raise ValueError(
            "csv_dir %r is the directory the calibration state file lives in. "
            "Point csv_dir at a subdirectory such as %s instead, so clearing "
            "the scan dumps cannot take the saved Z references with it."
            % (csv_dir, os.path.join(STATE_DIR, 'data').replace('\\', '/')))


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

# Tool indices accepted by T=.
MAX_TOOLS = 16

# The tool every other tool's offsets are measured against.
BASELINE_TOOL = 0

# The stages a fleet run reports a failure against. A failure is always
# attributed to exactly one of these, so the message says which part of the
# run stopped.
TOOL_PHASES = ('toolchange', 'switch probing', 'measurement', 'apply')

# The switch options EDDY_CALIBRATE_Z cannot run without. They are read at
# load but their absence is not a load error, because a machine that only
# wants XY offsets never needs them.
SWITCH_REQUIRED_OPTIONS = (
    'switch_pin', 'switch_x', 'switch_y', 'switch_probe_z_start')

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


class SwitchPinConfig:
    """Read-only config view presenting switch_pin under the name pin.

    tools_calibrate's endstop wrapper reads a single option named pin. Our own
    section names it switch_pin so the option stays descriptive next to the
    other switch options, and this view is what bridges the two names. Any
    other option is refused rather than passed through, so a future upstream
    change that starts reading a second option fails here instead of silently
    picking up one of our unrelated options.
    """

    def __init__(self, config, pin):
        self._config = config
        self._pin = pin

    def get_printer(self):
        return self._config.get_printer()

    def get_name(self):
        return self._config.get_name()

    def has_section(self, section):
        return self._config.has_section(section)

    def get(self, option, *args, **kwargs):
        if option == 'pin':
            return self._pin
        raise self._config.error(
            "%s: the contact switch endstop asked for the option %r, which "
            "this config view does not carry."
            % (self._config.get_name(), option))


class EddyToolCalibration:
    """Klippy extra: EDDY_QUERY / EDDY_LOCATE / EDDY_CALIBRATE_Z /
    EDDY_CALIBRATE_OFFSET gcode commands.
    """

    def __init__(self, config):
        self.printer = config.get_printer()
        self.name = config.get_name()

        # Geometry.
        self.coil_x = config.getfloat('coil_x', 350.0)
        self.coil_y = config.getfloat('coil_y', 5.0)
        self.coil_inner_diameter = config.getfloat(
            'coil_inner_diameter', 2.0, above=0.0)
        # Machine Z of the coil top face, the origin every other vertical
        # option in this section is measured from. _machine_z is the one
        # place that converts those heights into machine coordinates.
        self.coil_z = config.getfloat('coil_z', 0.0)
        # Height above the coil top face the XY scan passes run at.
        self.scan_height = config.getfloat('scan_height', 1.0)
        # Extra clearance above the scan height for travel moves.
        self.scan_safe_z = config.getfloat('scan_safe_z', 2.0, above=0.0)
        # Heights above the coil top face the Z descent runs between.
        self.z_start = config.getfloat('z_start', 5.0)
        self.z_stop = config.getfloat('z_stop', 0.5)
        self.z_step = config.getfloat('z_step', 0.05, above=0.0)
        try:
            validate_vertical_geometry(
                self.scan_height, self.z_start, self.z_stop)
        except ValueError as e:
            raise config.error(
                "%s: %s. Set coil_z to the machine Z of the coil top face, "
                "and keep scan_height, z_start and z_stop as heights above "
                "that face." % (self.name, e))
        # The targets stay in the configured frame, heights above the coil
        # top face, and are converted as each move is commanded.
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
        self.csv_dir = config.get(
            'csv_dir', os.path.join(STATE_DIR, 'data').replace('\\', '/'))
        try:
            validate_csv_dir(self.csv_dir)
        except ValueError as e:
            raise config.error("%s: %s" % (self.name, e))
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

        # Options this plugin no longer has. A removed option is refused at
        # load rather than ignored, so a config that means something it can no
        # longer do says so instead of quietly measuring something else.
        self._reject_removed_options(config)

        # Whether the Z descent runs at all. The descent is the slow part of a
        # calibration, so it stays off until Z offsets are wanted.
        self.calibrate_z = config.getboolean('calibrate_z', False)

        # Contact switch. switch_x, switch_y and switch_probe_z_start are
        # machine coordinates, not heights above the coil top face, because
        # the switch is a separate fixture with no fixed relation to the coil.
        self.switch_pin = config.get('switch_pin', None)
        self.switch_x = config.getfloat('switch_x', None)
        self.switch_y = config.getfloat('switch_y', None)
        self.switch_probe_z_start = config.getfloat(
            'switch_probe_z_start', None)
        # Guards match the ones the toolchanger probing tools apply.
        self.switch_probe_speed = config.getfloat(
            'switch_probe_speed', 5.0, above=0.0)
        self.switch_probe_lift_speed = config.getfloat(
            'switch_probe_lift_speed', self.switch_probe_speed, above=0.0)
        self.switch_probe_max_travel = config.getfloat(
            'switch_probe_max_travel', 4.0, above=0.0)
        self.switch_probe_sample_retract_dist = config.getfloat(
            'switch_probe_sample_retract_dist', 2.0, above=0.0)
        self.switch_probe_tolerance = config.getfloat(
            'switch_probe_tolerance', 0.020, above=0.0)
        # Each press starts from wherever the previous one retracted to, so a
        # retract at or beyond the travel allowance puts the switch out of
        # reach of every press after the first.
        if (self.switch_probe_sample_retract_dist
                >= self.switch_probe_max_travel):
            raise config.error(
                "%s: switch_probe_sample_retract_dist (%.4f mm) must be less "
                "than switch_probe_max_travel (%.4f mm). Each press starts "
                "from the height the previous press retracted to, so a "
                "retract that is not shorter than the travel allowance leaves "
                "the switch out of reach from the second press onward."
                % (self.name, self.switch_probe_sample_retract_dist,
                   self.switch_probe_max_travel))

        # Fleet options. They are what a run without T= needs: how many tools
        # there are, and the lines that mount one. Without them each tool is
        # still calibrated on its own with T=.
        self.tool_count = config.getint(
            'tool_count', None, minval=1, maxval=99)
        if self.tool_count is not None and self.tool_count > MAX_TOOLS:
            raise config.error(
                "%s: tool_count is %d, and T= accepts T0 through T%d. Lower "
                "tool_count to the number of tools the machine has."
                % (self.name, self.tool_count, MAX_TOOLS - 1))
        gcode_macro = self.printer.load_object(config, 'gcode_macro')
        self.toolchange_gcode = gcode_macro.load_template(
            config, 'toolchange_gcode', '')
        self.apply_offsets_gcode = gcode_macro.load_template(
            config, 'apply_offsets_gcode', '')
        # An empty template is how klippy spells "the owner did not set this
        # option", and both options change what the commands do rather than
        # only what they emit, so the plain text is checked once here.
        self.has_toolchange_gcode = bool(
            config.get('toolchange_gcode', '').strip())
        self.has_apply_offsets_gcode = bool(
            config.get('apply_offsets_gcode', '').strip())

        # Session state.
        self.center = None
        self.baseline = None
        self.results = {}
        self.session_id = 0
        self.last_tool = None

        # Persisted per-tool Z anchors, keyed by integer tool number.
        self.anchors = self._load_state(config)

        # The LDC1612 driver reads its i2c options, frequency and
        # reg_drive_current straight off the config section handed to it, and
        # our schema uses those same option names, so our own section is
        # passed through without a wrapper. It also registers
        # LDC_CALIBRATE_DRIVE_CURRENT CHIP=eddy_tool_calibration for us.
        from klippy.extras import ldc1612
        self.sensor = ldc1612.LDC1612(config)

        # The contact switch endstop, reused from the toolchanger probing
        # tools. It reads only the pin, allows the pin to be shared with an
        # existing [tools_calibrate] section, and registers no commands and no
        # pin chip of its own, so nothing here collides with that section.
        self.switch_endstop = None
        if self.switch_pin is not None:
            from klippy.extras import tools_calibrate
            self.switch_endstop = tools_calibrate.ProbeEndstopWrapper(
                SwitchPinConfig(config, self.switch_pin), 'z')

        gcode = self.printer.lookup_object('gcode')
        gcode.register_command(
            'EDDY_QUERY', self.cmd_EDDY_QUERY,
            desc=self.cmd_EDDY_QUERY_help)
        gcode.register_command(
            'EDDY_LOCATE', self.cmd_EDDY_LOCATE,
            desc=self.cmd_EDDY_LOCATE_help)
        gcode.register_command(
            'EDDY_CALIBRATE_Z', self.cmd_EDDY_CALIBRATE_Z,
            desc=self.cmd_EDDY_CALIBRATE_Z_help)
        gcode.register_command(
            'EDDY_CALIBRATE_OFFSET', self.cmd_EDDY_CALIBRATE_OFFSET,
            desc=self.cmd_EDDY_CALIBRATE_OFFSET_help)

    # -- config and persisted state ---------------------------------------

    def _reject_removed_options(self, config):
        """Refuse a config that still carries an option this plugin dropped."""
        if config.get('z_offset_mode', None) is not None:
            raise config.error(
                "%s: remove z_offset_mode and set calibrate_z instead. Every "
                "tool now carries its own Z reference, measured by "
                "EDDY_CALIBRATE_Z against a contact switch, so the mode that "
                "assumed every tool has an identical hotend is gone."
                % (self.name,))
        for tool in range(MAX_TOOLS):
            if config.get('z_ref_t%d' % (tool,), None) is None:
                continue
            raise config.error(
                "%s: remove z_ref_t%d and run EDDY_CALIBRATE_Z T=%d once per "
                "tool. References are stored in %s now. The old value cannot "
                "be converted: it is a machine Z tied to a coil position and "
                "a hand measurement, and the new reference is a height above "
                "the switch trigger plane, which only the switch measurement "
                "defines."
                % (self.name, tool, tool, self._state_path()))

    def _config_dir(self):
        """Directory the printer config lives in, the root of our own files."""
        config_file = self.printer.get_start_args()['config_file']
        return os.path.dirname(os.path.abspath(config_file))

    def _state_path(self):
        return os.path.join(self._config_dir(), STATE_DIR, STATE_FILENAME)

    def _load_state(self, config):
        """Read the persisted anchors. A missing file means none are set."""
        path = self._state_path()
        if not os.path.exists(path):
            return {}
        try:
            with open(path, 'r') as f:
                text = f.read()
        except OSError as e:
            raise config.error(
                "%s: could not read %s: %s. Fix the file permissions, or "
                "delete the file to start over and re-run EDDY_CALIBRATE_Z "
                "for each tool." % (self.name, path, e))
        try:
            return decode_state(text)
        except ValueError as e:
            raise config.error(
                "%s: %s (%s). Delete the file to start over and re-run "
                "EDDY_CALIBRATE_Z for each tool." % (self.name, e, path))

    def _write_state(self, gcmd):
        """Persist the anchors, replacing the state file atomically.

        The document is written to a temporary file in the same directory and
        moved over the target, so an interrupted write cannot leave a
        truncated state file behind.
        """
        path = self._state_path()
        directory = os.path.dirname(path)
        try:
            os.makedirs(directory, exist_ok=True)
            handle, temp_path = tempfile.mkstemp(
                dir=directory, prefix=STATE_FILENAME, suffix='.tmp')
            try:
                with os.fdopen(handle, 'w') as f:
                    f.write(encode_state(self.anchors))
                os.replace(temp_path, path)
            except Exception:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                raise
        except (OSError, ValueError) as e:
            raise gcmd.error(
                "Could not write the calibration state to %s: %s. Fix the "
                "directory permissions if the path cannot be written, or "
                "delete the file to start over if the message names an "
                "incomplete reference, then run EDDY_CALIBRATE_Z again. The "
                "reference was not kept, because a reference that did not "
                "persist would be gone at the next restart." % (path, e))

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

    def _optional_tool_index(self, gcmd):
        """The tool named by T=, or None when T= was left out.

        An omitted T= is a request for every tool rather than a mistake, so
        the two cases are handed back to the caller to dispatch over.
        """
        tool = gcmd.get_int('T', None, minval=0, maxval=MAX_TOOLS - 1)
        if tool is None:
            return None
        if self.tool_count is not None and tool >= self.tool_count:
            raise gcmd.error(
                "T=%d is beyond the last tool. tool_count is %d in the [%s] "
                "config section, so the tools are T0 through T%d."
                % (tool, self.tool_count, self.name, self.tool_count - 1))
        return tool

    def _sweep_tools(self, gcmd, command):
        """The tools a run without T= covers, or the error naming what is
        missing before one can run."""
        missing = []
        if self.tool_count is None:
            missing.append('tool_count')
        if not self.has_toolchange_gcode:
            missing.append('toolchange_gcode')
        if missing:
            raise gcmd.error(
                "Add %s to the [%s] config section and restart, or run "
                "%s T=0 to calibrate one tool at a time. Running the command "
                "without T= calibrates every tool in turn, which needs the "
                "number of tools in tool_count and the lines that mount a "
                "tool in toolchange_gcode."
                % (" and ".join(missing), self.name, command))
        try:
            return sweep_tool_order(self.tool_count)
        except ValueError as e:
            raise gcmd.error(
                "The tools of this machine could not be listed: %s. Set "
                "tool_count in the [%s] config section to the number of tools "
                "the machine has." % (e, self.name))

    @contextlib.contextmanager
    def _phase(self, gcmd, tool, phase, sweeping):
        """Name the tool and the stage a failure inside a fleet run came from.

        A single-tool run reports its own error unchanged: the tool is the one
        named on the command line and there is nothing to disambiguate.
        """
        if phase not in TOOL_PHASES:
            raise gcmd.error(
                "Internal error: the calibration stage %r is not one of %r."
                % (phase, TOOL_PHASES))
        if not sweeping:
            yield
            return
        try:
            yield
        except self.printer.command_error as e:
            raise gcmd.error(
                "T%d failed during %s: %s. The tools calibrated before it "
                "keep their results." % (tool, phase, e))

    def _mount_tool(self, gcmd, tool):
        """Run the configured toolchange lines for a tool.

        With no toolchange_gcode configured the plugin works on whatever tool
        is mounted, which is how it behaves on a machine that changes tools by
        hand. It never learns anything about the toolchanger either way: it
        runs the lines the owner wrote and nothing else.
        """
        if not self.has_toolchange_gcode:
            return
        context = self.toolchange_gcode.create_template_context()
        context['tool'] = tool
        self.toolchange_gcode.run_gcode_from_command(context)
        # A toolchange moves the toolhead, and the measurement reads positions
        # out of the motion queue, so the change is finished before it starts.
        self.printer.lookup_object('toolhead').wait_moves()

    def _apply_offsets(self, gcmd, tool, offsets):
        """Run the configured apply lines with a tool's measured offsets."""
        if not self.has_apply_offsets_gcode:
            return
        try:
            values = offset_template_context(tool, offsets, self.calibrate_z)
        except ValueError as e:
            raise gcmd.error(
                "The offsets of T%d cannot be applied: %s." % (tool, e))
        context = self.apply_offsets_gcode.create_template_context()
        context.update(values)
        try:
            self.apply_offsets_gcode.run_gcode_from_command(context)
        except self.printer.command_error as e:
            raise gcmd.error(
                "apply_offsets_gcode failed for T%d: %s. The lines can use "
                "tool, offset_x and offset_y%s."
                % (tool, e,
                   ", and offset_z" if self.calibrate_z else
                   "; offset_z is available only with calibrate_z set to "
                   "True, and it is False, so a line naming offset_z has "
                   "nothing to put there"))

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

    def _debug_flag(self, gcmd):
        return gcmd.get_int('DEBUG', 0, minval=0, maxval=1)

    def _new_aggregate(self):
        """Zeroed accumulator for the summary block's sample counters."""
        return {
            'samples_used': 0,
            'dropped_low_freq': 0,
            'dropped_no_position': 0,
            'dropped_outside_move': 0,
        }

    def _add_to_aggregate(self, agg, stats, kept_count):
        agg['samples_used'] += kept_count
        agg['dropped_low_freq'] += stats['dropped_low_freq']
        agg['dropped_no_position'] += stats.get('dropped_no_position', 0)
        agg['dropped_outside_move'] += stats['dropped_outside_move']

    def _merge_aggregate(self, agg, other):
        for key in agg:
            agg[key] += other[key]

    def _aggregate_rows(self, agg):
        """Labeled summary rows: total samples used, and drops if any.

        The drop counters stay hidden when they are all zero, so quiet mode
        does not grow rows over nothing, but any nonzero drop across the
        whole measurement stays visible even with DEBUG=0.
        """
        rows = ["samples used: %d" % (agg['samples_used'],)]
        if (agg['dropped_low_freq'] or agg['dropped_no_position']
                or agg['dropped_outside_move']):
            rows.append(
                "dropped below freq_min: %d" % (agg['dropped_low_freq'],))
            rows.append(
                "dropped without a position: %d"
                % (agg['dropped_no_position'],))
            rows.append(
                "dropped outside the move: %d"
                % (agg['dropped_outside_move'],))
        return rows

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

    def _machine_z(self, height):
        """Machine Z of a height above the coil top face.

        scan_height, z_start and z_stop are all heights above the coil top
        face, so this is the only place the configured frame becomes a
        machine coordinate. Heights read back off the kinematics during a
        descent are already machine Z and never pass through here.
        """
        return self.coil_z + height

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

    def _retreat(self):
        """Lift the toolhead clear of the coil and the switch.

        The same height the descent already ends at, so a run that stops
        anywhere leaves the nozzle where a successful run would have left it.
        """
        toolhead = self.printer.lookup_object('toolhead')
        toolhead.manual_move(
            [None, None, self._machine_z(self.z_start + Z_APPROACH_HOP)],
            self.z_speed)
        toolhead.wait_moves()

    @contextlib.contextmanager
    def _retreating(self):
        """Run a block of motion and lift clear afterwards, however it ends.

        A lift that fails while another error is already on its way up is
        logged instead of raised, so the message the console shows is the one
        naming the original cause rather than the failure to move away from
        it.
        """
        try:
            yield
        except Exception:
            try:
                self._retreat()
            except Exception:
                logging.exception(
                    "eddy_tool_calibration: could not lift the toolhead clear "
                    "after a failed calibration move")
            raise
        self._retreat()

    # -- contact switch probing -------------------------------------------

    def _kin_z_limits(self, gcmd):
        """The kinematic Z travel limits, as (minimum, maximum).

        The single place the plugin reads the machine's vertical limits, so
        every check against them, and the probing move's own clamp, agree.
        """
        toolhead = self.printer.lookup_object('toolhead')
        curtime = self.printer.get_reactor().monotonic()
        kin_status = toolhead.get_kinematics().get_status(curtime)
        if ('axis_minimum' not in kin_status
                or 'axis_maximum' not in kin_status):
            raise gcmd.error(
                "Switch probing works with cartesian kinematics only. The "
                "configured kinematics report no axis limits.")
        return kin_status['axis_minimum'][2], kin_status['axis_maximum'][2]

    def _require_switch_z_range(self, gcmd):
        """Refuse to travel to the switch when the heights are out of range."""
        minimum_z, maximum_z = self._kin_z_limits(gcmd)
        travel_z = self.switch_probe_z_start + self.scan_safe_z
        if self.switch_probe_z_start < minimum_z:
            raise gcmd.error(
                "switch_probe_z_start is machine Z %.4f mm, below the Z axis "
                "minimum of %.4f mm. Set switch_probe_z_start to a height "
                "the machine can reach, just above the switch."
                % (self.switch_probe_z_start, minimum_z))
        if travel_z > maximum_z:
            raise gcmd.error(
                "switch_probe_z_start plus scan_safe_z is machine Z %.4f mm, "
                "above the Z axis maximum of %.4f mm. Lower "
                "switch_probe_z_start or scan_safe_z; the plugin travels to "
                "the switch at that combined height."
                % (travel_z, maximum_z))

    def _require_switch_config(self, gcmd):
        """Refuse to probe until every option the switch needs is present."""
        if not self.calibrate_z:
            raise gcmd.error(
                "Set calibrate_z to True in the [%s] config section and "
                "restart. Z calibration is off, so there is nothing for a Z "
                "reference to be used by." % (self.name,))
        missing = [name for name in SWITCH_REQUIRED_OPTIONS
                   if getattr(self, name) is None]
        if missing:
            raise gcmd.error(
                "Add %s to the [%s] config section and restart. Probing the "
                "contact switch needs the switch pin, the machine X and Y the "
                "nozzle presses it at, and the machine Z the press starts "
                "from." % (", ".join(missing), self.name))

    def _query_switch(self, gcmd):
        """Refuse to move when the switch already reads triggered.

        A switch that is closed before the nozzle touches it is a fault, so it
        is reported rather than pressed again: a retry would turn a stuck
        switch, an inverted pin, or a start height below the trigger point
        into a silent second attempt.
        """
        toolhead = self.printer.lookup_object('toolhead')
        toolhead.wait_moves()
        if self.switch_endstop.query_endstop(toolhead.get_last_move_time()):
            raise gcmd.error(
                "The contact switch already reads triggered before the "
                "nozzle moved. Check that the switch is not stuck closed, "
                "that switch_pin has the right inverting prefix, and that "
                "switch_probe_z_start sits above the trigger point.")

    def _probe_switch_once(self, gcmd):
        """One downward probing move onto the switch, returning its trigger Z.

        Ported from tools_calibrate's PrinterProbeMultiAxis: the target is the
        current position lowered by the travel allowance and clamped to the
        kinematic Z minimum, and the move itself is the homing module's
        probing_move, which returns the position computed from step counts at
        trigger time.
        """
        phoming = self.printer.lookup_object('homing')
        toolhead = self.printer.lookup_object('toolhead')
        minimum_z, _maximum_z = self._kin_z_limits(gcmd)
        pos = toolhead.get_position()
        target = list(pos)
        requested_z = pos[2] - self.switch_probe_max_travel
        clamped = requested_z < minimum_z
        target[2] = max(requested_z, minimum_z)
        try:
            epos = phoming.probing_move(
                self.switch_endstop, target, self.switch_probe_speed)
        except self.printer.command_error as e:
            reason = str(e)
            if "Probe triggered prior to movement" in reason:
                raise gcmd.error(
                    "The contact switch already read triggered at the start "
                    "of a press. Check that the switch is not stuck closed, "
                    "that switch_pin has the right inverting prefix, and that "
                    "switch_probe_sample_retract_dist lifts the nozzle clear "
                    "of the trigger point between presses.")
            if "No trigger on probe after full movement" in reason:
                if clamped:
                    raise gcmd.error(
                        "The nozzle travelled %.4f mm down from machine Z "
                        "%.4f to the Z axis minimum of %.4f mm without "
                        "triggering the contact switch. The press stopped at "
                        "that axis limit rather than at "
                        "switch_probe_max_travel, so raising that option "
                        "cannot help. Check switch_x, switch_y and "
                        "switch_probe_z_start against where the switch "
                        "actually sits, and check the Z axis position_min."
                        % (pos[2] - target[2], pos[2], minimum_z))
                raise gcmd.error(
                    "The nozzle travelled %.4f mm down from machine Z %.4f "
                    "without triggering the contact switch. Check switch_x, "
                    "switch_y and switch_probe_z_start against where the "
                    "switch actually sits, and raise switch_probe_max_travel "
                    "if the nozzle stops short of it."
                    % (pos[2] - target[2], pos[2]))
            raise gcmd.error(
                "The switch probing move failed: %s. The nozzle was at "
                "machine Z %.4f and was moving to %.4f."
                % (reason, pos[2], target[2]))
        return epos[2]

    def _probe_switch(self, gcmd, debug):
        """Press the switch and return (trigger_z, counted, spread).

        Ported from tools_calibrate's run_probe sample loop, with its
        aggregation replaced: the presses are a fixed four, the first is
        discarded as a warm-up, and the median of the remaining three is the
        result. A spread above switch_probe_tolerance is an error rather than
        a retry, because a switch that cannot repeat inside its tolerance has
        a mechanical cause another press does not fix.

        Every press retracts, the last one included, so the nozzle is never
        left standing on the switch while the presses are aggregated.
        """
        toolhead = self.printer.lookup_object('toolhead')
        _minimum_z, maximum_z = self._kin_z_limits(gcmd)
        heights = []
        for press in range(SWITCH_PRESS_COUNT):
            trigger_z = self._probe_switch_once(gcmd)
            heights.append(trigger_z)
            if debug:
                gcmd.respond_info(
                    "switch press %d trigger (machine Z): %.4f mm"
                    % (press + 1, trigger_z))
            retract_z = trigger_z + self.switch_probe_sample_retract_dist
            if retract_z > maximum_z:
                raise gcmd.error(
                    "Retracting %.4f mm from the trigger at machine Z %.4f "
                    "would reach machine Z %.4f, above the Z axis maximum of "
                    "%.4f mm. Lower switch_probe_sample_retract_dist."
                    % (self.switch_probe_sample_retract_dist, trigger_z,
                       retract_z, maximum_z))
            toolhead.manual_move(
                [None, None, retract_z], self.switch_probe_lift_speed)
            toolhead.wait_moves()
        try:
            median, counted, press_spread = aggregate_switch_presses(
                heights, self.switch_probe_tolerance)
        except ValueError as e:
            raise gcmd.error(
                "The contact switch did not repeat: %s. Check the switch "
                "mounting and the nozzle for debris, or raise "
                "switch_probe_tolerance if your switch cannot do better."
                % (e,))
        return median, counted, press_spread

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

        Positions are resolved only after the scan move has completed, never
        inside the sensor callback. motion_report's get_trapq_position reads
        the trapq through trapq_extract_old, which walks the trapq's history
        list alone. A move reaches that history list only when the toolhead's
        flush loop calls trapq_finalize_moves with a free time past the move's
        end, so while the scan move is still executing it sits in the trapq's
        pending "moves" list and is invisible to the query. The query then
        finds the newest already-finalized move instead, clamps its move_time
        to that move's own duration, and returns its end position: the scan
        start point. That is a real tuple, not None, so a mid-move query
        silently yields a stale position for nearly every sample and the
        pass "finds" its extremum at the scan start. Queried after
        wait_moves(), the whole move is in history (retained for
        MOVE_HISTORY_EXPIRE, 30 s in toolhead.py, far longer than a pass),
        so every timestamp maps correctly and a None result again genuinely
        means the timestamp lies outside known motion.
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
                collected.append((print_time, freq))
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
        in_window = [s for s in collected if move_start <= s[0] <= move_end]
        stats['dropped_outside_move'] = len(collected) - len(in_window)
        samples = []
        for print_time, freq in in_window:
            pos, velocity = dump.get_trapq_position(print_time)
            if pos is None:
                stats['dropped_no_position'] += 1
                continue
            samples.append((print_time, freq, pos[0], pos[1]))
        return samples, self._close_collection(stats)

    def _scan_pass(self, gcmd, center_x, center_y, angle_deg, length, scan_z,
                   label, debug):
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
            self._save_csv(gcmd, label, samples, debug)
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

    def _save_csv(self, gcmd, label, samples, debug):
        directory = os.path.join(self._config_dir(), self.csv_dir)
        path = os.path.join(
            directory, "eddy_scan_%s.csv" % (label.replace(' ', '_'),))
        try:
            os.makedirs(directory, exist_ok=True)
            with open(path, 'w') as f:
                f.write("print_time,frequency,x,y\n")
                for print_time, freq, x, y in samples:
                    f.write("%.6f,%.3f,%.4f,%.4f\n" % (print_time, freq, x, y))
        except OSError as e:
            raise gcmd.error(
                "Could not write the scan data to %s (directory %s): %s. "
                "Set save_csv to False or fix the directory permissions."
                % (path, directory, e))
        if debug:
            gcmd.respond_info("scan data: %s" % (path,))

    # -- measurement ------------------------------------------------------

    def _measure_center(self, gcmd, center_x, center_y, length, label, debug):
        """One full multi-direction XY measurement around a center estimate.

        With DEBUG=0 the per-pass diagnostic rows are held back and only
        flushed if this measurement ends up failing, so a failed pass still
        reports its diagnostics in full.
        """
        angles = expand_scan_angles(self.scan_angles, self.pair_scans)
        scan_z = self._machine_z(self.scan_height)
        peaks = []
        agg = self._new_aggregate()
        pending_rows = []
        for angle in angles:
            try:
                result, stats = self._scan_pass(
                    gcmd, center_x, center_y, angle, length, scan_z,
                    "%s %.0f deg" % (label, angle), debug)
            except Exception:
                if not debug:
                    for block in pending_rows:
                        gcmd.respond_info(block)
                raise
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
            if debug:
                gcmd.respond_info("\n".join(rows))
            else:
                pending_rows.append("\n".join(rows))
            self._add_to_aggregate(agg, stats, result['sample_count'])
            peaks.append((angle, result['peak_x'], result['peak_y']))
        if self.pair_scans:
            projections = average_paired_projections(peaks)
        else:
            projections = project_peaks(peaks)
        try:
            center_x, center_y = solve_center_lsq(projections)
        except ValueError as e:
            if not debug:
                for block in pending_rows:
                    gcmd.respond_info(block)
            raise gcmd.error(
                "The scan directions did not reconstruct a center: %s. Set "
                "scan_angles to two directions at least 30 degrees apart."
                % (e,))
        return center_x, center_y, agg

    def _measure_xy(self, gcmd, debug):
        center_x, center_y = self.center if self.center else (
            self.coil_x, self.coil_y)
        first_x, first_y, agg1 = self._measure_center(
            gcmd, center_x, center_y, self.scan_length, "coarse", debug)
        if debug:
            gcmd.respond_info(
                "first pass center x: %.4f\nfirst pass center y: %.4f"
                % (first_x, first_y))
        # One iteration re-centered on the first result.
        refined_x, refined_y, agg2 = self._measure_center(
            gcmd, first_x, first_y, self.scan_length, "refine", debug)
        agg = self._new_aggregate()
        self._merge_aggregate(agg, agg1)
        self._merge_aggregate(agg, agg2)
        return refined_x, refined_y, agg

    def _measure_z_curve(self, gcmd, center_x, center_y):
        """Stepwise descent over the coil center, returning the Z curve.

        Ported from probe_eddy_current's calibration moves: every step is
        approached from above, samples are bucketed by an explicit per-step
        time window, and the height stored is the real kinematic position.

        The descent targets are heights above the coil top face and become
        machine Z as each move is commanded, so no move ever goes below the
        face. The curve itself holds the machine Z the kinematics reported,
        not the commanded height.
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

        self._move(center_x, center_y,
                   self._machine_z(self.z_start + Z_APPROACH_HOP),
                   self.z_speed)
        self.sensor.add_client(handle_batch)
        times = []
        try:
            toolhead.dwell(DESCENT_SETTLE_DWELL)
            for target in self.z_targets:
                toolhead.manual_move(
                    [None, None, self._machine_z(target + Z_APPROACH_HOP)],
                    self.z_speed)
                toolhead.manual_move(
                    [None, None, self._machine_z(target)], self.z_speed)
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
        self._move(center_x, center_y,
                   self._machine_z(self.z_start + Z_APPROACH_HOP),
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
            curve = build_z_curve(points)
        except ValueError as e:
            raise gcmd.error(
                "The Z descent did not produce a usable curve: %s. Lower "
                "z_start so the descent stays inside the sensor's range."
                % (e,))
        agg = self._new_aggregate()
        agg['samples_used'] = (
            stats['raw_count'] - stats['dropped_low_freq']
            - stats['dropped_outside_move'])
        agg['dropped_low_freq'] = stats['dropped_low_freq']
        agg['dropped_outside_move'] = stats['dropped_outside_move']
        return curve, agg

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
        "store the refined coil center for this session. Add DEBUG=1 to "
        "print each scan pass's diagnostic rows.")

    def cmd_EDDY_LOCATE(self, gcmd):
        self._ensure_homed(gcmd)
        debug = self._debug_flag(gcmd)
        coarse_x, coarse_y, agg1 = self._measure_center(
            gcmd, self.coil_x, self.coil_y, self.locate_scan_length,
            "locate coarse", debug)
        if debug:
            gcmd.respond_info(
                "coarse center x: %.4f\ncoarse center y: %.4f"
                % (coarse_x, coarse_y))
        refined_x, refined_y, agg2 = self._measure_center(
            gcmd, coarse_x, coarse_y, self.scan_length, "locate refine",
            debug)
        self.center = (refined_x, refined_y)
        agg = self._new_aggregate()
        self._merge_aggregate(agg, agg1)
        self._merge_aggregate(agg, agg2)
        rows = [
            "coil center x: %.4f" % (refined_x,),
            "coil center y: %.4f" % (refined_y,),
            "config coil_x: %.4f" % (self.coil_x,),
            "config coil_y: %.4f" % (self.coil_y,),
        ]
        rows.extend(self._aggregate_rows(agg))
        gcmd.respond_info("\n".join(rows))

    cmd_EDDY_CALIBRATE_Z_help = (
        "One-time Z reference setup for the tool named by T=, or for every "
        "tool when T= is left out. Presses the contact switch and binds the "
        "result to the eddy sensor's reading. Run it after changing a nozzle "
        "or a hotend, or after moving the coil or the switch. This is the "
        "setup step, not the routine offset measurement; that is "
        "EDDY_CALIBRATE_OFFSET. Add DEBUG=1 to print each scan pass's "
        "diagnostic rows.")

    def cmd_EDDY_CALIBRATE_Z(self, gcmd):
        self._require_switch_config(gcmd)
        self._ensure_homed(gcmd)
        debug = self._debug_flag(gcmd)
        tool = self._optional_tool_index(gcmd)
        if tool is None:
            tools = self._sweep_tools(gcmd, 'EDDY_CALIBRATE_Z')
            sweeping = True
        else:
            tools = [tool]
            sweeping = False
        self._require_switch_z_range(gcmd)
        for tool in tools:
            with self._phase(gcmd, tool, 'toolchange', sweeping):
                self._mount_tool(gcmd, tool)
            self._anchor_tool(gcmd, tool, debug, sweeping)

    def _anchor_tool(self, gcmd, tool, debug, sweeping):
        """Press the switch for one tool and store its Z reference."""
        travel_z = self.switch_probe_z_start + self.scan_safe_z
        with self._retreating():
            with self._phase(gcmd, tool, 'switch probing', sweeping):
                self._query_switch(gcmd)
                self._move(
                    self.switch_x, self.switch_y, travel_z, self.z_speed)
                self._move(self.switch_x, self.switch_y,
                           self.switch_probe_z_start, self.z_speed)
                trigger_z, counted, press_spread = self._probe_switch(
                    gcmd, debug)
                self._move(
                    self.switch_x, self.switch_y, travel_z, self.z_speed)
            with self._phase(gcmd, tool, 'measurement', sweeping):
                center_x, center_y, agg = self._measure_xy(gcmd, debug)
                curve, agg_z = self._measure_z_curve(gcmd, center_x, center_y)
        self._merge_aggregate(agg, agg_z)
        anchor_height, anchor_freq = switch_anchor(curve, trigger_z)
        record = {
            'anchor_height': anchor_height,
            'anchor_frequency': anchor_freq,
            'trigger_z': trigger_z,
            'curve_low_z': curve[0][0],
            'curve_high_z': curve[-1][0],
            'center_x': center_x,
            'center_y': center_y,
            'updated': time.strftime('%Y-%m-%dT%H:%M:%S'),
        }
        previous = self.anchors.get(tool)
        self.anchors[tool] = record
        try:
            with self._phase(gcmd, tool, 'measurement', sweeping):
                self._write_state(gcmd)
        except Exception:
            # An anchor that did not persist would be gone at the next
            # restart, so the in-memory state goes back to what it was.
            if previous is None:
                del self.anchors[tool]
            else:
                self.anchors[tool] = previous
            raise
        rows = [
            "tool: T%d" % (tool,),
            "counted press triggers (machine Z): %s mm"
            % (", ".join("%.4f" % (h,) for h in counted),),
            "press spread: %.4f mm" % (press_spread,),
            "press tolerance: %.4f mm" % (self.switch_probe_tolerance,),
            "switch trigger (machine Z): %.4f mm" % (trigger_z,),
            "center x: %.4f" % (center_x,),
            "center y: %.4f" % (center_y,),
        ]
        rows.extend(self._z_curve_rows(curve))
        rows.extend([
            "anchor height above trigger plane: %.4f mm" % (anchor_height,),
            "anchor frequency: %.3f Hz" % (anchor_freq,),
            "state file: %s" % (self._state_path(),),
        ])
        rows.extend(self._aggregate_rows(agg))
        gcmd.respond_info("\n".join(rows))

    cmd_EDDY_CALIBRATE_OFFSET_help = (
        "Measure the tool named by T= over the coil and print its offsets "
        "relative to T0, or measure every tool in turn when T= is left out. "
        "T=0 measures the baseline every other tool is compared against, so "
        "run it first. Add DEBUG=1 to print each scan pass's diagnostic rows.")

    def cmd_EDDY_CALIBRATE_OFFSET(self, gcmd):
        self._ensure_homed(gcmd)
        debug = self._debug_flag(gcmd)
        tool = self._optional_tool_index(gcmd)
        if tool is None:
            tools = self._sweep_tools(gcmd, 'EDDY_CALIBRATE_OFFSET')
            sweeping = True
        else:
            tools = [tool]
            sweeping = False
        if BASELINE_TOOL not in tools and self.baseline is None:
            raise gcmd.error(
                "Run EDDY_CALIBRATE_OFFSET T=%d first, with the baseline tool "
                "mounted. Offsets are measured against that tool and it has "
                "not been calibrated in this session."
                % (BASELINE_TOOL,))
        if self.calibrate_z:
            # Checked before any motion, so a machine that is only half
            # anchored fails in a second rather than partway through the run.
            needed = list(tools)
            if BASELINE_TOOL not in tools:
                needed.append(self.baseline['tool'])
            self._require_anchors(gcmd, needed)
        summary = []
        for tool in tools:
            summary.append(
                self._calibrate_one_offset(gcmd, tool, debug, sweeping))
        if sweeping:
            gcmd.respond_info("\n".join(fleet_summary_rows(summary)))

    def _calibrate_one_offset(self, gcmd, tool, debug, sweeping):
        """Measure one tool's offsets, report them, and apply them.

        Returns the tool's entry for the fleet summary: its offsets, or None
        for the baseline tool, whose offsets are zero by definition.
        """
        with self._phase(gcmd, tool, 'toolchange', sweeping):
            self._mount_tool(gcmd, tool)
        is_baseline_run = tool == BASELINE_TOOL
        with self._phase(gcmd, tool, 'measurement', sweeping):
            with self._retreating():
                result = self._run_tool_measurement(gcmd, tool, debug)
        if is_baseline_run:
            # A fresh T0 run always replaces the session baseline, so the
            # comparison never mixes results from two different setups.
            self.session_id += 1
            self.baseline = {
                'tool': tool,
                'x': result['x'],
                'y': result['y'],
                'z_curve': result['z_curve'],
                'z_trigger': result['z_trigger'],
            }
        result['session_id'] = self.session_id
        result['measured_time'] = self.printer.get_reactor().monotonic()
        self.last_tool = tool
        self._report_tool_result(gcmd, tool, result, is_baseline_run)
        if is_baseline_run:
            # The baseline tool's offsets are zero by definition, and applying
            # zeros would overwrite whatever the owner set for it.
            return {'tool': tool, 'offsets': None}
        offsets = self._offsets(result)
        with self._phase(gcmd, tool, 'apply', sweeping):
            self._apply_offsets(gcmd, tool, offsets)
        return {'tool': tool, 'offsets': offsets}

    def _require_anchors(self, gcmd, tools):
        """Refuse to measure Z for a tool that has no stored anchor."""
        missing = sorted(set(t for t in tools if t not in self.anchors))
        if not missing:
            return
        raise gcmd.error(
            "Run %s first, mounting each of those tools in turn. The Z "
            "reference for %s is missing, and calibrate_z is True, so a Z "
            "offset cannot be measured without it."
            % (", ".join("EDDY_CALIBRATE_Z T=%d" % (t,) for t in missing),
               ", ".join("T%d" % (t,) for t in missing)))

    def _run_tool_measurement(self, gcmd, tool, debug):
        center_x, center_y, agg_xy = self._measure_xy(gcmd, debug)
        agg = self._new_aggregate()
        self._merge_aggregate(agg, agg_xy)
        curve = None
        z_crossing = None
        z_trigger = None
        if self.calibrate_z:
            curve, agg_z = self._measure_z_curve(gcmd, center_x, center_y)
            self._merge_aggregate(agg, agg_z)
            z_trigger, z_crossing = self._trigger_plane(gcmd, tool, curve)
        result = {
            'x': center_x,
            'y': center_y,
            'z_curve': curve,
            'z_crossing': z_crossing,
            'z_trigger': z_trigger,
            'agg': agg,
            'session_id': self.session_id,
            'measured_time': None,
        }
        self.results[tool] = result
        return result

    def _trigger_plane(self, gcmd, tool, curve):
        """Machine Z of the switch trigger plane this descent reconstructs.

        Returns (trigger_z, crossing_z). This is the single place a measured
        curve becomes a comparable height, so the printed rows, the reported
        offsets and the status readout all read the same number.
        """
        anchor = self.anchors[tool]
        try:
            return trigger_plane_from_anchor(
                curve, anchor['anchor_height'], anchor['anchor_frequency'])
        except ValueError as e:
            raise gcmd.error(
                "The stored Z reference for T%d does not fall inside this "
                "descent: %s. Run EDDY_CALIBRATE_Z T=%d again, which is "
                "usually needed because the coil or the switch moved."
                % (tool, e, tool))

    def _z_curve_rows(self, curve):
        """Labeled rows describing the measured descent curve itself."""
        return [
            "z curve steps: %d" % (len(curve),),
            "z curve range (machine Z): %.4f to %.4f mm"
            % (curve[0][0], curve[-1][0]),
            "z curve frequency range: %.3f to %.3f Hz"
            % (curve[-1][1], curve[0][1]),
        ]

    def _z_rows(self, tool, result):
        """Every Z row for one tool: curve, anchor, crossing, trigger plane."""
        if not self.calibrate_z:
            return []
        anchor = self.anchors[tool]
        rows = self._z_curve_rows(result['z_curve'])
        rows.extend([
            "anchor frequency: %.3f Hz" % (anchor['anchor_frequency'],),
            "anchor height above trigger plane: %.4f mm"
            % (anchor['anchor_height'],),
            "z crossing (machine Z): %.4f mm" % (result['z_crossing'],),
            "switch trigger plane (machine Z): %.4f mm"
            % (result['z_trigger'],),
        ])
        return rows

    def _offsets(self, result):
        """Offsets of a measured tool against the session baseline.

        The Z entry is None when calibrate_z is False, because no descent ran
        and there is no measured Z to report.
        """
        base = self.baseline
        offsets = {
            'x': result['x'] - base['x'],
            'y': result['y'] - base['y'],
            'z': None,
        }
        if self.calibrate_z:
            offsets['z'] = result['z_trigger'] - base['z_trigger']
        return offsets

    def _report_tool_result(self, gcmd, tool, result, is_baseline_run):
        rows = [
            "tool: T%d" % (tool,),
            "center x: %.4f" % (result['x'],),
            "center y: %.4f" % (result['y'],),
        ]
        rows.extend(self._z_rows(tool, result))
        if is_baseline_run:
            rows.append("offsets: baseline tool, zero by definition")
        else:
            offsets = self._offsets(result)
            rows.append("baseline tool: T%d" % (self.baseline['tool'],))
            rows.append("offset x: %+.4f" % (offsets['x'],))
            rows.append("offset y: %+.4f" % (offsets['y'],))
            if offsets['z'] is not None:
                rows.append("offset z: %+.4f" % (offsets['z'],))
        rows.extend(self._aggregate_rows(result['agg']))
        gcmd.respond_info("\n".join(rows))

    def get_status(self, eventtime):
        """Anchors and this session's measurements, for macros to read.

        This is a view of the same numbers the printed rows carry, rebuilt on
        every call. Tool numbers are decimal strings so the dicts survive JSON
        transport unchanged.
        """
        anchors = {}
        for tool, record in self.anchors.items():
            anchors[str(tool)] = {
                'anchor_height': record['anchor_height'],
                'anchor_frequency': record['anchor_frequency'],
                'trigger_z': record['trigger_z'],
                'updated': record['updated'],
            }
        tools = {}
        for tool, result in self.results.items():
            if self.baseline is None or result['session_id'] != self.session_id:
                continue
            offsets = self._offsets(result)
            tools[str(tool)] = {
                'session_id': result['session_id'],
                'center_x': result['x'],
                'center_y': result['y'],
                'z_crossing': result['z_crossing'],
                'offset_x': offsets['x'],
                'offset_y': offsets['y'],
                'offset_z': offsets['z'],
                'measured_time': result['measured_time'],
            }
        return {
            'calibrate_z': self.calibrate_z,
            # None when tool_count is not configured, which is the state a
            # machine calibrated one tool at a time stays in.
            'tool_count': self.tool_count,
            'baseline_tool': (
                None if self.baseline is None else self.baseline['tool']),
            'session_id': self.session_id,
            'last_tool': self.last_tool,
            'anchors': anchors,
            'tools': tools,
        }


def load_config(config):
    return EddyToolCalibration(config)
