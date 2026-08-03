# Copyright (C) 2026  Jakob
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from integration_test import FORBIDDEN_ALWAYS, strip_benign_traceback

# Klipper master klippy/util.py:54-61: the shape logged when
# /proc/device-tree/model is missing on the host running the harness.
BENIGN_BLOCK = (
    "Exception on read /proc/device-tree/model: "
    "Traceback (most recent call last):\n"
    '  File "klippy/util.py", line 56, in _try_read_file\n'
    "    with open(filename, 'r') as f:\n"
    "FileNotFoundError: [Errno 2] No such file or directory: "
    "'/proc/device-tree/model'\n"
)

REAL_TRACEBACK = (
    "Unhandled exception during run\n"
    "Traceback (most recent call last):\n"
    '  File "klippy/gcode.py", line 100, in _process_commands\n'
    "    handler(gcmd)\n"
    "AttributeError: 'NoneType' object has no attribute 'freq_conv'\n"
)


def test_benign_device_tree_block_alone_carries_no_forbidden_marker():
    checked = strip_benign_traceback(
        "Starting printer\n" + BENIGN_BLOCK + "Config loaded\n")
    for marker in FORBIDDEN_ALWAYS:
        assert marker not in checked, marker


def test_real_traceback_beside_benign_block_still_trips():
    checked = strip_benign_traceback(
        "Starting printer\n" + BENIGN_BLOCK + REAL_TRACEBACK
        + "Config loaded\n")
    assert 'Traceback (most recent call last)' in checked


def test_surrounding_lines_survive_the_strip():
    checked = strip_benign_traceback(
        "Starting printer\n" + BENIGN_BLOCK + "Config loaded\n")
    assert checked == "Starting printer\nConfig loaded\n"
