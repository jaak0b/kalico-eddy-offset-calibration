# Multi-hotend XY offset calibration using an eddy current sensor
# Principle: starting from a known coil position, scan along multiple directions, detect the frequency peak position, and reconstruct the tool center coordinates via a geometric method
# This plugin depends on klipper's ldc1612.py
# version: V1.0.0 2026-06-01  Implemented basic XY offset calibration
#
# Copyright (C) 2026  bilibili user "Renquan's Son"
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging, math, os
from . import ldc1612

class ToolEddyCalibration:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.name = config.get_name()
        self.gcode = self.printer.lookup_object('gcode')

        # Create the sensor (see probe_eddy_current.py)
        sensors = { "ldc1612": ldc1612.LDC1612 }
        sensor_type = config.getchoice('sensor_type', {s: s for s in sensors})
        self.sensor = sensors[sensor_type](config, None)

        # Coil position and parameters
        self.coil_x = config.getfloat('coil_x')
        self.coil_y = config.getfloat('coil_y')
        self.coil_z = config.getfloat('coil_z')
        self.coil_inner_diameter = config.getfloat('coil_inner_diameter', 2.0,
                                                    minval=1.5, maxval=30.0)

        # Scan parameters
        self.scan_height = config.getfloat('scan_height', 0.5, minval=-20.0, maxval=50.0)
        self.scan_speed = config.getfloat('scan_speed', 5.0, minval=0.5, maxval=20.0)
        self.travel_speed_xy = config.getfloat('travel_speed_xy', 100.0, minval=5.0, maxval=300.0)
        self.travel_speed_z = config.getfloat('travel_speed_z', 10.0, minval=1.0, maxval=50.0)
        self.scan_length = config.getfloat('scan_length', 10.0, minval=2.0, maxval=20.0)
        self.scan_safe_z = config.getfloat('scan_safe_z', 2.0, minval=0.5, maxval=10.0)

        # Data saving option
        self.save_data = config.getboolean('save_data', False)

        # Detection result storage (stores the last detected center coordinates)
        self.last_result = {}

        # Register commands
        self.gcode.register_command('TOOL_EDDY_CALIBRATE',
                                   self.cmd_TOOL_EDDY_CALIBRATE,
                                   desc=self.cmd_TOOL_EDDY_CALIBRATE_help)
        self.gcode.register_command('GET_TOOL_CENTER',
                                   self.cmd_GET_TOOL_CENTER,
                                   desc=self.cmd_GET_TOOL_CENTER_help)
        self.gcode.register_command('SET_TOOL_Z',
                                   self.cmd_SET_TOOL_Z,
                                   desc=self.cmd_SET_TOOL_Z_help)
        self.gcode.register_command('CLEAR_TOOL_CENTER',
                                   self.cmd_CLEAR_TOOL_CENTER,
                                   desc=self.cmd_CLEAR_TOOL_CENTER_help)
        self.gcode.register_command('TEST_LDC1612',
                                   self.cmd_TEST_LDC1612,
                                   desc=self.cmd_TEST_LDC1612_help)

    cmd_TOOL_EDDY_CALIBRATE_help = "Calibrate tool center position using eddy current sensor"
    def cmd_TOOL_EDDY_CALIBRATE(self, gcmd):
        # Get the tool name (used only as a string identifier)
        tool_name = gcmd.get('TOOL', None)
        if tool_name is None:
            raise gcmd.error("TOOL parameter is required (e.g., TOOL=0)")

        # Get the scan Z offset (relative to scan_height, + = away from coil, - = toward coil)
        scan_height_offset = gcmd.get_float('SCAN_HEIGHT_OFFSET', 0.0)

        # Get the repeat count
        repeats = gcmd.get_int('REPEATS', 1, minval=1, maxval=10)

        # Get the pair cancellation switch
        pair_cancel = gcmd.get_int('PAIR_CANCEL', 0, minval=0, maxval=1)

        # Get the scan direction parameter (angles, comma-separated)
        dir_str = gcmd.get('DIR', '45,135')
        try:
            dir_angles = [float(a.strip()) for a in dir_str.split(',')]
        except ValueError:
            raise gcmd.error("DIR must be comma-separated angles (e.g., DIR=0,45,90,135)")
        if len(dir_angles) < 1:
            raise gcmd.error("At least 1 scan direction required (e.g., DIR=0,90)")

        # Get the pre-scan direction parameter, defaults to DIR
        pre_dir_str = gcmd.get('PRE_DIR', dir_str)
        try:
            pre_dir_angles = [float(a.strip()) for a in pre_dir_str.split(',')]
        except ValueError:
            raise gcmd.error("PRE_DIR must be comma-separated angles (e.g., PRE_DIR=0,90)")

        # Get the pre-scan center point, overrides coil_x/coil_y from config
        center_xy_str = gcmd.get('CENTER_XY', None)
        center_xy_override = None
        if center_xy_str is not None:
            try:
                parts = [float(v.strip()) for v in center_xy_str.split(',')]
                if len(parts) != 2:
                    raise ValueError
                center_xy_override = (parts[0], parts[1])
            except ValueError:
                raise gcmd.error("CENTER_XY must be X,Y format (e.g., CENTER_XY=23,45)")

        toolhead = self.printer.lookup_object('toolhead')

        def _fmt_angles(angles):
            return ','.join(str(int(a)) if a == int(a) else str(a) for a in angles)

        # Begin calibration
        gcmd.respond_info("Calibrating tool '%s' with PRE_DIR=%s DIR=%s..." %
                          (tool_name, _fmt_angles(pre_dir_angles),
                           _fmt_angles(dir_angles)))

        calibrator = EddyCalibrator(self, gcmd, scan_height_offset, repeats, bool(pair_cancel),
                                    center_xy_override)
        center_x, center_y = calibrator.run_calibration(tool_name, pre_dir_angles, dir_angles)

        # Save the detection result
        origin = self.last_result.get(tool_name, {})
        z_value = origin.get('z', 0.0)
        origin_x = origin.get('originX', 0.0)
        origin_y = origin.get('originY', 0.0)
        self.last_result[tool_name] = {
            'x': center_x,  # Measured center position of the coil
            'y': center_y,
            'z': z_value,
            'originX': origin_x,  # Center position determined by the pre-scan, used as the reference for subsequent scans
            'originY': origin_y,
        }

        # Move above the detected center point
        safe_z = self.coil_z + self.scan_height + self.scan_safe_z
        toolhead.manual_move([None, None, safe_z], self.travel_speed_z)
        toolhead.manual_move([center_x, center_y, None], self.travel_speed_xy)
        toolhead.wait_moves()

        # Report the result
        result = self.last_result[tool_name]
        gcmd.respond_info(
            "Tool '%s' center: X=%.4f Y=%.4f Z=%.4f" %
            (tool_name, result['x'], result['y'], z_value))
        gcmd.respond_info(
            "Use GET_TOOL_CENTER TOOL=%s to retrieve these values" %
            tool_name)

    cmd_GET_TOOL_CENTER_help = "Get the last detected tool center position"
    def cmd_GET_TOOL_CENTER(self, gcmd):
        tool_name = gcmd.get('TOOL', None)
        if tool_name is None:
            raise gcmd.error("TOOL parameter is required (e.g., TOOL=0)")

        if tool_name not in self.last_result:
            raise gcmd.error("Tool '%s' not calibrated yet" % tool_name)

        result = self.last_result[tool_name]
        gcmd.respond_info(
            "Tool '%s' center: X=%.4f Y=%.4f Z=%.4f" %
            (tool_name, result['x'], result['y'], result['z']))

    cmd_SET_TOOL_Z_help = "Set the Z coordinate for a tool (from external source)"
    def cmd_SET_TOOL_Z(self, gcmd):
        tool_name = gcmd.get('TOOL', None)
        if tool_name is None:
            raise gcmd.error("TOOL parameter is required (e.g., TOOL=0)")

        z = gcmd.get_float('Z', None)
        if z is None:
            raise gcmd.error("Z parameter is required")

        # Initialize data if the tool doesn't exist yet
        if tool_name not in self.last_result:
            self.last_result[tool_name] = {
                'x': 0.0,
                'y': 0.0,
                'z': z,
                'originX': 0.0,
                'originY': 0.0,
            }
            gcmd.respond_info("Tool '%s' initialized with Z=%.4f" % (tool_name, z))
        else:
            # Update the Z value
            self.last_result[tool_name]['z'] = z

        result = self.last_result[tool_name]
        gcmd.respond_info(
            "Tool '%s' Z updated: X=%.4f Y=%.4f Z=%.4f" %
            (tool_name, result['x'], result['y'], result['z']))

    cmd_CLEAR_TOOL_CENTER_help = "Clear calibration result for a tool, or all if TOOL not specified"
    def cmd_CLEAR_TOOL_CENTER(self, gcmd):
        tool_name = gcmd.get('TOOL', None)
        if tool_name is None:
            count = len(self.last_result)
            self.last_result.clear()
            gcmd.respond_info("Cleared all %d tool(s) calibration data" % count)
        elif tool_name in self.last_result:
            del self.last_result[tool_name]
            gcmd.respond_info("Tool '%s' calibration data cleared" % tool_name)
        else:
            gcmd.respond_info("Tool '%s' has no data to clear" % tool_name)

    cmd_TEST_LDC1612_help = "Test LDC1612 sensor: collect 100ms of data and report frequency statistics"
    def cmd_TEST_LDC1612(self, gcmd):
        duration = gcmd.get_float('DURATION', 0.1, minval=0.05, maxval=2.0)

        reactor = self.printer.get_reactor()
        toolhead = self.printer.lookup_object('toolhead')

        # Collect sensor data
        is_finished = False
        collected_freqs = []
        error_count = 0

        def data_callback(msg):
            if is_finished:
                return False
            if 'data' in msg:
                for samp_time, freq, z in msg['data']:
                    if freq > 1000000:
                        collected_freqs.append(freq)
            error_count = msg.get('errors', 0)
            return True

        self.sensor.add_client(data_callback)

        # Wait for sensor data to stabilize
        reactor.pause(reactor.monotonic() + 0.05)
        toolhead.dwell(0.010)
        toolhead.wait_moves()

        # Clear data from the settling period, restart collection
        collected_freqs.clear()

        # Collect data for the specified duration
        reactor.pause(reactor.monotonic() + duration)

        is_finished = True

        if not collected_freqs:
            gcmd.respond_info("LDC1612 test FAILED: no valid data received")
            gcmd.respond_info("  Check sensor wiring and configuration")
            return

        # Statistical analysis
        freq_min = min(collected_freqs)
        freq_max = max(collected_freqs)
        freq_avg = sum(collected_freqs) / len(collected_freqs)
        freq_range = freq_max - freq_min

        if len(collected_freqs) > 1:
            variance = sum((f - freq_avg)**2 for f in collected_freqs) / (len(collected_freqs) - 1)
            freq_std = math.sqrt(variance)
        else:
            freq_std = 0.0

        # Compute the fluctuation between adjacent samples (better reflects short-term stability)
        if len(collected_freqs) > 1:
            diffs = [abs(collected_freqs[i+1] - collected_freqs[i])
                     for i in range(len(collected_freqs) - 1)]
            avg_diff = sum(diffs) / len(diffs)
            max_diff = max(diffs)
        else:
            avg_diff = 0.0
            max_diff = 0.0

        sps = self.sensor.get_samples_per_second()

        gcmd.respond_info("LDC1612 sensor test results:")
        gcmd.respond_info("  Samples: %d (expect ~%d in %.0fms @ %d sps)" %
                          (len(collected_freqs), int(sps * duration),
                           duration * 1000, sps))
        gcmd.respond_info("  Frequency: avg=%.1f Hz, min=%.1f Hz, max=%.1f Hz" %
                          (freq_avg, freq_min, freq_max))
        gcmd.respond_info("  Range: %.1f Hz (%.4f%%)" %
                          (freq_range, freq_range / freq_avg * 100 if freq_avg else 0))
        gcmd.respond_info("  Std dev: %.1f Hz" % freq_std)
        gcmd.respond_info("  Adjacent diff: avg=%.1f Hz, max=%.1f Hz" %
                          (avg_diff, max_diff))
        if error_count:
            gcmd.respond_info("  Errors: %d" % error_count)

        # Assess quality based on the frequency fluctuation range
        if freq_range < 20:
            gcmd.respond_info("  Quality: EXCELLENT - frequency range < 20Hz")
        elif freq_range < 50:
            gcmd.respond_info("  Quality: GOOD - frequency range < 50Hz")
        elif freq_range < 100:
            gcmd.respond_info("  Quality: ACCEPTABLE - frequency range < 100Hz")
        elif freq_range < 200:
            gcmd.respond_info("  Quality: NOISY - frequency range < 200Hz")
        else:
            gcmd.respond_info("  Quality: UNSTABLE - frequency range >= 200Hz")

    def get_status(self, eventtime):
        """Returns status information for external queries"""
        return self.last_result


class EddyCalibrator:
    def __init__(self, calibration, gcmd, scan_height_offset=0.0, repeats=3,
                 pair_cancel=False, center_xy_override=None):
        self.cal = calibration
        self.gcmd = gcmd
        self.scan_height_offset = scan_height_offset
        self.repeats = repeats
        self.pair_cancel = pair_cancel
        self.center_xy_override = center_xy_override
        self.printer = calibration.printer
        self.sensor = calibration.sensor

        # Get the required objects
        self.toolhead = self.printer.lookup_object('toolhead')
        self.gcode_move = self.printer.lookup_object('gcode_move')

    def run_calibration(self, tool_name, pre_dir_angles, dir_angles):
        """Runs the full calibration for the specified tool
        tool_name: tool name, used to look up an existing center
        pre_dir_angles: pre-scan direction angle list, determines the precise center starting from the configured coil position
        dir_angles: precise scan direction angle list, run at the center determined by the pre-scan
        """
        # Home first if needed
        self._ensure_homed()

        # Prefer the pre-scan center from last_result's origin; otherwise use the config value and run a pre-scan
        need_pre_scan = True
        if tool_name in self.cal.last_result:
            last = self.cal.last_result[tool_name]
            origin_x = last.get('originX', 0.0)
            origin_y = last.get('originY', 0.0)
            if origin_x != 0.0 or origin_y != 0.0:
                need_pre_scan = False
                center_x = origin_x
                center_y = origin_y
                self.gcmd.respond_info(
                    "Using origin as center: X=%.4f Y=%.4f" %
                    (center_x, center_y))

        if need_pre_scan:
            if self.center_xy_override is not None:
                center_x, center_y = self.center_xy_override
                self.gcmd.respond_info(
                    "Starting from CENTER_XY override: X=%.4f Y=%.4f" %
                    (center_x, center_y))
            else:
                center_x = self.cal.coil_x
                center_y = self.cal.coil_y
                self.gcmd.respond_info(
                    "Starting from config coil center: X=%.4f Y=%.4f" %
                    (center_x, center_y))

            # Pre-scan: determine the precise center using the PRE_DIR directions (scanned only once)
            self.gcmd.respond_info(
                "Pre-scanning DIR=%s to locate precise center..." %
                ','.join(str(int(a)) if a == int(a) else str(a)
                         for a in pre_dir_angles))
            center_x, center_y = self._measure_center(
                center_x, center_y, pre_dir_angles, "Pre-scan", repeats=1)
            self.gcmd.respond_info(
                "Pre-scan center: X=%.4f Y=%.4f" %
                (center_x, center_y))

            # Record the pre-scan result as the origin, used as the reference for subsequent scans
            if tool_name in self.cal.last_result:
                self.cal.last_result[tool_name]['originX'] = center_x
                self.cal.last_result[tool_name]['originY'] = center_y
            else:
                self.cal.last_result[tool_name] = {
                    'x': 0.0, 'y': 0.0, 'z': 0.0,
                    'originX': center_x, 'originY': center_y}

        # Pair cancellation: automatically add the opposite direction
        if self.pair_cancel:
            full_dirs = set()
            for a in dir_angles:
                full_dirs.add(a % 360.0)
                full_dirs.add((a + 180.0) % 360.0)
            dir_angles = sorted(full_dirs)

        center_x, center_y = self._measure_center(
            center_x, center_y, dir_angles, "Scan", repeats=self.repeats)

        return center_x, center_y

    def _measure_center(self, scan_center_x, scan_center_y, dir_angles, pass_name,
                        repeats=None):
        # Average over multiple measurements
        if repeats is None:
            repeats = self.repeats
        results_x = []
        results_y = []

        for repeat in range(repeats):
            peak_points = []

            for angle in dir_angles:
                peak_x, peak_y, peak_freq = self._scan_direction(scan_center_x, scan_center_y, angle)
                if peak_freq is None:
                    raise self.gcmd.error("Failed to detect peak in DIR=%d direction" % int(angle))
                rad = math.radians(angle)
                peak_points.append((peak_x, peak_y, math.cos(rad), math.sin(rad), angle))
                self.gcmd.respond_info("  DIR=%d scan peak: (%.4f, %.4f) freq=%.0f" % (int(angle), peak_x, peak_y, peak_freq))

            # Reconstruct the center coordinates from the peak projections using paired averaging or LSQ
            if self.pair_cancel:
                center_x, center_y = self._compute_center_paired_avg(peak_points)
            else:
                center_x, center_y = self._compute_center_lsq(peak_points)

            results_x.append(center_x)
            results_y.append(center_y)

            if repeats > 1:
                self.gcmd.respond_info("  Result %d: X=%.4f Y=%.4f" % (repeat + 1, center_x, center_y))

        # Compute the mean and standard deviation
        avg_x = sum(results_x) / len(results_x)
        avg_y = sum(results_y) / len(results_y)

        # Compute the standard deviation
        std_x = math.sqrt(sum((x - avg_x)**2 for x in results_x) / len(results_x))
        std_y = math.sqrt(sum((y - avg_y)**2 for y in results_y) / len(results_y))

        # Display statistics
        if repeats > 1:
            self.gcmd.respond_info("%s statistics: X=%.4f±%.4f Y=%.4f±%.4f (n=%d)" %
                                  (pass_name, avg_x, std_x, avg_y, std_y, repeats))

        return avg_x, avg_y

    def _compute_center_lsq(self, peak_points):
        """Reconstructs the center coordinates from each direction's peak projection using LSQ
        peak_points: [(peak_x, peak_y, cos_θ, sin_θ, angle_deg), ...]
        Each direction's peak is projected onto its scan axis, and LSQ is used to solve for the center
        """
        # Compute each direction's projection: proj_i = peak_x·cos(θ_i) + peak_y·sin(θ_i)
        projections = []  # [(cos_θ, sin_θ, projection_value)]
        for px, py, c, s, angle in peak_points:
            proj = px * c + py * s
            projections.append((c, s, proj))

        if not projections:
            return peak_points[0][0], peak_points[0][1]

        # LSQ solve: tx·cos(θ_i) + ty·sin(θ_i) = proj_i
        a11 = sum(c * c for c, s, _ in projections)
        a12 = sum(c * s for c, s, _ in projections)
        a22 = sum(s * s for c, s, _ in projections)
        c1 = sum(c * p for c, s, p in projections)
        c2 = sum(s * p for c, s, p in projections)

        det = a11 * a22 - a12 * a12
        if abs(det) < 1e-12:
            avg_x = sum(p / c if abs(c) > abs(s) else 0.0
                       for c, s, p in projections) / len(projections)
            avg_y = sum(p / s if abs(s) > abs(c) else 0.0
                       for c, s, p in projections) / len(projections)
            return avg_x, avg_y

        tx = (a22 * c1 - a12 * c2) / det
        ty = (a11 * c2 - a12 * c1) / det
        return tx, ty

    def _compute_center_paired_avg(self, peak_points):
        """Uses paired averaging to cancel motion lag, then reconstructs the center coordinates using LSQ
        peak_points: [(peak_x, peak_y, cos_θ, sin_θ, angle_deg), ...]
        Pairs θ with θ+180, averaging the projections to cancel lag
        """
        by_angle = {}
        for px, py, c, s, angle in peak_points:
            norm_angle = angle % 360.0
            by_angle[norm_angle] = (px, py)

        paired_projections = []
        used = set()

        for angle, (px, py) in by_angle.items():
            if angle in used:
                continue
            opposite = (angle + 180.0) % 360.0
            rad = math.radians(angle)
            cos_a = math.cos(rad)
            sin_a = math.sin(rad)

            if opposite in by_angle and opposite not in used:
                ox, oy = by_angle[opposite]
                proj_fwd = px * cos_a + py * sin_a
                proj_bwd = ox * cos_a + oy * sin_a
                proj_avg = (proj_fwd + proj_bwd) / 2.0
                paired_projections.append((cos_a, sin_a, proj_avg))
                used.add(angle)
                used.add(opposite)
            else:
                proj = px * cos_a + py * sin_a
                paired_projections.append((cos_a, sin_a, proj))

        if not paired_projections:
            return peak_points[0][0], peak_points[0][1]

        # LSQ solve
        a11 = sum(c * c for c, s, _ in paired_projections)
        a12 = sum(c * s for c, s, _ in paired_projections)
        a22 = sum(s * s for c, s, _ in paired_projections)
        c1 = sum(c * p for c, s, p in paired_projections)
        c2 = sum(s * p for c, s, p in paired_projections)

        det = a11 * a22 - a12 * a12
        if abs(det) < 1e-12:
            avg_x = sum(p / c if abs(c) > abs(s) else 0.0
                       for c, s, p in paired_projections) / len(paired_projections)
            avg_y = sum(p / s if abs(s) > abs(c) else 0.0
                       for c, s, p in paired_projections) / len(paired_projections)
            return avg_x, avg_y

        tx = (a22 * c1 - a12 * c2) / det
        ty = (a11 * c2 - a12 * c1) / det
        return tx, ty

    def _ensure_homed(self):
        """Ensures the printer has been homed"""
        curtime = self.printer.get_reactor().monotonic()
        kin_status = self.toolhead.get_kinematics().get_status(curtime)

        if 'x' not in kin_status['homed_axes'] or \
           'y' not in kin_status['homed_axes'] or \
           'z' not in kin_status['homed_axes']:
            raise self.gcmd.error("Must home printer first")

    def _scan_direction(self, center_x, center_y, angle_deg):
        """Scans along the specified angle direction, passing through the center point
        angle_deg: scan direction angle (degrees), 0=X+, 90=Y+
        The start point is offset -L/2 from the center point, the end point is offset +L/2
        """
        half_len = self.cal.scan_length / 2.0
        rad = math.radians(angle_deg)
        cos_a = math.cos(rad)
        sin_a = math.sin(rad)

        start_x = center_x - half_len * cos_a
        start_y = center_y - half_len * sin_a
        end_x = center_x + half_len * cos_a
        end_y = center_y + half_len * sin_a

        scan_name = "DIR_%d" % int(angle_deg)

        # Compute the actual Z coordinate (relative to coil_z, + = away from coil, - = toward coil)
        effective_scan_height = self.cal.scan_height + self.scan_height_offset
        # scan_z must not go below coil_z, to prevent collision with the coil
        if effective_scan_height < 0:
            self.gcmd.respond_info(
                "  WARNING: scan_z would be below coil_z, "
                "clamping effective offset from %.4f to 0" %
                effective_scan_height)
            effective_scan_height = 0.0
        scan_z = self.cal.coil_z + effective_scan_height
        safe_z = self.cal.coil_z + effective_scan_height + self.cal.scan_safe_z

        # Move to the start position (safe Z height)
        self._move_to_position(start_x, start_y, safe_z)
        # Lower to scan height
        self._move_to_position(start_x, start_y, scan_z)

        # Collect data
        samples = self._collect_scan_data(start_x, start_y, end_x, end_y)

        # Raise Z
        self._move_to_position(end_x, end_y, safe_z)

        # Find the peak and save the data
        return self._find_peak(samples, scan_name)

    def _move_to_position(self, x, y, z):
        """Moves the toolhead to the specified position, using different speeds for XY and Z"""
        # Move X then Y first (fast), then Z (slow)
        self.toolhead.manual_move([x, None, None], self.cal.travel_speed_xy)
        self.toolhead.manual_move([None, y, None], self.cal.travel_speed_xy)
        self.toolhead.manual_move([None, None, z], self.cal.travel_speed_z)
        self.toolhead.wait_moves()

    def _collect_scan_data(self, start_x, start_y, end_x, end_y):
        """
        Collects frequency data during a straight-line scan
        Returns: [(x, y, freq), ...]
        """
        # Compute scan parameters
        distance = math.sqrt((end_x - start_x)**2 + (end_y - start_y)**2)
        scan_time = distance / self.cal.scan_speed

        # Start sensor data collection
        reactor = self.printer.get_reactor()

        # Set up the data collection callback
        is_finished = False
        collected_samples = []
        def data_callback(msg):
            if is_finished:
                return False  # Return False to auto-remove from client_cbs
            if 'data' in msg:
                collected_samples.append(msg)
            return True

        # Register the callback first, to ensure no data is missed
        self.sensor.add_client(data_callback)

        # Wait briefly to let the sensor data stabilize and the callback take effect
        reactor.pause(reactor.monotonic() + 0.05)

        try:
            # Add a short delay to ensure the move queue is ready
            self.toolhead.dwell(0.010)

            # Get the current last move time as the reference (print time)
            move_start_print_time = self.toolhead.get_last_move_time()

            # Start the scan move
            self.toolhead.manual_move([end_x, end_y, None], self.cal.scan_speed)
            self.toolhead.wait_moves()

            # Wait a bit to make sure the last data arrives
            reactor.pause(reactor.monotonic() + 0.2)

        finally:
            is_finished = True

        # Process the collected samples
        # Use the print time at the start of the move as the reference
        samples = []

        # Statistics
        skipped_before = 0
        skipped_after = 0
        skipped_invalid = 0

        for msg in collected_samples:
            for samp_time, freq, z in msg['data']:
                # Skip invalid low-frequency samples (noise during sensor startup)
                if freq < 1000000:
                    skipped_invalid += 1
                    continue

                relative_time = samp_time - move_start_print_time

                if relative_time < -0.05 or relative_time > scan_time + 0.05:
                    if relative_time < 0:
                        skipped_before += 1
                    else:
                        skipped_after += 1
                    continue

                if relative_time < 0:
                    skipped_before += 1
                    continue

                t = relative_time / scan_time
                x = start_x + t * (end_x - start_x)
                y = start_y + t * (end_y - start_y)

                samples.append((x, y, freq))

        if len(samples) < 10:
            self.gcmd.respond_info("  WARNING: only %d valid samples" % len(samples))

        return samples

    def _auto_detect_peak_type(self, freq_data):
        """Automatically detects whether the signal is peak-shaped or valley-shaped
        Compares the average frequency of the center region vs the edge regions:
        - center high, edges low → peak (large coil signature)
        - center low, edges high → valley (small coil signature)
        Returns: 'peak' or 'valley'
        """
        n = len(freq_data)
        # Edge regions are the first and last 15%
        edge_margin = max(1, n // 7)
        edge_freqs = freq_data[:edge_margin] + freq_data[-edge_margin:]
        edge_avg = sum(edge_freqs) / len(edge_freqs)

        # Center region is the middle 30%
        center_start = n * 35 // 100
        center_end = n * 65 // 100
        if center_end <= center_start:
            center_end = center_start + 1
        center_freqs = freq_data[center_start:center_end]
        center_avg = sum(center_freqs) / len(center_freqs)

        detected = 'peak' if center_avg > edge_avg else 'valley'
        return detected, center_avg, edge_avg

    def _find_peak(self, samples, scan_name=None):
        """
        Detects the frequency extremum (peak or valley) produced by the tool
        peak mode: frequency is highest at the center (large coil)
        valley mode: frequency is lowest at the center (small coil)
        auto mode: automatically detects the signal shape
        Returns: (peak_x, peak_y, peak_freq)
        """
        if len(samples) < 10:
            self.gcmd.respond_info("  ERROR: Not enough samples (%d)" % len(samples))
            return None, None, None

        freq_data = [s[2] for s in samples]

        # Save data to a file for analysis (if enabled)
        if scan_name and self.cal.save_data:
            self._save_scan_data(samples, scan_name)

        # Auto-detect peak_type (redetected on every scan)
        detected_type, center_avg, edge_avg = self._auto_detect_peak_type(freq_data)
        auto_detect_info = (detected_type, center_avg, edge_avg)

        # Exclude the edge regions (first 15% and last 15%)
        margin = (len(freq_data) * 15) // 100
        search_start = max(1, margin)
        search_end = min(len(freq_data) - 1, len(freq_data) - margin)

        find_min = detected_type == 'valley'
        best_idx = search_start
        best_freq = freq_data[search_start]
        for i in range(search_start + 1, search_end):
            if find_min:
                if freq_data[i] < best_freq:
                    best_freq = freq_data[i]
                    best_idx = i
            else:
                if freq_data[i] > best_freq:
                    best_freq = freq_data[i]
                    best_idx = i

        # Use parabolic fitting near the extremum to improve sub-sample precision
        peak_x, peak_y = self._refine_peak_position(samples, best_idx, detected_type,
                                                       auto_detect_info)

        return peak_x, peak_y, freq_data[best_idx]

    def _save_scan_data(self, samples, scan_name):
        """Saves scan data to a CSV file"""
        # Check whether data saving is enabled
        if not self.cal.save_data:
            return

        import os
        try:
            # Save to the /tmp directory, no timestamp, overwrite directly
            filename = "/tmp/tool_eddy_scan_%s.csv" % scan_name
            sps = self.sensor.get_samples_per_second()
            with open(filename, "w") as f:
                # Write a parameter line (starts with # for CSV compatibility)
                f.write("# scan_length=%.1f,scan_speed=%.1f,"
                        "coil_inner_diameter=%.1f,peak_type=auto,"
                        "scan_height=%.1f,sps=%.0f\n" % (
                            self.cal.scan_length, self.cal.scan_speed,
                            self.cal.coil_inner_diameter,
                            self.cal.scan_height, sps))
                f.write("x_mm,y_mm,frequency_hz\n")
                for x, y, freq in samples:
                    f.write("%.4f,%.4f,%.1f\n" % (x, y, freq))
        except Exception as e:
            self.gcmd.respond_info("  Warning: could not save data: %s" % str(e))

    def _refine_peak_position(self, samples, peak_idx, detected_type,
                                auto_detect_info=None):
        """
        Uses weighted least squares to fit a parabola to the points near the peak, refining the peak position
        detected_type: 'peak' or 'valley', used to determine the expected parabola opening direction
        """
        n_samples = len(samples)

        # Get the points near the peak for parabolic fitting
        # Window size is computed from the coil inner diameter: data points within (inner diameter - 0.5mm)
        effective_radius = (self.cal.coil_inner_diameter - 0.5) / 2.0
        sps = self.sensor.get_samples_per_second()
        if sps > 0.0 and self.cal.scan_speed > 0.0:
            samples_per_mm = sps / self.cal.scan_speed
            half_window = int(samples_per_mm * effective_radius)
        else:
            half_window = 50

        start_idx = max(0, peak_idx - half_window)
        end_idx = min(n_samples, peak_idx + half_window + 1)

        # If not enough samples, use all available samples
        if end_idx - start_idx < 3:
            return samples[peak_idx][0], samples[peak_idx][1]

        # Extract the data within the window
        window_samples = samples[start_idx:end_idx]
        n = len(window_samples)

        # Normalize the x coordinate so the peak index is 0
        # This way the window center corresponds to x=0
        peak_local_idx = peak_idx - start_idx
        x_coords = []
        freqs = []
        weights = []

        for i, (x, y, freq) in enumerate(window_samples):
            # Normalized coordinate, peak position is 0
            x_norm = float(i - peak_local_idx)
            x_coords.append(x_norm)
            freqs.append(freq)

            # Use Gaussian weighting, closer to the peak = higher weight
            # Standard deviation set to half the window
            sigma = half_window / 2.0
            weight = math.exp(-(x_norm * x_norm) / (2.0 * sigma * sigma))
            weights.append(weight)

        # Fit a parabola y = a*x^2 + b*x + c using weighted least squares
        # Weights are w_i

        w_sum = sum(weights)
        w_sum_x = sum(w * x for w, x in zip(weights, x_coords))
        w_sum_x2 = sum(w * x * x for w, x in zip(weights, x_coords))
        w_sum_x3 = sum(w * x * x * x for w, x in zip(weights, x_coords))
        w_sum_x4 = sum(w * x * x * x * x for w, x in zip(weights, x_coords))
        w_sum_y = sum(w * y for w, y in zip(weights, freqs))
        w_sum_xy = sum(w * x * y for w, x, y in zip(weights, x_coords, freqs))
        w_sum_x2y = sum(w * x * x * y for w, x, y in zip(weights, x_coords, freqs))

        # Build the determinant of the coefficient matrix for the weighted normal equations
        det = (w_sum_x4 * (w_sum_x2 * w_sum - w_sum_x * w_sum_x) -
               w_sum_x3 * (w_sum_x3 * w_sum - w_sum_x * w_sum_x2) +
               w_sum_x2 * (w_sum_x3 * w_sum_x - w_sum_x2 * w_sum_x2))

        if abs(det) < 1e-10:
            return samples[peak_idx][0], samples[peak_idx][1]

        # Compute a
        det_a = (w_sum_x2y * (w_sum_x2 * w_sum - w_sum_x * w_sum_x) -
                 w_sum_xy * (w_sum_x3 * w_sum - w_sum_x * w_sum_x2) +
                 w_sum_y * (w_sum_x3 * w_sum_x - w_sum_x2 * w_sum_x2))
        a = det_a / det

        # Compute b
        det_b = (w_sum_x4 * (w_sum_xy * w_sum - w_sum_x * w_sum_y) -
                 w_sum_x3 * (w_sum_x2y * w_sum - w_sum_x * w_sum_y) +
                 w_sum_x2 * (w_sum_x2y * w_sum_x - w_sum_xy * w_sum_x2))
        b = det_b / det

        # Compute c
        det_c = (w_sum_x4 * (w_sum_x2 * w_sum_y - w_sum_x * w_sum_xy) -
                 w_sum_x3 * (w_sum_x3 * w_sum_y - w_sum_x * w_sum_x2y) +
                 w_sum_x2 * (w_sum_x3 * w_sum_xy - w_sum_x2 * w_sum_x2y))
        c = det_c / det

        # Compute the weighted R² goodness of fit
        y_pred = [a * x * x + b * x + c for x in x_coords]
        w_mean = w_sum_y / w_sum
        ss_res = sum(w * (y - yp) ** 2 for w, y, yp in zip(weights, freqs, y_pred))
        ss_tot = sum(w * (y - w_mean) ** 2 for w, y in zip(weights, freqs))
        r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
        self.gcmd.respond_info("  Parabolic fit: R²=%.4f (n=%d) peak_type=%s" %
            (r_squared, n, detected_type))

        # Check fit quality
        if abs(a) < 1e-10:
            return samples[peak_idx][0], samples[peak_idx][1]

        # peak mode: a<0 means opens downward (peak), valley mode: a>0 means opens upward (valley)
        find_min = detected_type == 'valley'
        if find_min:
            if a < 0:
                return samples[peak_idx][0], samples[peak_idx][1]
        else:
            if a > 0:
                return samples[peak_idx][0], samples[peak_idx][1]

        x_peak = -b / (2.0 * a)

        # Clamp to a reasonable range (not exceeding the window size)
        max_offset = half_window * 0.5  # Deviate from the peak by at most half a window
        x_peak = max(-max_offset, min(max_offset, x_peak))

        # Convert back to a global index
        peak_global_idx = peak_local_idx + x_peak

        # Interpolate to the actual position
        peak_idx_floor = int(peak_global_idx)
        if peak_idx_floor < 0:
            peak_idx_floor = 0
        if peak_idx_floor >= n - 1:
            peak_idx_floor = n - 2
        t = peak_global_idx - peak_idx_floor

        # Get the actual coordinates from the window samples
        p1 = window_samples[peak_idx_floor]
        p2 = window_samples[peak_idx_floor + 1]
        peak_x = p1[0] + t * (p2[0] - p1[0])
        peak_y = p1[1] + t * (p2[1] - p1[1])

        return peak_x, peak_y


def load_config_prefix(config):
    return ToolEddyCalibration(config)
