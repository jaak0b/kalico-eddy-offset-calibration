"""Unit tests for the batch run predicate.

The literals mirror how the reference firmwares populate start_args: every
build's printer.py main stores the -o option under the debugoutput key only
when the option was given (Kalico development printer.py:652-653), so a live
printer's start_args carries no such key at all.
"""

import eddy_tool_calibration as etc


def test_a_debugoutput_path_marks_a_batch_run():
    assert etc.is_batch_run({'debugoutput': '/tmp/output'}) is True


def test_a_live_printer_without_the_key_is_not_a_batch_run():
    assert etc.is_batch_run({'config_file': '/tmp/printer.cfg'}) is False
