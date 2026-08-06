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

"""Kalico klippy plugin: eddy-current per-tool nozzle offset calibration.

This module must import cleanly on a machine without klippy installed, so
every import of a klippy module (klippy.extras.ldc1612, motion_report and the
rest) happens inside a function or method body, never at module scope.
"""

import contextlib
import importlib
import json
import logging
import math
import os
import tempfile
import time

# ---------------------------------------------------------------------------
# Framework-agnostic math.
# ---------------------------------------------------------------------------

# Provenance: upstream tool_eddy_calibration drops samples below 1 MHz as
# invalid startup or noise readings. The threshold sits well below any real
# coil resonance of an LDC1612 eddy board, so it separates garbage from
# signal rather than trimming real data.
FREQ_MIN_DEFAULT = 1000000.0

# Provenance: upstream peak-type auto-detection compares the average of the
# scan edges against the average of the middle band, taken as 35% to 65% of
# the pass.
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


def unhandled_member(kind, value, members):
    return ValueError(
        "unhandled %s %r, expected one of %r" % (kind, value, members))


def is_batch_run(start_args):
    """Whether klippy is replaying a gcode file against a simulated MCU.

    The debugoutput start argument marks klippy's batch mode (all four
    reference builds, printer.py main: the -o option stores it), where no
    hardware answers and no sensor delivers data.
    """
    return start_args.get('debugoutput') is not None


def detect_peak_type(freqs, edge_margin):
    """Ported from upstream's auto-detection: the response is a peak when the
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
        "the frequency readings show no contrast between the middle of the "
        "pass and its edges, both average %.3f Hz, so the scan may not cross "
        "the coil" % (edge_avg,))


def find_extremum_index(freqs, peak_type, edge_margin):
    n = len(freqs)
    if n < 3:
        raise ValueError(
            "the peak search needs at least 3 samples, got %d" % (n,))
    if not 0.0 < edge_margin < 0.5:
        raise ValueError(
            "edge margin must be between 0 and 0.5, got %r" % (edge_margin,))
    margin = max(1, int(round(n * edge_margin)))
    lo = margin
    hi = n - margin
    if hi - lo < 3:
        raise ValueError(
            "the peak search window holds %d samples after trimming %d edge "
            "samples from a %d sample pass" % (max(0, hi - lo), margin, n))
    window = list(freqs[lo:hi])
    if peak_type == 'peak':
        best = max(window)
    elif peak_type == 'valley':
        best = min(window)
    else:
        raise unhandled_member('peak type', peak_type, PEAK_TYPES)
    best_idx = lo + window.index(best)
    if best_idx == lo or best_idx == hi - 1:
        raise ValueError(
            "the strongest reading lies on the edge of the search window at "
            "sample %d of %d" % (best_idx, n))
    return best_idx


def determinant_3x3(rows):
    (a, b, c), (d, e, f), (g, h, i) = rows
    return a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)


def replace_column(rows, column, values):
    return [row[:column] + [value] + row[column + 1:]
            for row, value in zip(rows, values)]


def solve_weighted_quadratic(w, wx, wx2, wx3, wx4, wy, wxy, wx2y):
    """Coefficients (a, b, c) of y = a*x^2 + b*x + c, solved by Cramer's rule
    from the weighted power sums of the samples.

    Deliberate deviation from upstream's _refine_peak_position, whose b
    determinant (reference/tool_eddy_calibration.py:834-836) is expanded
    wrongly. It reduces to the correct value only when the fit window is
    symmetric about the extremum sample (wx = wx3 = 0), and a window clipped by
    the start or the end of a pass is not symmetric, so upstream returns a
    meaningless vertex there.
    """
    matrix = [[wx4, wx3, wx2],
              [wx3, wx2, wx],
              [wx2, wx, w]]
    moments = [wx2y, wxy, wy]
    det = determinant_3x3(matrix)
    if abs(det) < FIT_DET_EPSILON:
        raise ValueError(
            "quadratic fit normal equations are singular, the readings "
            "around the peak carry no curvature")
    return tuple(
        determinant_3x3(replace_column(matrix, column, moments)) / det
        for column in range(3))


def fit_vertex_offset(freqs, peak_idx, half_window, sigma, peak_type,
                      vertex_limit):
    """Ported from upstream's _refine_peak_position: a Gaussian-weighted
    quadratic least-squares fit. Returns the vertex position as a fractional
    sample offset relative to peak_idx. Deliberate deviation from upstream,
    which clamped a vertex that fell outside the window; a clamp turns a
    failed fit into a plausible looking number, so this raises instead.
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
            "fit_sigma_fraction must be greater than 0, got %r" % (sigma,))
    if peak_type not in PEAK_TYPES:
        raise unhandled_member('peak type', peak_type, PEAK_TYPES)
    start_idx = max(0, peak_idx - half_window)
    end_idx = min(n, peak_idx + half_window + 1)
    if end_idx - start_idx < 3:
        raise ValueError(
            "only %d samples surround the peak, and the quadratic fit needs "
            "at least 3" % (end_idx - start_idx,))
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
    a, b, _c = solve_weighted_quadratic(w, wx, wx2, wx3, wx4, wy, wxy, wx2y)
    if abs(a) < FIT_CURVATURE_EPSILON:
        raise ValueError(
            "quadratic fit is flat, the readings around the peak carry no "
            "curvature")
    if peak_type == 'peak' and a > 0.0:
        raise ValueError(
            "quadratic fit opens upward around a detected peak, so the "
            "samples around it do not hold the peak")
    if peak_type == 'valley' and a < 0.0:
        raise ValueError(
            "quadratic fit opens downward around a detected valley, so the "
            "samples around it do not hold the valley")
    x_peak = -b / (2.0 * a)
    max_offset = half_window * vertex_limit
    if abs(x_peak) > max_offset:
        raise ValueError(
            "the fitted peak sits %.2f samples away from the strongest "
            "sample, past the %.2f sample limit" % (abs(x_peak), max_offset))
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
    """Fit one directional scan pass, returning a dict with peak_x, peak_y,
    peak_type, extremum_index, vertex_offset and sample_count.
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


def normalize_angle(angle_deg):
    return float(angle_deg) % 360.0


def opposite_angle(angle_deg):
    return normalize_angle(float(angle_deg) + 180.0)


def project_peak(angle_deg, px, py):
    rad = math.radians(angle_deg)
    return px * math.cos(rad) + py * math.sin(rad)


def project_peaks(peaks):
    """Project each pass peak onto its own scan axis.

    peaks is a list of (angle_deg, peak_x, peak_y). Returns a list of
    (angle_deg, projection).
    """
    if not peaks:
        raise ValueError("center reconstruction needs at least one scan pass")
    out = []
    for angle_deg, px, py in peaks:
        out.append((angle_deg, project_peak(angle_deg, px, py)))
    return out


def average_paired_projections(peaks):
    """Ported from upstream's paired-averaging mode: both peaks of an opposed
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
        by_angle[normalize_angle(angle_deg)] = (px, py)
    out = []
    used = set()
    for angle in sorted(by_angle):
        if angle in used:
            continue
        px, py = by_angle[angle]
        proj = project_peak(angle, px, py)
        opposite = opposite_angle(angle)
        if opposite in by_angle and opposite not in used:
            ox, oy = by_angle[opposite]
            proj = (proj + project_peak(angle, ox, oy)) / 2.0
            used.add(opposite)
        used.add(angle)
        out.append((angle, proj))
    return out


def solve_center_lsq(projections):
    """projections is a list of (angle_deg, projection). Solves the
    overdetermined system tx*cos(t) + ty*sin(t) = projection by normal
    equations.
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
    """0 degrees runs along X+, 90 degrees along Y+. Returns
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
    """Normalize scan angles to [0, 360) and reject duplicates.

    With pair_scans the opposite of every configured angle is scanned as well,
    so a configured pair of opposites is the same duplicate one step later and
    is rejected here too.
    """
    if not angles:
        raise ValueError("at least one scan angle is required")
    out = []
    for angle in angles:
        normalized = normalize_angle(angle)
        if normalized in out:
            raise ValueError(
                "scan angle %.1f degrees is listed twice" % (normalized,))
        opposite = opposite_angle(normalized)
        if pair_scans and opposite in out:
            raise ValueError(
                "scan angles %.1f and %.1f degrees are opposites, and "
                "pair_scans already scans the opposite of every angle"
                % (opposite, normalized))
        out.append(normalized)
    return out


def expand_scan_angles(angles, pair_scans):
    out = list(angles)
    if pair_scans:
        for angle in list(angles):
            out.append(opposite_angle(angle))
    return out


def validate_vertical_geometry(scan_height, z_start, z_stop):
    """scan_height, z_start and z_stop are heights above the coil top face,
    where 0 mm is the nozzle touching the face. The z_start above z_stop
    ordering is not checked here: z_descent_targets owns the descent list and
    already rejects it.
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

    Works in whatever vertical frame the caller uses; the plugin hands it
    heights above the coil top face and converts each target to machine Z as
    it commands the move.
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


def bucket_samples_by_window(windows, samples):
    """Gather the samples each time window holds, returning (buckets, dropped).
    buckets maps the key of every window that received samples to the values it
    received, and dropped counts the samples that fell between or outside the
    windows.

    windows is a list of (start_time, end_time, key) and samples a list of
    (time, value). Both ascend in time and the windows do not overlap, so one
    walk down each sequence places every sample.
    """
    buckets = {}
    dropped = 0
    index = 0
    for start, end, key in windows:
        while index < len(samples) and samples[index][0] < start:
            index += 1
            dropped += 1
        inside = []
        while index < len(samples) and samples[index][0] <= end:
            inside.append(samples[index][1])
            index += 1
        if inside:
            buckets.setdefault(key, []).extend(inside)
    dropped += len(samples) - index
    return buckets, dropped


def samples_in_window(start, end, samples):
    """Gather the samples one time window holds, as (inside, dropped).

    Each sample is a sequence whose first element is its time, and the samples
    come back whole rather than reduced to a single value.
    """
    buckets, dropped = bucket_samples_by_window(
        [(start, end, 'window')], [(sample[0], sample) for sample in samples])
    return buckets.get('window', []), dropped


def build_z_curve(points):
    """points is a list of (z, frequency). Returns the list sorted by ascending
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


def require_interpolable_curve(curve):
    if len(curve) < 2:
        raise ValueError(
            "Z curve needs at least 2 steps to interpolate, got %d"
            % (len(curve),))


def z_curve_freq_at(curve, z):
    require_interpolable_curve(curve)
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
    require_interpolable_curve(curve)
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
    """Returns (z, frequency) at the midpoint by height of the measured range.

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

# A first press on a cold or unseated switch travels differently from the
# rest, so it is a warm-up rather than a measurement and is dropped by its
# position in the sequence rather than by any test on its value.
SWITCH_PRESS_COUNT = 4
SWITCH_PRESS_DISCARDED = 1


def aggregate_switch_presses(heights, tolerance):
    """Median trigger height of the counted presses of one switch probing.

    heights holds the trigger height of every press in the order it was made.
    Returns (median, counted, spread), where counted holds the presses that
    were kept.
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

    What is returned is the height above the switch trigger plane rather than
    a machine Z, so the switch's own height cancels out of every comparison
    between two tools.
    """
    anchor_z, anchor_freq = z_curve_shared_reference(curve)
    return anchor_z - float(trigger_z), anchor_freq


def trigger_plane_from_anchor(curve, anchor_height, anchor_frequency):
    """Machine Z of the trigger plane a stored anchor implies for a curve.

    Returns (trigger_z, crossing_z).
    """
    crossing = z_curve_z_at_freq(curve, anchor_frequency)
    return crossing - float(anchor_height), crossing


# --- nozzle temperature ----------------------------------------------------


def default_tool_extruder(tool):
    """Klipper's extruder section name for a tool number: extruder,
    extruder1, extruder2 and so on.
    """
    index = int(tool)
    if index < 0:
        raise ValueError("tool numbers start at 0, got %r" % (tool,))
    if index == 0:
        return 'extruder'
    return 'extruder%d' % (index,)


def parse_tool_extruders(text, tool_count):
    """Heater section names indexed by tool number, from tool_extruders.

    Returns None when the option is not set, which leaves every tool on
    Klipper's own extruder naming.
    """
    if text is None:
        return None
    names = [name.strip() for name in str(text).split(',')]
    for name in names:
        if not name:
            raise ValueError(
                "tool_extruders holds an empty entry in %r" % (text,))
    if tool_count is not None and len(names) < int(tool_count):
        raise ValueError(
            "tool_extruders lists %d heater names and tool_count is %d, so "
            "T%d has no heater" % (len(names), int(tool_count), len(names)))
    return names


def tool_extruder_name(names, tool):
    if names is None:
        return default_tool_extruder(tool)
    index = int(tool)
    if index < 0 or index >= len(names):
        raise ValueError(
            "tool_extruders lists %d heater names, so T%d has none"
            % (len(names), index))
    return names[index]


def temperature_warning(tool, anchored_setpoint, configured_setpoint):
    """Warning text when a tool was anchored at another setpoint.

    Both values are setpoints, so they differ only when the option was changed
    after the tool was anchored, and any difference at all is reported rather
    than absorbed by a margin.
    """
    if float(anchored_setpoint) == float(configured_setpoint):
        return None
    return (
        "warning: the Z reference for T%d was measured with calibration_temp "
        "at %.1f C, and calibration_temp is now %.1f C. This run heats T%d "
        "to %.1f C, the temperature its Z reference was measured at. Run "
        "EDDY_CALIBRATE_Z T=%d to measure the reference at the configured "
        "calibration_temp instead."
        % (int(tool), float(anchored_setpoint), float(configured_setpoint),
           int(tool), float(anchored_setpoint), int(tool)))


def temperature_in_band(reading, setpoint, band):
    """Whether a nozzle reading counts as being at its setpoint.

    The band applies in both directions, so a nozzle that overshot only has to
    fall back inside it. A hotend under PID control wanders around its
    setpoint instead of sitting on it, so a band narrower than that wander is
    a wait for a coincidence.
    """
    return abs(float(reading) - float(setpoint)) <= float(band)


def preheat_plan_rows(entries, band, settle_time):
    """Labeled rows a preheat prints before it starts waiting.

    entries is a list of (tool, heater section name, current reading,
    setpoint), one per tool the preheat covers.
    """
    if not entries:
        raise ValueError("a preheat plan needs at least one tool")
    rows = ["heating every listed tool before measuring:"]
    for tool, name, current, setpoint in entries:
        rows.append(
            "T%d heater %s: %.1f C now, %.1f C target"
            % (int(tool), name, float(current), float(setpoint)))
    rows.append(
        "temperature band: %.1f C either side of the target temperature"
        % (float(band),))
    rows.append(
        "settle time after reaching the band: %.1f s" % (float(settle_time),))
    rows.append(
        "The command waits for every listed tool to read inside its band, "
        "including a tool that has to cool into it.")
    return rows


# --- firmware surface: the sensor driver import ----------------------------

SENSOR_IMPORT_STRATEGIES = (
    ('klippy_package', 'klippy.extras.ldc1612'),
    ('extras_package', 'extras.ldc1612'),
)
SENSOR_IMPORT_STRATEGY_NAMES = tuple(
    name for name, _path in SENSOR_IMPORT_STRATEGIES)


def resolve_sensor_import(import_module):
    """The strategy that imports the ldc1612 driver module on this firmware
    build and the module it imports, as (strategy name, module).

    Provenance: Kalico ships klippy/__init__.py, so its modules import under
    the klippy package; stock Klipper does not, and klippy.py:103 in v0.13.0
    and on master imports extras modules as extras. plus the name.
    """
    for strategy, path in SENSOR_IMPORT_STRATEGIES:
        try:
            return strategy, import_module(path)
        except ImportError as e:
            # An ImportError whose missing name is the path itself, or a
            # package on the way to it, means this build does not spell the
            # module that way. Any other missing name comes from inside a
            # driver module that does exist, and hiding it would blame the
            # firmware for the driver's own broken dependency.
            missing = getattr(e, 'name', None)
            if missing is not None and (
                    path == missing or path.startswith(missing + '.')):
                continue
            raise
    raise ValueError(
        "the sensor driver import cannot be resolved on this firmware "
        "build.\n"
        "strategies this plugin knows: %s\n"
        "klippy.extras.ldc1612 imports: no\n"
        "extras.ldc1612 imports: no\n"
        "Report this with the firmware name and version, or install a "
        "firmware version the requirements name."
        % (", ".join(SENSOR_IMPORT_STRATEGY_NAMES),))


# --- firmware surface: the motion queue dictionary --------------------------

MOTION_QUEUE_STRATEGIES = ('trapqs', 'dtrapqs')


def resolve_motion_queue(motion_report):
    """The strategy that reaches the motion queue dictionary on this firmware
    build, as the attribute name it is published under.

    Provenance: Kalico development and Kalico 3b98cf51 motion_report.py:210
    and Klipper v0.13.0 motion_report.py:137 name the dictionary trapqs;
    Klipper master motion_report.py:149 names it dtrapqs.
    """
    if hasattr(motion_report, 'trapqs'):
        return 'trapqs'
    if hasattr(motion_report, 'dtrapqs'):
        return 'dtrapqs'
    raise ValueError(
        "the motion queue cannot be resolved on this firmware build.\n"
        "strategies this plugin knows: %s\n"
        "motion_report carries trapqs: no\n"
        "motion_report carries dtrapqs: no\n"
        "Report this with the firmware name and version, or install a "
        "firmware version the requirements name."
        % (", ".join(MOTION_QUEUE_STRATEGIES),))


# --- firmware surface: the preheat wait -------------------------------------

PREHEAT_WAIT_STRATEGIES = ('printer_wait_while', 'reactor_poll')


def resolve_preheat_wait(printer, heaters):
    """The strategy the nozzle preheat waits with on this firmware build.

    Provenance: Kalico development printer.py:504 and Kalico 3b98cf51
    printer.py:624 carry printer.wait_while, the primitive Kalico's own heater
    wait polls on; stock Klipper carries no wait_while and its heater wait
    polls the reactor directly (heaters.py:348-351 in v0.13.0, :356-359 on
    master). Both strategies emit heaters._get_temp, the M105 line of that
    wait, on every poll, so a build without it fails the row whichever wait
    it has.
    """
    has_wait_while = callable(getattr(printer, 'wait_while', None))
    has_get_temp = callable(getattr(heaters, '_get_temp', None))
    if has_get_temp:
        if has_wait_while:
            return 'printer_wait_while'
        return 'reactor_poll'
    raise ValueError(
        "the preheat wait cannot be resolved on this firmware build.\n"
        "strategies this plugin knows: %s\n"
        "printer carries wait_while: %s\n"
        "heaters carries _get_temp: no\n"
        "Report this with the firmware name and version, or install a "
        "firmware version the requirements name."
        % (", ".join(PREHEAT_WAIT_STRATEGIES),
           "yes" if has_wait_while else "no"))


def preheat_poll_wait(reactor, is_shutdown, waiting, error, description):
    """Poll waiting(eventtime) once per second until it returns False.

    Provenance: transcription of stock Klipper's heater wait loop,
    heaters.py:348-351 in v0.13.0 and :356-359 on master, which polls
    reactor.pause(eventtime + 1.) while the printer is not shut down. One
    deviation: a wait ended by a shutdown raises instead of returning,
    because the caller measures as soon as the wait returns.
    """
    eventtime = reactor.monotonic()
    while waiting(eventtime):
        if is_shutdown():
            raise error(
                "The printer shut down while waiting for %s." % (description,))
        eventtime = reactor.pause(eventtime + 1.)


# --- firmware surface: the LDC1612 reference clock -------------------------

SENSOR_CLOCK_STRATEGIES = (
    'driver_clock_freq', 'driver_frequency', 'module_clock')


def resolve_sensor_clock(driver, module):
    """The strategy that reaches the LDC1612 reference clock on this firmware
    build and the clock it reads, as (strategy name, clock in Hz).

    Provenance: Kalico development ldc1612.py:103 and Klipper master
    ldc1612.py:91 hold the configured clock as the clock_freq attribute;
    Klipper v0.13.0 ldc1612.py:90 holds it as the frequency attribute; Kalico
    3b98cf51 holds it only as the module constant LDC1612_FREQ
    (ldc1612.py:16).
    """
    if hasattr(driver, 'clock_freq'):
        return 'driver_clock_freq', float(driver.clock_freq)
    if hasattr(driver, 'frequency'):
        return 'driver_frequency', float(driver.frequency)
    if hasattr(module, 'LDC1612_FREQ'):
        return 'module_clock', float(module.LDC1612_FREQ)
    raise ValueError(
        "the sensor clock cannot be resolved on this firmware build.\n"
        "strategies this plugin knows: %s\n"
        "ldc1612 driver carries clock_freq: no\n"
        "ldc1612 driver carries frequency: no\n"
        "ldc1612 module carries LDC1612_FREQ: no\n"
        "Report this with the firmware name and version, or install a "
        "firmware version the requirements name."
        % (", ".join(SENSOR_CLOCK_STRATEGIES),))


# --- persisted calibration state -------------------------------------------

STATE_DIR = 'EddyToolCalibration'
STATE_FILENAME = 'calibration_state.json'

STATE_VERSION = 1

# Every field of an anchor record, the type the state file stores it as, and
# whether get_status publishes it. sensor_clock and drive_current are withheld
# because an anchor frequency describes a height only under the sensor
# settings it was taken with, a comparison the plugin makes itself rather than
# leaving to a macro, and the curve bounds and the fitted center are
# diagnostics of the run that measured the anchor.
ANCHOR_FIELDS = (
    ('anchor_height', float, True),
    ('anchor_frequency', float, True),
    ('setpoint_temperature', float, True),
    ('sensor_clock', float, False),
    ('drive_current', float, False),
    ('observed_temperature', float, True),
    ('trigger_z', float, True),
    ('curve_low_z', float, False),
    ('curve_high_z', float, False),
    ('center_x', float, False),
    ('center_y', float, False),
    ('updated', str, True),
)
ANCHOR_NUMBER_FIELDS = tuple(
    name for name, convert, _status in ANCHOR_FIELDS if convert is float)
ANCHOR_TEXT_FIELDS = tuple(
    name for name, convert, _status in ANCHOR_FIELDS if convert is str)
ANCHOR_STATUS_FIELDS = tuple(
    name for name, _convert, status in ANCHOR_FIELDS if status)


def anchor_record(curve, trigger_z, setpoint_temperature,
                  observed_temperature, sensor_clock, drive_current,
                  center_x, center_y):
    """The Z reference one EDDY_CALIBRATE_Z run stores for a tool.

    curve is the descent the run measured and trigger_z the machine Z of the
    switch trigger plane it pressed, which together fix the anchor pair.
    """
    anchor_height, anchor_frequency = switch_anchor(curve, trigger_z)
    return {
        'anchor_height': anchor_height,
        'anchor_frequency': anchor_frequency,
        'setpoint_temperature': setpoint_temperature,
        'sensor_clock': sensor_clock,
        'drive_current': drive_current,
        'observed_temperature': observed_temperature,
        'trigger_z': trigger_z,
        'curve_low_z': curve[0][0],
        'curve_high_z': curve[-1][0],
        'center_x': center_x,
        'center_y': center_y,
        'updated': log_timestamp(),
    }


def require_anchor_field(tool, record, field):
    if field not in record:
        raise ValueError(
            "the anchor for T%d is missing the %s field" % (tool, field))
    return record[field]


def anchor_status(tool, record):
    """The fields of an anchor record a macro reads through get_status."""
    return dict((name, require_anchor_field(tool, record, name))
                for name in ANCHOR_STATUS_FIELDS)


def missing_anchor_message(tools):
    """Refusal text when the Z reference of one or more tools was never
    measured.
    """
    if not tools:
        raise ValueError(
            "a missing anchor message needs at least one tool number")
    return (
        "Run %s first, mounting each of those tools in turn. The Z reference "
        "for %s is missing, and calibrate_z is True, so a Z offset cannot be "
        "measured without it."
        % (", ".join("EDDY_CALIBRATE_Z T=%d" % (t,) for t in tools),
           ", ".join("T%d" % (t,) for t in tools)))


def anchor_sensor_mismatch(tool, anchor, sensor_clock, drive_current):
    """Refusal text when an anchor's sensor settings are not the live ones.

    A coil's frequency at a given height depends on how hard the coil is
    driven and on the reference clock the count is scaled by, so an anchor
    frequency describes a height only under the settings it was taken with.
    Returns None when both match.
    """
    stored_clock = float(anchor['sensor_clock'])
    stored_current = float(anchor['drive_current'])
    if (stored_clock == float(sensor_clock)
            and stored_current == float(drive_current)):
        return None
    return (
        "The Z reference for T%d was measured with sensor settings that are "
        "not the ones in use now, so the frequency it stores no longer "
        "describes the same height.\n"
        "sensor clock when the Z reference was measured: %s Hz\n"
        "sensor clock now: %s Hz\n"
        "drive current when the Z reference was measured: %s\n"
        "drive current now: %s\n"
        "Run EDDY_CALIBRATE_Z T=%d to measure the reference again with the "
        "settings in use now."
        % (int(tool), stored_clock, float(sensor_clock), stored_current,
           float(drive_current), int(tool)))


# A coil that moved on the bed invalidates every stored Z reference: the
# anchor frequency was measured over the old position. 0.5 mm sits far above
# the locate repeatability of the XY scan and far below the shift a remount
# of the board produces.
ANCHOR_CENTER_TOLERANCE = 0.5


def anchor_center_mismatch(tool, anchor, center_x, center_y):
    """Refusal text when an anchor's stored coil center is not where this run
    found the coil. The anchor frequency describes a height only over the
    coil position it was measured at. Returns None within tolerance.
    """
    stored_x = float(anchor['center_x'])
    stored_y = float(anchor['center_y'])
    distance = math.hypot(stored_x - float(center_x),
                          stored_y - float(center_y))
    if distance <= ANCHOR_CENTER_TOLERANCE:
        return None
    return (
        "The Z reference for T%d was measured with the coil at a different "
        "position, so the frequency it stores no longer describes the same "
        "height.\n"
        "coil position when the Z reference was measured: x %.4f, y %.4f\n"
        "coil position now: x %.4f, y %.4f\n"
        "distance: %.4f mm, more than the allowed %.4f mm\n"
        "Run EDDY_CALIBRATE_Z T=%d to measure the reference again."
        % (int(tool), stored_x, stored_y, float(center_x), float(center_y),
           distance, ANCHOR_CENTER_TOLERANCE, int(tool)))


def encode_state(anchors):
    """Serialise per-tool anchors to the state file's JSON document.

    anchors maps an integer tool number to an anchor record. Tool numbers
    become decimal strings because JSON object keys are strings.
    """
    document = {'version': STATE_VERSION, 'anchors': {}}
    for tool in sorted(anchors):
        record = anchors[tool]
        entry = {}
        for field, convert, _status in ANCHOR_FIELDS:
            entry[field] = convert(require_anchor_field(tool, record, field))
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
        if 'freq_conv' in record and 'sensor_clock' not in record:
            raise ValueError(
                "the anchor for T%d was written by an earlier plugin version "
                "that stored the frequency conversion instead of the sensor "
                "clock, and the old field cannot be converted. Run "
                "EDDY_CALIBRATE_Z T=%d to measure the reference again"
                % (tool, tool))
        entry = {}
        for field in ANCHOR_NUMBER_FIELDS:
            value = require_anchor_field(tool, record, field)
            try:
                entry[field] = float(value)
            except (TypeError, ValueError):
                raise ValueError(
                    "the anchor for T%d holds %r in its %s field, which is "
                    "not a number" % (tool, value, field))
        for field in ANCHOR_TEXT_FIELDS:
            entry[field] = str(require_anchor_field(tool, record, field))
        anchors[tool] = entry
    return anchors


def fleet_tool_order(tool_count):
    """Tool numbers a fleet run visits, in the order it visits them.

    The baseline tool comes first, which is what lets a fleet offset run
    satisfy the baseline rule on its own.
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


def parse_tool_list(text, tool_count, max_tools):
    """Tool numbers a T= parameter names, in the order it lists them.

    text is the raw parameter: a single tool number, a comma separated list of
    them, or None when T= was left out, which names every tool of the fleet.
    """
    if text is None:
        return fleet_tool_order(tool_count)
    if not str(text).strip():
        raise ValueError("T= carries no tool number")
    limit = max_tools
    if tool_count is not None:
        limit = min(int(tool_count), max_tools)
    tools = []
    for entry in str(text).split(','):
        entry = entry.strip()
        if not entry:
            raise ValueError(
                "T=%s holds an empty entry between two commas" % (text,))
        try:
            tool = int(entry)
        except ValueError:
            raise ValueError(
                "T=%s holds the entry %r, which is not a tool number"
                % (text, entry))
        if tool < 0 or tool >= limit:
            if tool_count is None:
                raise ValueError(
                    "T=%s names T%d, and the tools this plugin accepts are T0 "
                    "through T%d" % (text, tool, max_tools - 1))
            raise ValueError(
                "T=%s names T%d, and tool_count is %d, so the tools are T0 "
                "through T%d" % (text, tool, int(tool_count), limit - 1))
        if tool in tools:
            raise ValueError("T=%s names T%d twice" % (text, tool))
        tools.append(tool)
    return tools


def baseline_first(tools, baseline_tool):
    """Order a tool list so the baseline tool is measured before the rest.

    Every other tool's offsets are differences against the baseline tool, so
    it is measured first however the list was written.
    """
    ordered = [t for t in tools if t == baseline_tool]
    ordered.extend(t for t in tools if t != baseline_tool)
    return ordered


def offset_template_context(tool, offsets, calibrate_z):
    """Names an apply_offsets_gcode template is rendered with.

    With calibrate_z False no descent ran, so offset_z is left out of the
    context rather than passed as zero.
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


# --- readout rows ----------------------------------------------------------


def tool_row(tool):
    return "tool: T%d" % (int(tool),)


def center_value(coordinate):
    return "%.4f" % (coordinate,)


def center_rows(center_x, center_y, label='center'):
    return ["%s x: %s" % (label, center_value(center_x)),
            "%s y: %s" % (label, center_value(center_y))]


def coil_center_settings(center_x, center_y):
    return (('coil_x', center_value(center_x)),
            ('coil_y', center_value(center_y)))


def new_center_rows(center_x, center_y):
    return ["new %s: %s" % (option, value)
            for option, value in coil_center_settings(center_x, center_y)]


def offset_rows(offsets):
    """A Z offset no descent measured is left out rather than shown as a zero,
    which would read as a measured value.
    """
    rows = ["offset x: %+.4f" % (offsets['x'],),
            "offset y: %+.4f" % (offsets['y'],)]
    if offsets['z'] is not None:
        rows.append("offset z: %+.4f" % (offsets['z'],))
    return rows


def switch_trigger_row(trigger_z):
    return "switch trigger (machine Z): %.4f mm" % (trigger_z,)


def anchor_height_row(height):
    return "anchor height above trigger plane: %.4f mm" % (height,)


def anchor_frequency_row(frequency):
    return "anchor frequency: %.3f Hz" % (frequency,)


def anchor_rows(anchor):
    return [anchor_height_row(anchor['anchor_height']),
            anchor_frequency_row(anchor['anchor_frequency'])]


def setpoint_temperature_row(label, temperature):
    return "%s temperature setpoint: %.1f C" % (label, temperature)


def target_temperature_row(label, temperature):
    return "%s target temperature: %.1f C" % (label, temperature)


def observed_temperature_row(label, temperature):
    return "%s temperature observed: %.1f C" % (label, temperature)


def anchor_temperature_rows(label, anchor, setpoint_row):
    return [setpoint_row(label, anchor['setpoint_temperature']),
            observed_temperature_row(label, anchor['observed_temperature'])]


def fleet_summary_rows(entries):
    """Labeled per-tool rows closing a fleet run.

    entries is a list of dicts holding a tool number and either its offsets
    or None for the baseline tool, in the order the tools were measured.
    """
    if not entries:
        raise ValueError("a fleet summary needs at least one measured tool")
    rows = ["summary of all measured tools:"]
    for entry in entries:
        tool = int(entry['tool'])
        offsets = entry['offsets']
        if offsets is None:
            rows.append(
                "T%d: baseline tool, offsets zero by definition" % (tool,))
            continue
        rows.append("T%d: %s" % (tool, ", ".join(offset_rows(offsets))))
    return rows


def resolve_dir(config_dir, directory):
    """The directory a configured path names.

    A relative option is read against the printer config directory, and an
    absolute one names itself: os.path.join keeps an absolute second argument
    whole, which is what lets both spellings of one directory resolve to the
    same place.
    """
    return os.path.normpath(os.path.join(config_dir, directory))


def same_directory(config_dir, first, second):
    """Whether two configured paths name one directory.

    The comparison is case folded by os.path.normcase, so it follows the
    platform: two spellings that differ only in case are one directory on
    Windows and two on Linux.
    """
    return (os.path.normcase(resolve_dir(config_dir, first))
            == os.path.normcase(resolve_dir(config_dir, second)))


def validate_data_dir(config_dir, option, directory):
    """Reject a data directory that would sit on the state file's own."""
    if same_directory(config_dir, directory, STATE_DIR):
        raise ValueError(
            "%s %r is the directory the calibration state file lives in. "
            "Point %s at a subdirectory such as %s instead, so clearing that "
            "directory cannot take the saved Z references with it."
            % (option, directory, option,
               os.path.join(STATE_DIR, 'data').replace('\\', '/')))


def validate_log_dir(config_dir, log_dir, csv_dir):
    """Reject a log directory the scan dumps are cleared out of."""
    if same_directory(config_dir, log_dir, csv_dir):
        raise ValueError(
            "log_dir %r is the directory csv_dir names. Point log_dir at a "
            "different directory, so clearing the scan dumps cannot take the "
            "drift logs and the study files with it." % (log_dir,))


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
            "fit_window_radius must be greater than 0, got %r"
            % (window_radius,))
    return max(1, int(sample_rate / scan_speed * window_radius))


def spread(values):
    """Minimum, maximum and population standard deviation of a sample set.

    The population form is what EDDY_QUERY's frequency stream needs: those
    samples are the whole of one steady reading rather than a small sample
    drawn from a wider population.
    """
    if not values:
        raise ValueError("spread needs at least one value")
    n = len(values)
    avg = sum(values) / n
    var = sum((v - avg) ** 2 for v in values) / n
    return min(values), max(values), math.sqrt(var)


# --- sample collection -----------------------------------------------------

# Every reason a collected sample is not used, in the order the readouts list
# them, with the label each is reported under. A collection's own counters and
# the aggregate of several both carry these fields.
SAMPLE_DROP_FIELDS = (
    ('dropped_low_freq', 'dropped below freq_min'),
    ('dropped_no_position', 'dropped without a position'),
    ('dropped_outside_move', 'dropped outside the move'),
)


def drop_count_rows(counts):
    return ["%s: %d" % (label, counts[field])
            for field, label in SAMPLE_DROP_FIELDS]


def any_dropped(counts):
    return any(counts[field] for field, _label in SAMPLE_DROP_FIELDS)


# The sensor fault counters a collection reports, each beside the field its
# starting value is kept in. The driver zeroes both when it starts its sample
# stream (ldc1612.py:241 and bulk_sensor.py:278), and BatchBulkHelper._start
# returns without starting one when a stream is already running
# (bulk_sensor.py:55-57), so a collection that joins a stream another client
# opened is handed counts that predate it. What its own batches add on top of
# the first one it sees is the collection's own.
SENSOR_FAULT_FIELDS = (('errors', 'errors_before'),
                       ('overflows', 'overflows_before'))


def new_collection():
    """Zeroed sample and fault counters for one collection.

    Every field a collection carries is created here, because the readouts and
    the aggregate read them without a default.
    """
    stats = {'raw_count': 0}
    stats.update((field, 0) for field, _before in SENSOR_FAULT_FIELDS)
    stats.update((before, None) for _field, before in SENSOR_FAULT_FIELDS)
    stats.update((field, 0) for field, _label in SAMPLE_DROP_FIELDS)
    return stats


def note_batch(stats, msg):
    # ldc1612.py's _process_batch puts the totals since the stream started on
    # every batch, so the counts rise across a collection and never fall.
    for field, before in SENSOR_FAULT_FIELDS:
        if stats[before] is None:
            stats[before] = msg[field]
        stats[field] = msg[field] - stats[before]


def sample_drop_rows(stats):
    return (["raw samples: %d" % (stats['raw_count'],)]
            + drop_count_rows(stats))


def new_aggregate():
    """Zeroed counters for the several collections one command runs.

    Every field an aggregate carries is created here, because
    add_to_aggregate and merge_aggregate read them without a default.
    """
    agg = {'samples_used': 0}
    agg.update((field, 0) for field, _label in SAMPLE_DROP_FIELDS)
    return agg


def add_to_aggregate(agg, stats, kept_count):
    agg['samples_used'] += kept_count
    for field, _label in SAMPLE_DROP_FIELDS:
        agg[field] += stats[field]


def merge_aggregate(agg, other):
    for key in agg:
        agg[key] += other[key]


def aggregate_rows(agg):
    """Labeled summary rows: total samples used, and drops if any."""
    rows = ["samples used: %d" % (agg['samples_used'],)]
    if any_dropped(agg):
        rows.extend(drop_count_rows(agg))
    return rows


def wiring_advice(evidence):
    return ("Check the sensor wiring and the I2C bus configuration. %s"
            % (evidence,))


def drive_current_advice(chip, evidence):
    return ("Lower freq_min, or run LDC_CALIBRATE_DRIVE_CURRENT CHIP=%s. %s"
            % (chip, evidence))


def no_sample_cause(stats, chip):
    """Name the likely cause when a collection yielded nothing usable."""
    if stats['raw_count'] == 0:
        return wiring_advice("The sensor delivered no samples at all.")
    if stats['dropped_no_position'] > stats['dropped_low_freq']:
        return ("Re-run the command after homing, and leave the printer idle "
                "while it runs. The toolhead motion queue did not cover the "
                "sample timestamps.")
    if stats['dropped_low_freq'] > 0:
        return drive_current_advice(
            chip,
            "Every sample read below freq_min, so the coil may not be "
            "resonating.")
    return ("Lower scan_speed or raise scan_length. Samples arrived but none "
            "of them fell inside the scan move's time window.")


def descent_gap_cause(stats, chip):
    """Name the likely cause when a descent left some steps without data."""
    if stats['raw_count'] == 0:
        return wiring_advice(
            "The sensor delivered no samples during the descent.")
    if stats['dropped_low_freq'] == stats['raw_count']:
        return drive_current_advice(
            chip, "Every descent sample read below freq_min.")
    return ("Raise z_step above the printer's Z resolution. Steps finer than "
            "the kinematics can resolve land on the same height and collapse "
            "into one measurement.")


# --- machine resolution ----------------------------------------------------


def step_distance_rows(step_distances):
    """Labeled rows naming how far one microstep moves each XY stepper.

    step_distances is a list of (stepper section name, millimetres per step).
    Each row is that one stepper's own microstep distance, which is the
    machine's positional quantum only where a single stepper drives the axis
    on its own: on kinematics that drive an axis from a combination of
    steppers, CoreXY among them, the quantum is a combination of the listed
    distances rather than either one.
    """
    return ["stepper microstep distance %s: %.6f mm" % (name, distance)
            for name, distance in step_distances]


# --- move timing -----------------------------------------------------------

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


def machine_z(coil_z, height):
    """Machine Z of a height above the coil top face.

    This is the only place the configured frame becomes a machine coordinate.
    Heights read back off the kinematics during a descent are already machine
    Z and never pass through here.
    """
    return coil_z + height


def approach_z(coil_z, height):
    return machine_z(coil_z, height + Z_APPROACH_HOP)


def retreat_z(coil_z, z_start):
    return approach_z(coil_z, z_start)


# --- measurement logs ------------------------------------------------------


def log_timestamp(seconds=None):
    """The timestamp a log row or an anchor record carries.

    UTC with a trailing Z rather than local time: a local time without an
    offset reorders a log across a daylight saving rollback.
    """
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(seconds))


# The drift log's columns, in file order, each with the format its values are
# written in. A value that was not measured is written as an empty field
# rather than a zero. baseline_session names the session whose baseline
# measurement the offsets were taken against, so a drift in the baseline is
# not read as a drift of the tool.
HISTORY_COLUMNS = (
    ('timestamp', '%s'),
    ('command', '%s'),
    ('center_x', '%.4f'),
    ('center_y', '%.4f'),
    ('offset_x', '%.4f'),
    ('offset_y', '%.4f'),
    ('z_crossing', '%.4f'),
    ('trigger_z', '%.4f'),
    ('offset_z', '%.4f'),
    ('baseline_session', '%d'),
    ('setpoint_temperature', '%.1f'),
    ('observed_temperature', '%.1f'),
    ('samples_used', '%d'),
)

STUDY_COLUMNS = (('cycle', '%d'), ('run', '%d')) + HISTORY_COLUMNS


def offset_fields(offsets):
    """The offset fields of a record: empty when offsets is None, because a
    tool that reported no offsets must not read as one measured at zero.
    """
    return {
        'offset_x': None if offsets is None else offsets['x'],
        'offset_y': None if offsets is None else offsets['y'],
        'offset_z': None if offsets is None else offsets['z'],
    }


def csv_header(columns):
    return ",".join(name for name, _format in columns) + "\n"


def csv_row(columns, entry):
    """One CSV line for a column layout.

    The fields are joined on commas and never quoted, which holds only because
    every string column of these layouts is produced by this plugin: a
    timestamp and a gcode command name, neither of which can carry a comma, a
    quote or a newline. A layout that ever takes free text from elsewhere
    needs the csv module here instead.
    """
    known = set(name for name, _format in columns)
    unknown = sorted(set(entry) - known)
    if unknown:
        raise ValueError(
            "the log row carries the unknown field %s" % (", ".join(unknown),))
    fields = []
    for name, value_format in columns:
        if name not in entry:
            raise ValueError("the log row is missing the %s field" % (name,))
        value = entry[name]
        if value is None:
            fields.append('')
            continue
        fields.append(value_format % (value,))
    return ",".join(fields) + "\n"


def next_numbered_index(existing, prefix, suffix):
    """The index a new numbered file takes, read off the names already there.

    The highest index in use plus one, never the smallest free one: a free
    index below the highest is a gap a deleted file left, and reusing it would
    place a newer file below an older one.
    """
    highest = 0
    for name in existing:
        if not name.startswith(prefix) or not name.endswith(suffix):
            continue
        index = name[len(prefix):len(name) - len(suffix)]
        # str.isdigit accepts digits int() cannot parse, superscripts among
        # them, so an index is only read off a name that is plain ASCII digits.
        if not index.isascii() or not index.isdigit():
            continue
        highest = max(highest, int(index))
    return highest + 1


def rotated_log_path(path, existing):
    """The sibling name an outdated log file is renamed to.

    history_T0.csv becomes history_T0.1.csv, history_T0.2.csv and so on.
    existing holds the file names already in the directory the log lives in.
    """
    root, ext = os.path.splitext(path)
    prefix = os.path.basename(root) + "."
    return "%s.%d%s" % (root, next_numbered_index(existing, prefix, ext), ext)


def append_csv(path, columns, entry):
    """Append one measurement to a log file, creating it with its header.

    Returns the path the old file was renamed to when its header no longer
    matched columns (see rotated_log_path), or None when no rotation was
    needed.

    Raises OSError, which the callers turn into a gcode error naming what was
    already completed. ValueError still propagates from csv_row, for an entry
    with an unknown or missing field.
    """
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    header = csv_header(columns)
    header_needed = True
    rotated_to = None
    if os.path.exists(path) and os.path.getsize(path) > 0:
        header_needed = False
        with open(path, 'r') as f:
            first_line = f.readline()
        if first_line != header:
            rotated_to = rotated_log_path(path, os.listdir(directory))
            os.rename(path, rotated_to)
            header_needed = True
    line = csv_row(columns, entry)
    with open(path, 'a') as f:
        if header_needed:
            f.write(header)
        f.write(line)
    return rotated_to


def history_filename(tool):
    return "history_T%d.csv" % (int(tool),)


def next_study_filename(existing, tool):
    """File name for a repeatability study that cannot overwrite an earlier one.

    existing holds the file names already in the directory.
    """
    prefix = "repeatability_T%d_" % (int(tool),)
    suffix = ".csv"
    return "%s%03d%s" % (
        prefix, next_numbered_index(existing, prefix, suffix), suffix)


# --- repeatability studies -------------------------------------------------

# Every state a study's docking can be in: whether the study docks the
# measured tool between its cycles, and the reason it does not when it cannot.
DOCKING_STATES = (
    ('through_tool', True, None),
    ('no_other_tool', False, "tool_count names no second tool to dock "
                             "through"),
    ('no_toolchange_gcode', False, "toolchange_gcode is not set"),
)


def study_docking(tool, tool_count, has_toolchange_gcode):
    """The tool a study docks through, as (state, tool number).

    The state is one named by DOCKING_STATES; the tool number is None whenever
    no docking can run.
    """
    if not has_toolchange_gcode:
        return 'no_toolchange_gcode', None
    if tool_count is not None:
        for candidate in range(int(tool_count)):
            if candidate != int(tool):
                return 'through_tool', candidate
    return 'no_other_tool', None


def docking_state(state):
    """What one docking state means, as (docks, reason)."""
    for name, docks, reason in DOCKING_STATES:
        if name == state:
            return docks, reason
    raise unhandled_member(
        'docking state', state,
        tuple(name for name, _docks, _reason in DOCKING_STATES))


def study_docks(state):
    docks, _reason = docking_state(state)
    return docks


def docking_row(state, docking_tool, cycles):
    """The labeled row saying whether a study exercised the docking."""
    docks, reason = docking_state(state)
    if docks:
        return ("docking between cycles: each cycle mounts T%d and remounts "
                "the measured tool" % (int(docking_tool),))
    if cycles > 1:
        return ("docking between cycles: not exercised, %s, so the cycles "
                "measure drift over time instead" % (reason,))
    return "docking between cycles: not exercised, %s" % (reason,)


# Whether a study heats the measured tool before it measures, and why it does
# not when it cannot.
HEATING_STATES = ('to_anchor_temperature', 'no_anchor', 'z_calibration_off')


def study_heating(calibrate_z, has_anchor):
    """Whether a study heats the measured tool, as a state."""
    if not calibrate_z:
        return 'z_calibration_off'
    if not has_anchor:
        return 'no_anchor'
    return 'to_anchor_temperature'


def study_heats(state):
    if state == 'to_anchor_temperature':
        return True
    if state == 'no_anchor':
        return False
    if state == 'z_calibration_off':
        return False
    raise unhandled_member('heating state', state, HEATING_STATES)


# What each axis of a study reads off one measurement. The Z figure is the
# reconstructed switch trigger plane, the height a Z offset is the difference
# of, so its spread is the spread of the offset the run would have reported.
STUDY_AXIS_FIELDS = (('x', 'x'), ('y', 'y'), ('z', 'z_trigger'))
STUDY_AXIS_LABELS = tuple(label for label, _field in STUDY_AXIS_FIELDS)


def study_axes(include_z):
    """The axes a study summarises, in the order it reports them."""
    return [label for label in STUDY_AXIS_LABELS
            if label != 'z' or include_z]


def study_axis_field(axis):
    for label, field in STUDY_AXIS_FIELDS:
        if label == axis:
            return field
    raise unhandled_member('study axis', axis, STUDY_AXIS_LABELS)


def reports_offsets(tool, baseline_tool, has_baseline):
    """Whether a measurement of a tool reports offsets against the baseline.

    The baseline tool's own offsets are zero by definition, so they are left
    out rather than written as zeros, and a run with no baseline measurement
    in the session has nothing to difference against at all.
    """
    return int(tool) != int(baseline_tool) and has_baseline


def measured_offsets(tool, baseline_tool, result, baseline, include_z):
    """Offsets of a measured tool against the session baseline, or None when
    the measurement reports none.

    The Z entry is None whenever no descent ran. include_z says whether one
    did: it is calibrate_z for a calibration run, and a repeatability study
    may turn the descent off on top of that.
    """
    if not reports_offsets(tool, baseline_tool, baseline is not None):
        return None
    offsets = {
        'x': result['x'] - baseline['x'],
        'y': result['y'] - baseline['y'],
        'z': None,
    }
    if include_z:
        offsets['z'] = result['z_trigger'] - baseline['z_trigger']
    return offsets


def repeatability_statistics(cycles):
    """Split the variation of a study into measurement and docking.

    This is the gauge repeatability and reproducibility decomposition of the
    AIAG measurement systems analysis manual, in its one-way analysis of
    variance form: the runs inside a cycle differ by the measurement alone
    (repeatability), and the cycle means differ by the measurement plus
    whatever the docking between cycles adds (reproducibility).

    cycles is a list of one list of values per cycle, every cycle holding the
    same number of runs. Returns a dict holding the grand mean, the pooled
    within-cycle standard deviation, the standard deviation of the cycle
    means, the docking component that follows from the two, the range and the
    largest deviation from the grand mean.

    The docking component is the between-cycle variance component of that
    decomposition: the observed variance of the cycle means carries the
    measurement's own variance divided by the number of runs, so that share
    is subtracted. The manual's convention when the subtraction leaves nothing
    is followed here: the component is reported as zero and flagged as
    unresolved, never as a negative variance.
    """
    if not cycles:
        raise ValueError("a repeatability study needs at least one cycle")
    run_count = len(cycles[0])
    if run_count < 2:
        raise ValueError(
            "a repeatability study needs at least 2 runs per cycle, got %d"
            % (run_count,))
    for index, runs in enumerate(cycles):
        if len(runs) != run_count:
            raise ValueError(
                "cycle %d holds %d runs and cycle 1 holds %d, and the "
                "decomposition needs the same number of runs in every cycle"
                % (index + 1, len(runs), run_count))
        # A value that is not finite passes every ordered comparison the
        # summary makes and comes back out as a confident zero spread beside a
        # plausible range, so it is refused here.
        for run_index, value in enumerate(runs):
            if not math.isfinite(value):
                raise ValueError(
                    "cycle %d run %d measured %r, which is not a finite "
                    "number" % (index + 1, run_index + 1, value))
    cycle_count = len(cycles)
    values = [value for runs in cycles for value in runs]
    grand_mean = sum(values) / len(values)
    cycle_means = [sum(runs) / run_count for runs in cycles]
    within_ss = sum((value - mean) ** 2
                    for runs, mean in zip(cycles, cycle_means)
                    for value in runs)
    within_variance = within_ss / (cycle_count * (run_count - 1))
    mean_spread = None
    between = None
    between_resolved = None
    if cycle_count > 1:
        mean_variance = (sum((mean - grand_mean) ** 2 for mean in cycle_means)
                         / (cycle_count - 1))
        mean_spread = math.sqrt(mean_variance)
        component = mean_variance - within_variance / run_count
        between_resolved = component > 0.0
        between = math.sqrt(component) if between_resolved else 0.0
    return {
        'cycle_count': cycle_count,
        'run_count': run_count,
        'mean': grand_mean,
        'within': math.sqrt(within_variance),
        'cycle_mean_spread': mean_spread,
        'between': between,
        'between_resolved': between_resolved,
        'between_dof': None if cycle_count < 2 else cycle_count - 1,
        'range': max(values) - min(values),
        'max_deviation': max(abs(value - grand_mean) for value in values),
    }


def measurement_progress_row(cycle, cycles, run, runs):
    """The labeled row printed just before one measurement of a study runs.

    cycle and run are the 1-based position of the measurement about to start;
    cycles and runs are the study's totals.
    """
    return ("progress: cycle %d of %d, measurement %d of %d"
            % (cycle, cycles, run, runs))


def measurement_spread_row(label, stats):
    return "%s measurement spread: %.4f mm" % (label, stats['within'])


def docking_spread_row(label, stats):
    if stats['cycle_mean_spread'] is None:
        return ("%s docking spread: not measured, the study ran one cycle"
                % (label,))
    if stats['between_resolved']:
        return ("%s docking spread: %.4f mm (%d degrees of freedom)"
                % (label, stats['between'], stats['between_dof']))
    if stats['within'] == 0.0 and stats['cycle_mean_spread'] == 0.0:
        return ("%s docking spread: 0.0000 mm (%d degrees of freedom), the "
                "measurements did not vary at all"
                % (label, stats['between_dof']))
    return ("%s docking spread: 0.0000 mm (%d degrees of freedom), the "
            "cycle means differ by no more than the measurement itself"
            % (label, stats['between_dof']))


def worst_deviation_row(label, stats):
    return ("%s worst deviation from the mean: %.4f mm"
            % (label, stats['max_deviation']))


def repeatability_summary_rows(tool, runs, cycles, state, docking_tool,
                               include_z, axes, stats_by_axis,
                               step_distances, path):
    rows = [
        "repeatability summary:",
        tool_row(tool),
        "runs per cycle: %d" % (runs,),
        "cycles: %d" % (cycles,),
        "measurements: %d" % (runs * cycles,),
        "z descent: %s" % ("included" if include_z else "skipped",),
        docking_row(state, docking_tool, cycles),
        "",
    ]
    rows.extend(
        measurement_spread_row(axis, stats_by_axis[axis]) for axis in axes)
    rows.extend(
        docking_spread_row(axis, stats_by_axis[axis]) for axis in axes)
    rows.extend(
        worst_deviation_row(axis, stats_by_axis[axis]) for axis in axes)
    rows.extend(step_distance_rows(step_distances))
    rows.append("measurement data: %s" % (path,))
    return rows


# --- the status document ---------------------------------------------------


def status_document(anchors, results, baseline, baseline_tool, session_id,
                    last_tool, tool_count, calibrate_z):
    """Anchors and this session's measurements, for macros to read.

    Tool numbers are decimal strings so the dicts survive JSON transport
    unchanged. A result is published only while the baseline it was measured
    against is still the session's, so a run that cleared the baseline takes
    the results that were compared against it out of the document rather than
    leaving them there with empty offsets.
    """
    tools = {}
    for tool, result in results.items():
        if baseline is None or result['session_id'] != session_id:
            continue
        tools[str(tool)] = dict(
            offset_fields(measured_offsets(
                tool, baseline_tool, result, baseline, calibrate_z)),
            session_id=result['session_id'],
            center_x=result['x'],
            center_y=result['y'],
            z_crossing=result['z_crossing'],
            measured_time=result['measured_time'],
        )
    return {
        'calibrate_z': calibrate_z,
        'tool_count': tool_count,
        'baseline_tool': None if baseline is None else baseline['tool'],
        'session_id': session_id,
        'last_tool': last_tool,
        'anchors': dict(
            (str(tool), anchor_status(tool, record))
            for tool, record in anchors.items()),
        'tools': tools,
    }


# ---------------------------------------------------------------------------
# Klippy-facing layer.
# ---------------------------------------------------------------------------

# Tool indices accepted by T=.
MAX_TOOLS = 16

# The tool every other tool's offsets are measured against.
BASELINE_TOOL = 0

# The stages a fleet run reports a failure against.
TOOL_PHASES = ('toolchange', 'switch probing', 'measurement', 'apply')

# The switch options EDDY_CALIBRATE_Z cannot run without. They are read at
# load but their absence is not a load error, because a machine that only
# wants XY offsets never needs them.
SWITCH_REQUIRED_OPTIONS = (
    'switch_pin', 'switch_x', 'switch_y', 'switch_probe_z_start')

# Chosen value, not from probe_eddy_current: half a second at the sensor's
# 250 Hz sample rate gives about 125 samples, enough for a meaningful spread
# without making a wiring check feel slow.
QUERY_COLLECT_TIME_DEFAULT = 0.500

# Chosen value: a scan pass has to cross the whole response and still leave
# margin on both sides for the fit window, and the response is about as wide
# as the bore.
SCAN_LENGTH_BORE_FACTOR = 1.5


def default_scan_length(coil_inner_diameter):
    """Default scan_length for a coil bore diameter, both in mm."""
    return coil_inner_diameter * SCAN_LENGTH_BORE_FACTOR


# Chosen value: the coarse locate pass has to cover the uncertainty in the
# configured coil position, which is much larger than the coil itself.
LOCATE_SCAN_LENGTH_FACTOR = 3.0

# The scan rounds one XY measurement runs, in order, and the label each one
# reports under.
XY_MEASUREMENT_ROUNDS = ('measurement coarse', 'measurement refine')

# The scan rounds EDDY_LOCATE runs, in order, and the label each one reports
# under. Its opening round covers locate_scan_length rather than scan_length,
# so these rounds carry labels of their own.
LOCATE_ROUNDS = ('locate coarse', 'locate refine')


class EddyToolCalibration:

    def __init__(self, config):
        self.printer = config.get_printer()
        self.name = config.get_name()

        # Geometry. coil_x, coil_y and coil_z are machine coordinates.
        self.coil_x = config.getfloat('coil_x')
        self.coil_y = config.getfloat('coil_y')
        self.coil_inner_diameter = config.getfloat(
            'coil_inner_diameter', above=0.0)
        # Machine Z of the coil top face, the origin every other vertical
        # option in this section is measured from.
        self.coil_z = config.getfloat('coil_z')
        self.scan_height = config.getfloat('scan_height', 1.0)
        self.scan_safe_z = config.getfloat('scan_safe_z', 2.0, above=0.0)
        # 2.5 mm is where the sensor's usable range ends above the coil top
        # face; higher and the descent curve stops being monotonic.
        self.z_start = config.getfloat('z_start', 2.5)
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
        try:
            self.z_targets = z_descent_targets(
                self.z_start, self.z_stop, self.z_step)
        except ValueError as e:
            raise config.error(
                "%s: %s. Raise z_stop, lower z_start, or pick a z_step that "
                "divides the span." % (self.name, e))

        # Scan tuning.
        self.scan_speed = config.getfloat('scan_speed', 4.0, above=0.0)
        self.scan_length = config.getfloat(
            'scan_length', default_scan_length(self.coil_inner_diameter),
            above=0.0)
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
        self.save_history = config.getboolean('save_history', True)
        self.csv_dir = config.get(
            'csv_dir', os.path.join(STATE_DIR, 'data').replace('\\', '/'))
        self.log_dir = config.get(
            'log_dir', os.path.join(STATE_DIR, 'logs').replace('\\', '/'))
        try:
            validate_data_dir(self._config_dir(), 'csv_dir', self.csv_dir)
            validate_data_dir(self._config_dir(), 'log_dir', self.log_dir)
            validate_log_dir(
                self._config_dir(), self.log_dir, self.csv_dir)
        except ValueError as e:
            raise config.error("%s: %s" % (self.name, e))
        self.query_time = config.getfloat(
            'query_time', QUERY_COLLECT_TIME_DEFAULT, above=0.0)

        # Fit tuning. Deliberate deviation from upstream, which shrinks the
        # bore by a further 0.5 mm before halving it; that shrink is
        # unexplained and an unexplained constant is not carried over.
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

        self._reject_removed_options(config)

        self.calibrate_z = config.getboolean('calibrate_z', False)

        # Contact switch. switch_x, switch_y and switch_probe_z_start are
        # machine coordinates, not heights above the coil top face: the switch
        # is a separate fixture with no fixed relation to the coil.
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

        # Fleet options.
        self.tool_count = config.getint(
            'tool_count', None, minval=1, maxval=MAX_TOOLS)
        gcode_macro = self.printer.load_object(config, 'gcode_macro')
        self.toolchange_gcode = gcode_macro.load_template(
            config, 'toolchange_gcode', '')
        self.apply_offsets_gcode = gcode_macro.load_template(
            config, 'apply_offsets_gcode', '')
        # An empty template is how klippy spells "the owner did not set this
        # option", so the plain text is what says whether either was set.
        self.has_toolchange_gcode = bool(
            config.get('toolchange_gcode', '').strip())
        self.has_apply_offsets_gcode = bool(
            config.get('apply_offsets_gcode', '').strip())

        # Nozzle temperature. How the frequency reads against height depends
        # on how hot the nozzle is, and the reproducible name for a thermal
        # state is the heater setpoint: a hotend under PID control wanders
        # around its setpoint rather than sitting on it, so a reading taken at
        # any one moment is a sample of that wander.
        self.calibration_temp = config.getfloat(
            'calibration_temp', 150.0, minval=0.0)
        if self.calibrate_z and self.calibration_temp <= 0.0:
            raise config.error(
                "%s: set calibration_temp above 0, or set calibrate_z to "
                "False. A Z reference measured with the nozzle cold could "
                "only be compared against a later measurement taken cold as "
                "well, and a nozzle that has been hot does not come back to "
                "cold inside a calibration run." % (self.name,))
        self.calibration_temp_band = config.getfloat(
            'calibration_temp_band', 2.0, above=0.0)
        # The heater block reaches its setpoint well before the nozzle tip
        # does, so both commands dwell here after the band is reached.
        self.calibration_settle_time = config.getfloat(
            'calibration_settle_time', 30.0, minval=0.0)
        try:
            self.tool_extruders = parse_tool_extruders(
                config.get('tool_extruders', None), self.tool_count)
        except ValueError as e:
            raise config.error("%s: %s." % (self.name, e))

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
        try:
            self.sensor_import_strategy, self.sensor_module = \
                resolve_sensor_import(importlib.import_module)
        except ValueError as e:
            raise config.error("%s: %s" % (self.name, e))
        self.sensor = self.sensor_module.LDC1612(config)
        # Resolved at connect behind the calibrate_z gate; a machine measuring
        # XY only stores no anchor and never reads a clock.
        self.sensor_clock = None
        self.preheat_wait_strategy = None
        # Resolved at connect whatever calibrate_z says, because every scan
        # reads it; a printer with no steppers carries no motion_report object
        # and leaves it unresolved.
        self.motion_queue_strategy = None

        # Ported from tools_calibrate.py:438-476. The pin is marked multi-use
        # before it is looked up, so an existing [tools_calibrate] section may
        # share it whichever of the two sections is built first, and
        # allow_multi_use_pin parses with no prefix allowed, so it is handed
        # the chip and pin name rather than the raw option.
        self.switch_endstop = None
        if self.switch_pin is not None:
            ppins = self.printer.lookup_object('pins')
            shared = ppins.parse_pin(
                self.switch_pin, can_invert=True, can_pullup=True)
            ppins.allow_multi_use_pin(
                '%s:%s' % (shared['chip_name'], shared['pin']))
            self.switch_endstop = ppins.setup_pin('endstop', self.switch_pin)
            self.printer.register_event_handler(
                'klippy:mcu_identify', self._handle_mcu_identify)

        # The heater sections are still being created while this one is
        # parsed, so the tool heaters are resolved at connect instead.
        self.printer.register_event_handler(
            'klippy:connect', self._handle_connect)

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
        gcode.register_command(
            'EDDY_REPEATABILITY', self.cmd_EDDY_REPEATABILITY,
            desc=self.cmd_EDDY_REPEATABILITY_help)

    # -- config and persisted state ---------------------------------------

    def _reject_removed_options(self, config):
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
                "the point where the switch triggers, which only the switch "
                "measurement "
                "defines."
                % (self.name, tool, tool, self._state_path()))

    def _config_dir(self):
        config_file = self.printer.get_start_args()['config_file']
        return os.path.dirname(os.path.abspath(config_file))

    def _state_path(self):
        return os.path.join(
            resolve_dir(self._config_dir(), STATE_DIR), STATE_FILENAME)

    def _data_dir(self):
        return resolve_dir(self._config_dir(), self.csv_dir)

    def _log_dir(self):
        return resolve_dir(self._config_dir(), self.log_dir)

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
                "%s: %s (%s). Delete that file and run EDDY_CALIBRATE_Z again "
                "for each tool, which measures every reference it held from "
                "scratch. A state file written by an earlier version of this "
                "plugin can be missing a field this version needs, and the "
                "references in it cannot be converted."
                % (self.name, e, path))

    def _write_state(self, gcmd):
        """Persist the anchors, replacing the state file atomically."""
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
        if self.motion_queue_strategy is None:
            raise gcmd.error(
                "Configure this printer's steppers before calibrating. The "
                "motion queue is registered by the steppers, and this printer "
                "has none, so a scan has nothing to read the position of each "
                "sample from.")
        motion_report = self.printer.lookup_object('motion_report')
        if self.motion_queue_strategy == 'trapqs':
            return motion_report.trapqs['toolhead']
        if self.motion_queue_strategy == 'dtrapqs':
            return motion_report.dtrapqs['toolhead']
        raise unhandled_member(
            'motion queue strategy', self.motion_queue_strategy,
            MOTION_QUEUE_STRATEGIES)

    def _requested_tools(self, gcmd, command):
        """The tools a command runs over, in the order it measures them."""
        text = gcmd.get('T', None)
        try:
            tools = parse_tool_list(text, self.tool_count, MAX_TOOLS)
        except ValueError as e:
            if text is None:
                raise gcmd.error(
                    "Add tool_count to the [%s] config section and restart, "
                    "or name the tools with T=0,1. A run without T= needs "
                    "the tool count to know how many tools this printer has."
                    % (self.name,))
            raise gcmd.error(
                "Give T= one tool number such as T=0, or a comma separated "
                "list such as T=0,1,2. %s." % (e,))
        if len(tools) > 1 and not self.has_toolchange_gcode:
            raise gcmd.error(
                "Add toolchange_gcode to the [%s] config section and restart, "
                "or run %s T=%d to calibrate one tool at a time. Measuring "
                "more than one tool mounts each of them, which needs the "
                "lines that mount a tool." % (self.name, command, tools[0]))
        return tools

    def _internal_error(self, gcmd, reason):
        """The error a caller raises for a member of a closed set this build
        does not handle.

        Klipper's dispatcher catches only its own command errors, so an
        exception of any other kind leaving a handler shuts the printer down
        instead of printing a message.
        """
        return gcmd.error("Internal error: %s." % (reason,))

    @contextlib.contextmanager
    def _internal_errors(self, gcmd):
        try:
            yield
        except ValueError as e:
            raise self._internal_error(gcmd, e)

    def _require_phase(self, gcmd, phase):
        with self._internal_errors(gcmd):
            if phase not in TOOL_PHASES:
                raise unhandled_member('calibration stage', phase, TOOL_PHASES)

    def _study_heats(self, gcmd, heating):
        with self._internal_errors(gcmd):
            return study_heats(heating)

    def _study_docks(self, gcmd, state):
        with self._internal_errors(gcmd):
            return study_docks(state)

    @contextlib.contextmanager
    def _phase(self, gcmd, tool, phase, multiple_tools):
        """Name the tool and the stage that a failure came from."""
        self._require_phase(gcmd, phase)
        if not multiple_tools:
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
        is mounted.
        """
        if self.has_toolchange_gcode:
            context = self.toolchange_gcode.create_template_context()
            context['tool'] = tool
            self.toolchange_gcode.run_gcode_from_command(context)
            # The measurement reads positions out of the motion queue, so the
            # toolchange has to have left it before the measurement starts.
            self.printer.lookup_object('toolhead').wait_moves()
        self.last_tool = tool

    def _apply_offsets(self, gcmd, tool, offsets):
        """Run the configured apply lines with a tool's measured offsets."""
        if not self.has_apply_offsets_gcode:
            return
        try:
            values = offset_template_context(tool, offsets, self.calibrate_z)
        except ValueError as e:
            raise gcmd.error("The offsets cannot be applied: %s." % (e,))
        context = self.apply_offsets_gcode.create_template_context()
        context.update(values)
        try:
            self.apply_offsets_gcode.run_gcode_from_command(context)
        except self.printer.command_error as e:
            raise gcmd.error(
                "apply_offsets_gcode failed: %s. The lines can use "
                "tool, offset_x and offset_y%s."
                % (e,
                   ", and offset_z" if self.calibrate_z else
                   "; offset_z is available only with calibrate_z set to "
                   "True, and it is False, so a line naming offset_z has "
                   "nothing to put there"))

    def _sensor_chip_name(self):
        # The driver registers LDC_CALIBRATE_DRIVE_CURRENT under the last word
        # of the config section name it was handed (ldc1612.py:44-49), which is
        # this section, so the CHIP= argument is derived the same way here.
        return self.name.split()[-1]

    def _report_sensor_health(self, gcmd, stats):
        if stats['errors']:
            raise gcmd.error(
                "Run LDC_CALIBRATE_DRIVE_CURRENT CHIP=%s and retry. The "
                "sensor reported %d sample errors during the measurement."
                % (self._sensor_chip_name(), stats['errors']))
        if stats['overflows']:
            gcmd.respond_info(
                "sensor buffer overflows: %d" % (stats['overflows'],))

    def _respond_measurement(self, gcmd, rows, agg):
        rows.extend(step_distance_rows(self._step_distances()))
        rows.extend(aggregate_rows(agg))
        gcmd.respond_info("\n".join(rows))

    def _debug_flag(self, gcmd):
        return gcmd.get_int('DEBUG', 0, minval=0, maxval=1)

    def _sample_diagnosis(self, stats, cause):
        return "\n".join(
            [cause(stats, self._sensor_chip_name())] + sample_drop_rows(stats))

    def _machine_z(self, height):
        return machine_z(self.coil_z, height)

    def _approach_z(self, height):
        return approach_z(self.coil_z, height)

    def _retreat_z(self):
        return retreat_z(self.coil_z, self.z_start)

    def _move(self, x, y, z, z_speed):
        """Travel to (x, y, z), never crossing the coil below the target Z.

        XY travels at travel_speed and the Z leg at z_speed.
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

        The retreat height sits above the coil, and a tool that failed while
        probing the switch or while docking stands higher than that, so this
        only ever raises: commanding the height outright would drive such a
        tool down through whatever it was standing over.
        """
        toolhead = self.printer.lookup_object('toolhead')
        retreat_z = self._retreat_z()
        if retreat_z <= toolhead.get_position()[2]:
            return
        toolhead.manual_move([None, None, retreat_z], self.z_speed)
        toolhead.wait_moves()

    @contextlib.contextmanager
    def _retreating(self):
        """Run a block of motion and lift clear afterwards, however it ends.

        A lift that fails while another error is already on its way up is
        logged rather than raised, so the console shows the original cause.
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

    # -- nozzle temperature -----------------------------------------------

    def _startup_error(self, message):
        return self.printer.config_error("%s: %s" % (self.name, message))

    def _resolve_at_connect(self, row_label, resolver, *args, detail=""):
        try:
            result = resolver(*args)
        except ValueError as e:
            raise self._startup_error(str(e))
        values = result if isinstance(result, tuple) else (result,)
        logging.info(
            "%s: " + row_label + " resolved by %s" + detail,
            self.name, *values)
        return result

    def _handle_connect(self):
        """Resolve the motion queue, the preheat wait and the sensor clock,
        then resolve every tool's heater once the heaters exist.

        Extruder sections are still being created while this section is
        parsed, so neither the heater names nor the heaters object can be
        reached at config load; connect is the first moment they all exist.
        Nothing heats with calibrate_z False, and without tool_count the tools
        are not known here, so each one is resolved when a command names it
        instead.
        """
        logging.info(
            "%s: sensor driver import resolved by %s",
            self.name, self.sensor_import_strategy)
        motion_report = self.printer.lookup_object('motion_report', None)
        if motion_report is not None:
            self.motion_queue_strategy = self._resolve_at_connect(
                "motion queue", resolve_motion_queue, motion_report)
        if not self.calibrate_z:
            return
        # A printer with no heater at all carries no heaters object to
        # resolve against, and it never reaches the wait either: the heater
        # lookup every preheat starts with refuses such a machine first, and
        # it names the missing hotend rather than a missing firmware method.
        pheaters = self.printer.lookup_object('heaters', None)
        if pheaters is not None:
            self.preheat_wait_strategy = self._resolve_at_connect(
                "preheat wait", resolve_preheat_wait, self.printer, pheaters)
        _, self.sensor_clock = self._resolve_at_connect(
            "sensor clock", resolve_sensor_clock,
            self.sensor, self.sensor_module, detail=": %s Hz")
        if self.tool_count is None:
            return
        for tool in range(self.tool_count):
            self._tool_heater(tool, self._startup_error)

    def _tool_heater(self, tool, error):
        """The heater holding one tool's nozzle, as (section name, heater).

        A fleet preheat sets a setpoint on tools that are not mounted, so the
        heater is looked up by section name rather than through whichever
        extruder happens to be active. error builds the exception the caller
        needs: a gcode error inside a command, a startup error at connect.
        """
        try:
            name = tool_extruder_name(self.tool_extruders, tool)
        except ValueError as e:
            raise error(
                "%s. List one heater section name per tool in tool_extruders, "
                "in tool number order." % (e,))
        pheaters = self.printer.lookup_object('heaters', None)
        if pheaters is None:
            raise error(
                "Configure a hotend before calibrating. This printer carries "
                "no heater at all, so the nozzle cannot be brought to the "
                "calibration setpoint.")
        try:
            return name, pheaters.lookup_heater(name)
        except self.printer.config_error as e:
            raise error(
                "T%d would be heated by the heater %s, which is not "
                "configured: %s. Set tool_extruders to the heater section "
                "names of your tools, in tool number order. Without that "
                "option each tool is assumed to follow Klipper's extruder "
                "naming: extruder for T0, extruder1 for T1, and so on."
                % (tool, name, e))

    def _observed_temperature(self, gcmd, tool):
        """What one tool's heater reads right now, in degrees Celsius.

        This is the reading, not the setpoint: it is recorded as evidence
        rather than reproduced by a later run.
        """
        _name, heater = self._tool_heater(tool, gcmd.error)
        current, _target = heater.get_temp(
            self.printer.get_reactor().monotonic())
        return current

    def _preheat(self, gcmd, targets, source):
        """Bring every listed tool to its setpoint and let the nozzles settle.

        targets is a list of (tool, setpoint). Every setpoint is set before any
        waiting starts, so the tools heat side by side rather than one after
        another, and the settle dwell then runs once for all of them.
        """
        if not targets:
            raise gcmd.error(
                "Internal error: a preheat was asked to heat no tools.")
        resolved = []
        for tool, setpoint in targets:
            name, heater = self._tool_heater(tool, gcmd.error)
            resolved.append((tool, name, heater, float(setpoint)))
        pheaters = self.printer.lookup_object('heaters')
        eventtime = self.printer.get_reactor().monotonic()
        entries = []
        for tool, name, heater, setpoint in resolved:
            current, _target = heater.get_temp(eventtime)
            entries.append((tool, name, current, setpoint))
        gcmd.respond_info("\n".join(preheat_plan_rows(
            entries, self.calibration_temp_band,
            self.calibration_settle_time)))
        for tool, name, heater, setpoint in resolved:
            try:
                pheaters.set_temperature(heater, setpoint, False)
            except self.printer.command_error as e:
                raise gcmd.error(
                    "T%d could not be held at %.1f C by the heater %s: %s. "
                    "That target temperature comes from %s."
                    % (tool, setpoint, name, e, source))
        for tool, name, heater, setpoint in resolved:
            self._wait_for_band(gcmd, tool, name, heater, setpoint)
        toolhead = self.printer.lookup_object('toolhead')
        toolhead.dwell(self.calibration_settle_time)
        toolhead.wait_moves()

    def _wait_for_band(self, gcmd, tool, name, heater, setpoint):
        """Wait until one nozzle reads inside the band around its setpoint.

        The polling runs through the preheat wait strategy resolved at
        connect, so the reactor keeps running and a shutdown or a gcode
        interrupt ends the wait as it would end any other. A heater that
        never approaches its setpoint is not this plugin's to diagnose: every
        heater is supervised by the firmware's verify_heater, which shuts the
        printer down when one stops approaching, and that shutdown ends this
        wait. Each poll emits the M105 line heaters.py's cmd_TEMPERATURE_WAIT
        emits, which front ends read to follow a wait that blocks for
        minutes.
        """
        # A batch run's heaters never move, so any wait on a reading would
        # never return. Kalico's own heater wait skips itself there for the
        # same reason (heaters.py:1464).
        if is_batch_run(self.printer.get_start_args()):
            logging.info(
                "eddy_tool_calibration: not waiting for T%d to reach %.1f C, "
                "because this is a batch run with no hardware behind the "
                "heater %s", tool, setpoint, name)
            return
        pheaters = self.printer.lookup_object('heaters')

        def waiting(eventtime):
            current, _target = heater.get_temp(eventtime)
            if temperature_in_band(
                    current, setpoint, self.calibration_temp_band):
                return False
            gcmd.respond_raw(pheaters._get_temp(eventtime))
            return True

        if self.preheat_wait_strategy == 'printer_wait_while':
            self.printer.wait_while(waiting)
            return
        if self.preheat_wait_strategy == 'reactor_poll':
            preheat_poll_wait(
                self.printer.get_reactor(), self.printer.is_shutdown,
                waiting, gcmd.error,
                "T%d to reach %.1f C on the heater %s"
                % (tool, setpoint, name))
            return
        raise unhandled_member(
            'preheat wait strategy', self.preheat_wait_strategy,
            PREHEAT_WAIT_STRATEGIES)

    # -- machine resolution -----------------------------------------------

    def _step_distances(self):
        """How far one microstep moves each X and Y stepper, in mm.

        Returns a list of (stepper section name, millimetres per step).
        Kinematics that drive no stepper_x or stepper_y at all, delta, polar
        and winch among them, report none.
        """
        toolhead = self.printer.lookup_object('toolhead')
        distances = []
        for stepper in toolhead.get_kinematics().get_steppers():
            name = stepper.get_name()
            if not name.startswith('stepper_x') and \
                    not name.startswith('stepper_y'):
                continue
            distances.append((name, stepper.get_step_dist()))
        if not distances:
            logging.info(
                "eddy_tool_calibration: the kinematics report no stepper_x or "
                "stepper_y stepper, so the stepper microstep distance rows "
                "are left out")
        return distances

    # -- contact switch probing -------------------------------------------

    def _handle_mcu_identify(self):
        # The steppers a probing move is allowed to stop exist only once the
        # kinematics are built, which is what this event announces.
        kin = self.printer.lookup_object('toolhead').get_kinematics()
        for stepper in kin.get_steppers():
            if stepper.is_active_axis('z'):
                self.switch_endstop.add_stepper(stepper)

    def _kin_z_limits(self):
        """The kinematic Z travel limits, as (minimum, maximum)."""
        toolhead = self.printer.lookup_object('toolhead')
        curtime = self.printer.get_reactor().monotonic()
        kin_status = toolhead.get_kinematics().get_status(curtime)
        return kin_status['axis_minimum'][2], kin_status['axis_maximum'][2]

    def _switch_travel_z(self):
        return self.switch_probe_z_start + self.scan_safe_z

    def _require_switch_z_range(self, gcmd):
        """Refuse to travel to the switch when the heights are out of range."""
        minimum_z, maximum_z = self._kin_z_limits()
        travel_z = self._switch_travel_z()
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

    def _probe_switch_once(self, gcmd, press):
        """One downward probing move onto the switch, returning its trigger Z.

        Ported from tools_calibrate's PrinterProbeMultiAxis: the move is the
        homing module's probing_move, which returns the position computed from
        step counts at trigger time. press is the 1-based position of this
        press in the sequence.
        """
        phoming = self.printer.lookup_object('homing')
        toolhead = self.printer.lookup_object('toolhead')
        minimum_z, _maximum_z = self._kin_z_limits()
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
                if press == 1:
                    raise gcmd.error(
                        "The contact switch already read triggered before the "
                        "first press moved the nozzle. Check that the switch "
                        "is not stuck closed, that switch_pin has the right "
                        "inverting prefix, and that switch_probe_z_start sits "
                        "above the trigger point.")
                raise gcmd.error(
                    "The contact switch still read triggered at the start of "
                    "press %d. Check that the switch is not stuck closed, "
                    "that switch_pin has the right inverting prefix, and that "
                    "switch_probe_sample_retract_dist lifts the nozzle clear "
                    "of the trigger point between presses." % (press,))
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
        result.

        Every press retracts, the last one included, so the nozzle is never
        left standing on the switch.
        """
        toolhead = self.printer.lookup_object('toolhead')
        _minimum_z, maximum_z = self._kin_z_limits()
        heights = []
        for press in range(SWITCH_PRESS_COUNT):
            trigger_z = self._probe_switch_once(gcmd, press + 1)
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

    @contextlib.contextmanager
    def _collecting(self):
        """Stream sensor samples for as long as the block runs.

        Yields (samples, stats): the (print_time, frequency) samples that read
        at or above freq_min, and the counters of the collection. A callback
        that returns False unregisters its client (bulk_sensor.py:97-104), so
        the stream ends with the block.
        """
        # In a batch run the driver's start callback issues an i2c query the
        # simulated MCU never answers (ldc1612.py:214 read_reg), and the send
        # blocks on a response stream that batch mode never starts
        # (serialhdl.py:394 raw_send_wait_ack; connect_file at
        # serialhdl.py:316 starts no reader thread), so add_client would
        # block this command until the run is killed.
        if is_batch_run(self.printer.get_start_args()):
            raise self.printer.command_error(
                "Run this command on the printer. This is a batch run with "
                "no hardware behind the eddy sensor, so the sensor can "
                "deliver no samples.")
        samples = []
        stats = new_collection()
        state = {'running': True}

        def handle_batch(msg):
            if not state['running']:
                return False
            if not msg:
                return True
            note_batch(stats, msg)
            for print_time, freq, dummy_z in msg['data']:
                stats['raw_count'] += 1
                if freq < self.freq_min:
                    stats['dropped_low_freq'] += 1
                    continue
                samples.append((print_time, freq))
            return True

        self.sensor.add_client(handle_batch)
        try:
            yield samples, stats
        finally:
            state['running'] = False

    def _collect_stationary(self, duration):
        """Collect samples without moving. Returns (freqs, stats)."""
        reactor = self.printer.get_reactor()
        toolhead = self.printer.lookup_object('toolhead')
        with self._collecting() as (samples, stats):
            toolhead.wait_moves()
            reactor.pause(reactor.monotonic() + duration)
        return [freq for _print_time, freq in samples], stats

    def _resolve_positions_after_moves(self, dump, timed, stats):
        """Wait for the motion queue to drain, then map each sample's
        print_time to the position the toolhead held at that time.

        The wait comes first because motion_report reads the trapq's history
        list alone, and a move reaches that list only when the toolhead's flush
        loop finalizes it. Queried while the move still executes, the lookup
        finds the newest already-finalized move and returns its end position,
        the scan start point: a real tuple, not None, so the pass would "find"
        its extremum at the scan start. Once the queue has drained the whole
        move is in history, retained for MOVE_HISTORY_EXPIRE (30 s in
        toolhead.py, far longer than a pass), so a None result again means the
        timestamp lies outside known motion.
        """
        self.printer.lookup_object('toolhead').wait_moves()
        samples = []
        for print_time, freq in timed:
            pos, velocity = dump.get_trapq_position(print_time)
            if pos is None:
                stats['dropped_no_position'] += 1
                continue
            samples.append((print_time, freq, pos[0], pos[1]))
        return samples

    def _collect_scan(self, gcmd, start_x, start_y, end_x, end_y, scan_z):
        """Run one directional scan pass, returning (samples, stats) with
        samples a list of (print_time, frequency, x, y).
        """
        reactor = self.printer.get_reactor()
        toolhead = self.printer.lookup_object('toolhead')
        dump = self._get_trapq(gcmd)
        safe_z = scan_z + self.scan_safe_z
        self._move(start_x, start_y, safe_z, self.z_speed)
        self._move(start_x, start_y, scan_z, self.z_speed)
        with self._collecting() as (collected, stats):
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
        toolhead.manual_move([None, None, safe_z], self.z_speed)
        in_window, outside = samples_in_window(move_start, move_end, collected)
        stats['dropped_outside_move'] += outside
        samples = self._resolve_positions_after_moves(dump, in_window, stats)
        return samples, stats

    def _scan_pass(self, gcmd, center_x, center_y, angle_deg, length, scan_z,
                   label, debug):
        start_x, start_y, end_x, end_y = scan_endpoints(
            center_x, center_y, angle_deg, length)
        samples, stats = self._collect_scan(
            gcmd, start_x, start_y, end_x, end_y, scan_z)
        self._report_sensor_health(gcmd, stats)
        if not samples:
            raise gcmd.error(
                "The %s pass produced no usable samples. %s"
                % (label, self._sample_diagnosis(stats, no_sample_cause)))
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
                "The %s pass did not yield a usable fit: %s. Run "
                "EDDY_LOCATE to refine the coil center, and check that no "
                "metal sits near the coil." % (label, e))
        return result, stats

    def _save_csv(self, gcmd, label, samples, debug):
        directory = self._data_dir()
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

    # -- measurement logs -------------------------------------------------

    def _log_entry(self, command, result, offsets):
        """The fields one completed measurement contributes to a log row.

        offsets is None when the measurement had no baseline to be compared
        against, which leaves the offset columns empty rather than zero.
        """
        return dict(
            offset_fields(offsets),
            timestamp=log_timestamp(),
            command=command,
            center_x=result['x'],
            center_y=result['y'],
            z_crossing=result['z_crossing'],
            trigger_z=result['z_trigger'],
            baseline_session=None if offsets is None else self.session_id,
            setpoint_temperature=result['setpoint_temperature'],
            observed_temperature=result['observed_temperature'],
            samples_used=result['agg']['samples_used'],
        )

    def _append_history(self, gcmd, tool, entry, completed):
        """Append one completed measurement to the tool's drift log.

        Called last, after the work of the command is finished and reported.
        A log failure warns and never raises: raising here would abort a
        print whose measurement already succeeded. completed is the sentence
        naming what is already in place when that happens.
        """
        if not self.save_history:
            return
        path = os.path.join(self._log_dir(), history_filename(tool))
        try:
            rotated_to = append_csv(path, HISTORY_COLUMNS, entry)
        except ValueError as e:
            gcmd.respond_raw(
                "!! %s Only the drift log write failed: %s"
                % (completed, e))
            return
        except OSError as e:
            gcmd.respond_raw(
                "!! %s Only the drift log write failed: %s could not be "
                "written: %s. Fix the directory permissions, or set "
                "save_history to False in the [%s] config section. This "
                "warning repeats every measurement until one of those "
                "happens."
                % (completed, path, e, self.name))
            return
        if rotated_to is not None:
            gcmd.respond_info(
                "drift log rotated: %s -> %s" % (path, rotated_to))

    # -- measurement ------------------------------------------------------

    def _measure_center(self, gcmd, center_x, center_y, length, label, debug):
        """One full multi-direction XY measurement around a center estimate.

        With DEBUG=0 the per-pass diagnostic rows are held back and flushed
        only if this measurement fails, so a failed pass still reports its
        diagnostics in full.
        """
        angles = expand_scan_angles(self.scan_angles, self.pair_scans)
        scan_z = self._machine_z(self.scan_height)
        peaks = []
        agg = new_aggregate()
        pending_rows = []

        def flush_pending_rows():
            if debug:
                return
            for block in pending_rows:
                gcmd.respond_info(block)

        for angle in angles:
            try:
                result, stats = self._scan_pass(
                    gcmd, center_x, center_y, angle, length, scan_z,
                    "%s %.0f deg" % (label, angle), debug)
            except Exception:
                flush_pending_rows()
                raise
            rows = [
                "pass angle: %.1f deg" % (angle,),
                "frequency peak type: %s" % (result['peak_type'],),
                "samples: %d" % (result['sample_count'],),
                "peak sample: %d" % (result['extremum_index'],),
                "peak offset: %+.3f samples" % (result['vertex_offset'],),
                "peak x: %.4f" % (result['peak_x'],),
                "peak y: %.4f" % (result['peak_y'],),
            ]
            rows.extend(sample_drop_rows(stats))
            if debug:
                gcmd.respond_info("\n".join(rows))
            else:
                pending_rows.append("\n".join(rows))
            add_to_aggregate(agg, stats, result['sample_count'])
            peaks.append((angle, result['peak_x'], result['peak_y']))
        if self.pair_scans:
            projections = average_paired_projections(peaks)
        else:
            projections = project_peaks(peaks)
        try:
            center_x, center_y = solve_center_lsq(projections)
        except ValueError as e:
            flush_pending_rows()
            raise gcmd.error(
                "The scan directions did not reconstruct a center: %s. Set "
                "scan_angles to two directions at least 30 degrees apart."
                % (e,))
        return center_x, center_y, agg

    def _scan_rounds(self, labels, first_length):
        """Pair every round of a measurement with the length its passes cover.

        Only the opening round covers first_length: it is the one that has to
        bracket the uncertainty in the center estimate it starts from.
        """
        return [(label, first_length if index == 0 else self.scan_length)
                for index, label in enumerate(labels)]

    def _measure_rounds(self, gcmd, center_x, center_y, rounds, debug):
        agg = new_aggregate()
        for label, length in rounds:
            center_x, center_y, round_agg = self._measure_center(
                gcmd, center_x, center_y, length, label, debug)
            merge_aggregate(agg, round_agg)
            if debug:
                gcmd.respond_info("\n".join(center_rows(
                    center_x, center_y, "%s center" % (label,))))
        return center_x, center_y, agg

    def _measure_xy(self, gcmd, debug):
        center_x, center_y = self.center if self.center else (
            self.coil_x, self.coil_y)
        return self._measure_rounds(
            gcmd, center_x, center_y,
            self._scan_rounds(XY_MEASUREMENT_ROUNDS, self.scan_length), debug)

    def _measure_z_curve(self, gcmd, center_x, center_y):
        """Stepwise descent over the coil center, returning the Z curve.

        Ported from probe_eddy_current's calibration moves: every step is
        approached from above, samples are bucketed by an explicit per-step
        time window, and the height stored is the real kinematic position. The
        curve holds the machine Z the kinematics reported, not the commanded
        height.
        """
        reactor = self.printer.get_reactor()
        toolhead = self.printer.lookup_object('toolhead')
        kin = toolhead.get_kinematics()
        self._move(center_x, center_y, self._retreat_z(), self.z_speed)
        step_windows = []
        with self._collecting() as (readings, stats):
            toolhead.dwell(DESCENT_SETTLE_DWELL)
            for target in self.z_targets:
                toolhead.manual_move(
                    [None, None, self._approach_z(target)], self.z_speed)
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
                step_windows.append(
                    (start_query_time, end_query_time, kin_pos[2]))
            toolhead.dwell(DESCENT_SETTLE_DWELL)
            toolhead.wait_moves()
            reactor.pause(reactor.monotonic() + COLLECT_TAIL_TIME)
        self._move(center_x, center_y, self._retreat_z(), self.z_speed)
        self._report_sensor_health(gcmd, stats)
        buckets, outside = bucket_samples_by_window(step_windows, readings)
        stats['dropped_outside_move'] += outside
        if len(buckets) != len(step_windows):
            raise gcmd.error(
                "The descent produced sensor data for %d of %d steps. %s"
                % (len(buckets), len(step_windows),
                   self._sample_diagnosis(stats, descent_gap_cause)))
        points = [(z, sum(freqs) / len(freqs))
                  for z, freqs in buckets.items()]
        try:
            curve = build_z_curve(points)
        except ValueError as e:
            raise gcmd.error(
                "The Z descent did not produce a usable curve: %s. Lower "
                "z_start so the descent stays inside the sensor's range."
                % (e,))
        agg = new_aggregate()
        add_to_aggregate(
            agg, stats, sum(len(freqs) for freqs in buckets.values()))
        return curve, agg

    # -- commands ---------------------------------------------------------

    cmd_EDDY_QUERY_help = (
        "Print the current eddy sensor frequency reading, for a wiring "
        "sanity check.")

    def cmd_EDDY_QUERY(self, gcmd):
        freqs, stats = self._collect_stationary(self.query_time)
        if not freqs:
            raise gcmd.error(
                "The sensor returned no usable samples. %s"
                % (self._sample_diagnosis(stats, no_sample_cause),))
        self._report_sensor_health(gcmd, stats)
        low, high, std = spread(freqs)
        rows = [
            "samples: %d" % (len(freqs),),
            "frequency mean: %.3f Hz" % (sum(freqs) / len(freqs),),
            "frequency min: %.3f Hz" % (low,),
            "frequency max: %.3f Hz" % (high,),
            "frequency stddev: %.3f Hz" % (std,),
        ]
        rows.extend(sample_drop_rows(stats))
        gcmd.respond_info("\n".join(rows))

    cmd_EDDY_LOCATE_help = (
        "Coarse straight line scan passes over the configured coil position "
        "to find and store the refined coil center for this session, and to "
        "stage it as coil_x and coil_y for SAVE_CONFIG. Add DEBUG=1 to print "
        "each scan pass's diagnostic rows.")

    def cmd_EDDY_LOCATE(self, gcmd):
        self._ensure_homed(gcmd)
        debug = self._debug_flag(gcmd)
        with self._retreating():
            refined_x, refined_y, agg = self._measure_rounds(
                gcmd, self.coil_x, self.coil_y,
                self._scan_rounds(LOCATE_ROUNDS, self.locate_scan_length),
                debug)
            self.center = (refined_x, refined_y)
            self._respond_measurement(
                gcmd, new_center_rows(refined_x, refined_y), agg)
        self._stage_coil_center(gcmd, refined_x, refined_y)

    def _stage_coil_center(self, gcmd, center_x, center_y):
        # configfile.set(section, option, value) holds a value for SAVE_CONFIG
        # to write and raises the pending flag the frontends show
        # (configfile.py:629-642), which is how ldc1612.py:84-85 hands over its
        # own calibrated value.
        settings = coil_center_settings(center_x, center_y)
        staged = []
        try:
            configfile = self.printer.lookup_object('configfile')
            for option, value in settings:
                configfile.set(self.name, option, value)
                staged.append(option)
        except Exception as e:
            raise gcmd.error(
                "The coil center printed above was measured, and it could "
                "not be staged for SAVE_CONFIG: %s\n"
                "staged: %s\n"
                "not staged: %s\n"
                "Put the printed values into coil_x and coil_y in the [%s] "
                "config section by hand."
                % (e, ", ".join(staged) if staged else "none",
                   ", ".join(o for o, _v in settings if o not in staged),
                   self.name))
        gcmd.respond_info(
            "The SAVE_CONFIG command will update the printer config file "
            "and restart the printer.")

    cmd_EDDY_CALIBRATE_Z_help = (
        "One-time Z reference setup for the tools named by T=, or for every "
        "tool when T= is left out. Presses the contact switch and binds the "
        "result to the eddy sensor's reading, at calibration_temp, which is "
        "recorded as the target temperature every later run of that tool is "
        "heated to. "
        "Run it after changing a nozzle or a hotend, after moving the coil or "
        "the switch, or after changing calibration_temp. This is the setup "
        "step, not the routine offset measurement; that is "
        "EDDY_CALIBRATE_OFFSET. "
        "Add DEBUG=1 to print each scan pass's diagnostic rows.")

    def cmd_EDDY_CALIBRATE_Z(self, gcmd):
        self._require_switch_config(gcmd)
        self._ensure_homed(gcmd)
        debug = self._debug_flag(gcmd)
        tools = self._requested_tools(gcmd, 'EDDY_CALIBRATE_Z')
        multiple_tools = len(tools) > 1
        self._require_switch_z_range(gcmd)
        self._preheat(
            gcmd, [(tool, self.calibration_temp) for tool in tools],
            'calibration_temp in the [%s] config section' % (self.name,))
        for tool in tools:
            self._anchor_tool(gcmd, tool, debug, multiple_tools)

    def _anchor_tool(self, gcmd, tool, debug, multiple_tools):
        """Press the switch for one tool and store its Z reference."""
        travel_z = self._switch_travel_z()
        with self._retreating():
            with self._phase(gcmd, tool, 'toolchange', multiple_tools):
                self._mount_tool(gcmd, tool)
            with self._phase(gcmd, tool, 'switch probing', multiple_tools):
                self._move(
                    self.switch_x, self.switch_y, travel_z, self.z_speed)
                self._move(self.switch_x, self.switch_y,
                           self.switch_probe_z_start, self.z_speed)
                trigger_z, counted, press_spread = self._probe_switch(
                    gcmd, debug)
                self._move(
                    self.switch_x, self.switch_y, travel_z, self.z_speed)
            with self._phase(gcmd, tool, 'measurement', multiple_tools):
                observed = self._observed_temperature(gcmd, tool)
                center_x, center_y, agg = self._measure_xy(gcmd, debug)
                curve, agg_z = self._measure_z_curve(gcmd, center_x, center_y)
        merge_aggregate(agg, agg_z)
        record = anchor_record(
            curve, trigger_z, self.calibration_temp, observed,
            self._resolved_sensor_clock(gcmd),
            self.sensor.dccal.get_drive_current(),
            center_x, center_y)
        previous = self.anchors.get(tool)
        self.anchors[tool] = record
        try:
            self._write_state(gcmd)
        except Exception:
            # An anchor that did not persist would be gone at the next restart.
            if previous is None:
                del self.anchors[tool]
            else:
                self.anchors[tool] = previous
            raise
        # The tool's session measurements were taken against the reference this
        # anchor just replaced, so they are dropped rather than compared across
        # two references.
        self.results.pop(tool, None)
        if self.baseline is not None and self.baseline['tool'] == tool:
            self.baseline = None
        rows = [
            tool_row(tool),
            "counted press triggers (machine Z): %s mm"
            % (", ".join("%.4f" % (h,) for h in counted),),
            "press spread: %.4f mm" % (press_spread,),
            "press tolerance: %.4f mm" % (self.switch_probe_tolerance,),
            switch_trigger_row(trigger_z),
        ]
        rows.extend(center_rows(center_x, center_y))
        rows.extend(self._z_curve_rows(curve))
        rows.extend(anchor_rows(record))
        rows.extend([
            "sensor clock: %s Hz" % (record['sensor_clock'],),
            "sensor drive current: %d" % (record['drive_current'],),
        ])
        rows.extend(anchor_temperature_rows(
            'nozzle', record, target_temperature_row))
        rows.append("state file: %s" % (self._state_path(),))
        self._respond_measurement(gcmd, rows, agg)
        # The descent of an anchor run defines the anchor rather than being
        # evaluated against one, so it has no crossing to log; what the run
        # contributes to the drift record is the trigger plane it pressed.
        self._append_history(
            gcmd, tool,
            self._log_entry(
                'EDDY_CALIBRATE_Z',
                self._measurement_result(
                    center_x, center_y, curve, None, trigger_z,
                    record['setpoint_temperature'],
                    record['observed_temperature'], agg),
                None),
            "The Z reference for T%d is measured and saved to the state file."
            % (tool,))

    cmd_EDDY_CALIBRATE_OFFSET_help = (
        "Measure the tools named by T= over the coil and print their offsets "
        "relative to T0, or measure every tool in turn when T= is left out. "
        "T=0 measures the baseline every other tool is compared against, and "
        "it is measured first whatever order T= lists it in. With "
        "calibrate_z True each tool is heated to the temperature its "
        "EDDY_CALIBRATE_Z reference was measured at. Add DEBUG=1 to print "
        "each scan pass's diagnostic rows.")

    def cmd_EDDY_CALIBRATE_OFFSET(self, gcmd):
        self._ensure_homed(gcmd)
        debug = self._debug_flag(gcmd)
        tools = baseline_first(
            self._requested_tools(gcmd, 'EDDY_CALIBRATE_OFFSET'),
            BASELINE_TOOL)
        multiple_tools = len(tools) > 1
        if BASELINE_TOOL not in tools and self.baseline is None:
            raise gcmd.error(
                "Run EDDY_CALIBRATE_OFFSET T=%d first, with the baseline tool "
                "mounted. Offsets are measured against that tool and it has "
                "not been calibrated in this session."
                % (BASELINE_TOOL,))
        if self.calibrate_z:
            # Checked before any motion, so a machine that is only half
            # anchored fails before it moves.
            needed = list(tools)
            if BASELINE_TOOL not in tools:
                needed.append(self.baseline['tool'])
            self._require_anchors(gcmd, needed)
            self._preheat_anchored(gcmd, tools)
        summary = []
        for tool in tools:
            summary.append(
                self._calibrate_one_offset(gcmd, tool, debug, multiple_tools))
        if multiple_tools:
            with self._internal_errors(gcmd):
                gcmd.respond_info("\n".join(fleet_summary_rows(summary)))

    def _calibrate_one_offset(self, gcmd, tool, debug, multiple_tools):
        """Measure one tool's offsets, report them, and apply them.

        Returns the tool's entry for the fleet summary: its offsets, or None
        for the baseline tool, whose offsets are zero by definition.
        """
        is_baseline_run = tool == BASELINE_TOOL
        with self._retreating():
            with self._phase(gcmd, tool, 'toolchange', multiple_tools):
                self._mount_tool(gcmd, tool)
            with self._phase(gcmd, tool, 'measurement', multiple_tools):
                result = self._run_tool_measurement(
                    gcmd, tool, debug, self.calibrate_z,
                    self._anchored_setpoint(gcmd, tool))
            self._publish_measurement(tool, result, is_baseline_run)
            offsets = self._offsets(tool, result, self.calibrate_z)
            self._report_tool_result(gcmd, tool, result, offsets)
            if offsets is not None:
                # The baseline tool's offsets are zero by definition, and
                # applying zeros would overwrite whatever the owner set for it.
                with self._phase(gcmd, tool, 'apply', multiple_tools):
                    self._apply_offsets(gcmd, tool, offsets)
            # An apply that fails leaves no log row, which is the deliberate
            # cost of this order: a row would claim an offset the machine never
            # received.
            self._append_history(
                gcmd, tool,
                self._log_entry('EDDY_CALIBRATE_OFFSET', result, offsets),
                "The measurement of T%d is complete and reported above."
                % (tool,))
        return {'tool': tool, 'offsets': offsets}

    def _publish_measurement(self, tool, result, is_baseline_run):
        """Store the session id, the baseline and the result as one unit.

        A baseline run replaces the session baseline, so a comparison never
        mixes results from two different setups. Nothing between the three
        stores yields to the reactor, so a status read never pairs a fresh
        measurement with the baseline it replaced.
        """
        if is_baseline_run:
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
        self.results[tool] = result

    def _preheat_anchored(self, gcmd, tools):
        """Heat every listed tool to the setpoint its anchor was taken at.

        The anchor frequency was measured in that thermal state, and only a
        measurement taken in the same state can be compared against it.
        """
        targets = []
        for tool in tools:
            setpoint = self._anchor_setpoint(gcmd, tool)
            warning = temperature_warning(
                tool, setpoint, self.calibration_temp)
            if warning is not None:
                gcmd.respond_info(warning)
            targets.append((tool, setpoint))
        self._preheat(
            gcmd, targets,
            'the setpoint EDDY_CALIBRATE_Z recorded for that tool')

    def _anchored_setpoint(self, gcmd, tool):
        """The setpoint an offset run holds one tool at, None with
        calibrate_z False, where nothing is heated at all.
        """
        if not self.calibrate_z:
            return None
        return self._anchor_setpoint(gcmd, tool)

    def _resolved_sensor_clock(self, gcmd):
        """The clock resolved at connect. Every caller runs behind the same
        calibrate_z gate as the resolution, so an unresolved clock here means
        the gate was bypassed, and it is refused rather than stored.
        """
        if self.sensor_clock is None:
            raise gcmd.error(
                "Set calibrate_z to True in the [%s] config section and "
                "restart. Z calibration is off, so the sensor clock was not "
                "resolved at startup and no Z reference can be measured or "
                "read." % (self.name,))
        return self.sensor_clock

    def _anchor(self, gcmd, tool):
        """One tool's stored anchor, refused when it is missing and when the
        sensor settings moved.
        """
        if tool not in self.anchors:
            raise gcmd.error(missing_anchor_message([tool]))
        anchor = self.anchors[tool]
        mismatch = anchor_sensor_mismatch(
            tool, anchor, self._resolved_sensor_clock(gcmd),
            self.sensor.dccal.get_drive_current())
        if mismatch is not None:
            raise gcmd.error(mismatch)
        return anchor

    def _anchor_setpoint(self, gcmd, tool):
        return self._anchor(gcmd, tool)['setpoint_temperature']

    def _require_anchors(self, gcmd, tools):
        """Refuse to measure Z against an anchor that is missing or stale."""
        missing = sorted(set(t for t in tools if t not in self.anchors))
        if missing:
            raise gcmd.error(missing_anchor_message(missing))
        for tool in sorted(set(tools)):
            self._anchor(gcmd, tool)

    def _measurement_observed_temperature(self, gcmd, tool, include_z):
        """The nozzle reading one measurement is recorded against.

        A descent has to have it, so a heater that cannot be named there is
        the run's error. An XY only run needs no heater at all, so one that
        cannot be named leaves the field unmeasured rather than costing a
        measurement that never depended on it.
        """
        if include_z:
            return self._observed_temperature(gcmd, tool)
        try:
            return self._observed_temperature(gcmd, tool)
        except self.printer.command_error:
            logging.exception(
                "eddy_tool_calibration: could not read the nozzle temperature "
                "of T%d, so its measurement log row leaves the field empty"
                % (tool,))
            return None

    def _run_tool_measurement(self, gcmd, tool, debug, include_z, setpoint):
        """Measure one tool, recorded against the setpoint it was held at.

        setpoint is what the caller heated this tool to before the measurement
        started, and None when it heated nothing.
        """
        center_x, center_y, agg = self._measure_xy(gcmd, debug)
        curve = None
        z_crossing = None
        z_trigger = None
        # Read before any descent, so a curve measured in the wrong thermal
        # state is visible in the readout rather than only in the offset it
        # moved.
        observed = self._measurement_observed_temperature(
            gcmd, tool, include_z)
        if include_z:
            mismatch = anchor_center_mismatch(
                tool, self._anchor(gcmd, tool), center_x, center_y)
            if mismatch is not None:
                raise gcmd.error(mismatch)
            curve, agg_z = self._measure_z_curve(gcmd, center_x, center_y)
            merge_aggregate(agg, agg_z)
            z_trigger, z_crossing = self._trigger_plane(gcmd, tool, curve)
        return self._measurement_result(
            center_x, center_y, curve, z_crossing, z_trigger, setpoint,
            observed, agg)

    def _measurement_result(self, center_x, center_y, curve, z_crossing,
                            z_trigger, setpoint, observed, agg):
        """The record one measurement of a tool leaves behind."""
        return {
            'x': center_x,
            'y': center_y,
            'z_curve': curve,
            'z_crossing': z_crossing,
            'z_trigger': z_trigger,
            'setpoint_temperature': setpoint,
            'observed_temperature': observed,
            'agg': agg,
            # Stamped by the caller, which publishes the result only once the
            # session baseline it belongs to is in place.
            'session_id': None,
            'measured_time': None,
        }

    def _trigger_plane(self, gcmd, tool, curve):
        """Machine Z of the switch trigger plane this descent reconstructs.

        Returns (trigger_z, crossing_z).
        """
        anchor = self._anchor(gcmd, tool)
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
        return [
            "z curve steps: %d" % (len(curve),),
            "z curve range (machine Z): %.4f to %.4f mm"
            % (curve[0][0], curve[-1][0]),
            "z curve frequency range: %.3f to %.3f Hz"
            % (curve[-1][1], curve[0][1]),
        ]

    def _z_rows(self, gcmd, tool, result):
        """Every Z row for one tool: curve, anchor, crossing, trigger plane."""
        if not self.calibrate_z:
            return []
        anchor = self._anchor(gcmd, tool)
        rows = self._z_curve_rows(result['z_curve'])
        rows.extend(anchor_rows(anchor))
        rows.extend(anchor_temperature_rows(
            'anchor', anchor, setpoint_temperature_row))
        rows.extend([
            observed_temperature_row(
                'nozzle', result['observed_temperature']),
            "z crossing (machine Z): %.4f mm" % (result['z_crossing'],),
            switch_trigger_row(result['z_trigger']),
        ])
        return rows

    def _offsets(self, tool, result, include_z):
        return measured_offsets(
            tool, BASELINE_TOOL, result, self.baseline, include_z)

    def _report_tool_result(self, gcmd, tool, result, offsets):
        rows = [tool_row(tool)]
        rows.extend(center_rows(result['x'], result['y']))
        rows.extend(self._z_rows(gcmd, tool, result))
        if offsets is None:
            if self.baseline is None:
                rows.append(
                    "offsets: not reported, T%d has not been measured in this "
                    "session" % (BASELINE_TOOL,))
            else:
                rows.append("offsets: baseline tool, zero by definition")
        else:
            rows.append("baseline tool: T%d" % (self.baseline['tool'],))
            rows.extend(offset_rows(offsets))
        self._respond_measurement(gcmd, rows, result['agg'])

    cmd_EDDY_REPEATABILITY_help = (
        "Measure one tool over and over and report how far the results "
        "spread: EDDY_REPEATABILITY T=<tool> RUNS=<n> CYCLES=<n> [SKIP_Z=1] "
        "[DEBUG=1]. T, RUNS and CYCLES are all required. Each cycle mounts "
        "another tool and remounts the measured one before its runs, so the "
        "runs inside a cycle show the measurement alone and the cycles show "
        "what the docking adds. A machine with no second tool or no "
        "toolchange_gcode still runs any number of cycles, which then measure "
        "drift over time instead of a docking. SKIP_Z defaults to 1 and skips "
        "the Z descent even with calibrate_z True; SKIP_Z=0 runs it. Every "
        "measurement is written to a CSV file in log_dir.")

    def cmd_EDDY_REPEATABILITY(self, gcmd):
        self._ensure_homed(gcmd)
        debug = self._debug_flag(gcmd)
        tool = self._study_tool(gcmd)
        runs = self._study_count(gcmd, 'RUNS', 2)
        cycles = self._study_count(gcmd, 'CYCLES', 1)
        include_z = self._study_include_z(gcmd)
        state, docking_tool = study_docking(
            tool, self.tool_count, self.has_toolchange_gcode)
        heating = study_heating(self.calibrate_z, tool in self.anchors)
        if include_z:
            # Checked before any motion: without an anchor the descent has
            # nothing to be evaluated against.
            self._require_anchors(gcmd, [tool])
        setpoint = self._heating_setpoint(gcmd, heating, tool)
        axes = study_axes(include_z)
        with self._internal_errors(gcmd):
            fields = [(axis, study_axis_field(axis)) for axis in axes]
        # Resolved before anything is heated, so a directory that cannot be
        # written fails in a second rather than after minutes of heating.
        log = {'path': self._study_csv_path(gcmd, tool), 'rows': 0}
        self._study_preheat(gcmd, heating, tool)
        by_cycle = dict((axis, []) for axis in axes)
        with self._retreating():
            # The first cycle measures whatever is mounted, so the tool is
            # mounted before it starts.
            self._mount_tool(gcmd, tool)
        for cycle in range(1, cycles + 1):
            # Each cycle retreats on its own, so it ends at the height every
            # toolchange starts from.
            with self._retreating():
                measured = self._run_study_cycle(
                    gcmd, tool, cycle, cycles, runs, include_z, setpoint,
                    debug, state, docking_tool, log, fields)
            for axis in axes:
                by_cycle[axis].append(measured[axis])
        self._report_study(
            gcmd, tool, runs, cycles, include_z, state, docking_tool,
            log['path'], axes, by_cycle)

    def _heating_setpoint(self, gcmd, heating, tool):
        """The setpoint a study heats the tool to, or None when it does not."""
        if not self._study_heats(gcmd, heating):
            return None
        return self._anchor_setpoint(gcmd, tool)

    def _study_preheat(self, gcmd, heating, tool):
        """Heat the measured tool to its anchor setpoint, where it has one."""
        if self._study_heats(gcmd, heating):
            self._preheat_anchored(gcmd, [tool])

    def _study_tool(self, gcmd):
        """The one tool a repeatability study measures."""
        text = gcmd.get('T', None)
        if text is None:
            raise gcmd.error(
                "Add T= to name the tool to measure, as in EDDY_REPEATABILITY "
                "T=0 RUNS=10 CYCLES=3. A repeatability study measures one "
                "tool.")
        try:
            tools = parse_tool_list(text, self.tool_count, MAX_TOOLS)
        except ValueError as e:
            raise gcmd.error(
                "%s. Give T= one tool number such as T=0." % (e,))
        if len(tools) != 1:
            raise gcmd.error(
                "T=%s names %d tools. Give T= one tool number such as T=0: a "
                "repeatability study measures one tool." % (text, len(tools)))
        return tools[0]

    def _study_count(self, gcmd, name, minimum):
        """A required repetition count of a study, or the error naming it.

        The bound is checked here rather than handed to get_int, so a count
        below it and a missing count both reach the same message.
        """
        value = gcmd.get_int(name, None)
        if value is None or value < minimum:
            raise gcmd.error(
                "Set %s= to at least %d, as in EDDY_REPEATABILITY T=0 "
                "RUNS=10 CYCLES=3. RUNS is how many measurements a cycle "
                "takes without touching the tool, at least 2, and CYCLES is "
                "how many times the tool is docked and mounted again first, "
                "at least 1." % (name, minimum))
        return value

    def _study_include_z(self, gcmd):
        skip_z = gcmd.get_int('SKIP_Z', None, minval=0, maxval=1)
        if skip_z == 0 and not self.calibrate_z:
            raise gcmd.error(
                "Set calibrate_z to True in the [%s] config section and "
                "restart, or leave SKIP_Z out. SKIP_Z=0 asks for the Z "
                "descent, and calibrate_z is False, so no descent runs at "
                "all." % (self.name,))
        if skip_z is None:
            skip_z = 1
        return self.calibrate_z and not skip_z

    def _study_csv_path(self, gcmd, tool):
        directory = self._log_dir()
        try:
            os.makedirs(directory, exist_ok=True)
            name = next_study_filename(os.listdir(directory), tool)
        except (OSError, ValueError) as e:
            raise gcmd.error(
                "Could not prepare the study data directory %s: %s. Fix the "
                "directory permissions, or point log_dir at a directory the "
                "printer host can write." % (directory, e))
        return os.path.join(directory, name)

    @contextlib.contextmanager
    def _study_step(self, gcmd, phase, cycle, run, log):
        """Name the cycle and the run a study failed in.

        run is None for the toolchange that opens a cycle, which belongs to the
        cycle rather than to any one measurement. log carries the study file
        and how many rows have reached it.
        """
        self._require_phase(gcmd, phase)
        try:
            yield
        except self.printer.command_error as e:
            where = ("cycle %d" % (cycle,) if run is None
                     else "cycle %d run %d" % (cycle, run))
            if not log['rows']:
                raise gcmd.error(
                    "The study stopped during %s of %s: %s. It had written no "
                    "measurements yet." % (phase, where, e))
            raise gcmd.error(
                "The study stopped during %s of %s: %s. The %d measurements "
                "it already took are in %s."
                % (phase, where, e, log['rows'], log['path']))

    def _exercise_docking(self, gcmd, tool, cycle, state, docking_tool, log):
        """Dock the measured tool and mount it again, when a partner exists."""
        if not self._study_docks(gcmd, state):
            return
        with self._study_step(gcmd, 'toolchange', cycle, None, log):
            self._mount_tool(gcmd, docking_tool)
            self._mount_tool(gcmd, tool)

    def _run_study_cycle(self, gcmd, tool, cycle, cycles, runs, include_z,
                         setpoint, debug, state, docking_tool, log, fields):
        """One cycle: dock and remount the tool, then measure it runs times.

        fields pairs every axis with the result field it reads. Returns the
        measured values of the cycle, keyed by axis label. The session baseline
        is left alone: a study repeats one measurement rather than replacing
        what a calibration run established.
        """
        self._exercise_docking(gcmd, tool, cycle, state, docking_tool, log)
        measured = dict((axis, []) for axis, _field in fields)
        for run in range(1, runs + 1):
            gcmd.respond_info(
                measurement_progress_row(cycle, cycles, run, runs))
            with self._study_step(gcmd, 'measurement', cycle, run, log):
                result = self._run_tool_measurement(
                    gcmd, tool, debug, include_z, setpoint)
            offsets = self._offsets(tool, result, include_z)
            entry = self._log_entry('EDDY_REPEATABILITY', result, offsets)
            try:
                append_csv(
                    log['path'], STUDY_COLUMNS,
                    dict(entry, cycle=cycle, run=run))
            except ValueError as e:
                raise gcmd.error(
                    "Cycle %d run %d is complete, and it could not be "
                    "written: %s" % (cycle, run, e))
            except OSError as e:
                raise gcmd.error(
                    "Cycle %d run %d is complete, and it could not be written "
                    "to %s: %s. Fix the directory permissions, or point "
                    "log_dir at a directory the printer host can write."
                    % (cycle, run, log['path'], e))
            log['rows'] += 1
            self._append_history(
                gcmd, tool, entry,
                "Cycle %d run %d is complete and written to %s."
                % (cycle, run, log['path']))
            for axis, field in fields:
                measured[axis].append(result[field])
        return measured

    def _report_study(self, gcmd, tool, runs, cycles, include_z, state,
                      docking_tool, path, axes, by_cycle):
        stats_by_axis = {}
        for axis in axes:
            try:
                stats_by_axis[axis] = repeatability_statistics(by_cycle[axis])
            except ValueError as e:
                raise gcmd.error(
                    "The %s results could not be summarised: %s. The "
                    "measurements themselves are in %s." % (axis, e, path))
        with self._internal_errors(gcmd):
            rows = repeatability_summary_rows(
                tool, runs, cycles, state, docking_tool, include_z, axes,
                stats_by_axis, self._step_distances(), path)
        gcmd.respond_info("\n".join(rows))

    def get_status(self, eventtime):
        return status_document(
            self.anchors, self.results, self.baseline, BASELINE_TOOL,
            self.session_id, self.last_tool, self.tool_count,
            self.calibrate_z)


def load_config(config):
    return EddyToolCalibration(config)
