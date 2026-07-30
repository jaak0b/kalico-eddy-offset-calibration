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

# ---------------------------------------------------------------------------
# Framework-agnostic math functions live in this section.
#
# Parabolic sub-sample fitting, forward/reverse pair averaging, least-squares
# center reconstruction, and Z-curve evaluation belong here once ported from
# reference/tool_eddy_calibration.py. These functions must take plain
# numbers/arrays only and must never import klippy, so they stay unit
# testable standalone (see tests/). Not yet implemented.
# ---------------------------------------------------------------------------


class EddyToolCalibration:
    """Klippy extra: EDDY_QUERY / EDDY_LOCATE / EDDY_CALIBRATE_TOOL /
    EDDY_SET_BASELINE / EDDY_SET_Z_REF gcode commands.
    """

    def __init__(self, config):
        self.printer = config.get_printer()
        self.name = config.get_name()

        # Sensor wiring/config (embedded ldc1612, no separate section
        # needed; see docs/design.md "Config schema").
        self.i2c_mcu = config.get('i2c_mcu', 'mcu')
        self.i2c_software_scl_pin = config.get(
            'i2c_software_scl_pin', None)
        self.i2c_software_sda_pin = config.get(
            'i2c_software_sda_pin', None)
        self.i2c_address = config.getint('i2c_address', 42)
        self.frequency = config.getint('frequency', 24000000)
        self.reg_drive_current = config.getint('reg_drive_current', None)

        # Geometry.
        self.coil_x = config.getfloat('coil_x', 350.0)
        self.coil_y = config.getfloat('coil_y', 5.0)
        self.scan_height = config.getfloat('scan_height', 1.0)
        self.z_start = config.getfloat('z_start', 5.0)
        self.z_stop = config.getfloat('z_stop', 0.5)

        # Scan tuning.
        self.scan_speed = config.getfloat('scan_speed', 4.0)
        self.scan_length = config.getfloat('scan_length', 4.0)
        self.scan_angles = [
            float(a) for a in
            config.get('scan_angles', '45, 135').split(',')
        ]
        self.pair_scans = config.getboolean('pair_scans', True)
        self.samples_min = config.getint('samples_min', 100)
        self.save_csv = config.getboolean('save_csv', False)

        # TODO: instantiate the LDC1612 sensor wrapper here once the
        # measurement code lands (klippy.extras.ldc1612.LDC1612 or an
        # equivalent config-driven wrapper per docs/design.md).

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

    cmd_EDDY_QUERY_help = (
        "Print the current eddy sensor frequency reading, for a wiring "
        "sanity check.")

    def cmd_EDDY_QUERY(self, gcmd):
        raise gcmd.error("EDDY_QUERY not implemented yet")

    cmd_EDDY_LOCATE_help = (
        "Coarse raster scan over the configured coil position to find and "
        "store the refined coil center for this session.")

    def cmd_EDDY_LOCATE(self, gcmd):
        raise gcmd.error("EDDY_LOCATE not implemented yet")

    cmd_EDDY_CALIBRATE_TOOL_help = (
        "Run the full XY and Z eddy-current measurement for the mounted "
        "tool and print its offsets relative to the T0 baseline.")

    def cmd_EDDY_CALIBRATE_TOOL(self, gcmd):
        raise gcmd.error("EDDY_CALIBRATE_TOOL not implemented yet")

    cmd_EDDY_SET_BASELINE_help = (
        "Declare the currently mounted tool as the T0 baseline for this "
        "session.")

    def cmd_EDDY_SET_BASELINE(self, gcmd):
        raise gcmd.error("EDDY_SET_BASELINE not implemented yet")

    cmd_EDDY_SET_Z_REF_help = (
        "Bind the current tool's measured frequency curve to a real Z "
        "offset obtained by another method (Z= parameter).")

    def cmd_EDDY_SET_Z_REF(self, gcmd):
        raise gcmd.error("EDDY_SET_Z_REF not implemented yet")


def load_config(config):
    return EddyToolCalibration(config)
