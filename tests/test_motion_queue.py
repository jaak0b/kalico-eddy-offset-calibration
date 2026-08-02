"""Unit tests for the motion queue resolution over the reference firmwares.

Every stand-in below mirrors the motion queue attribute surface of one
reference build, read from that build's motion_report.py at the cited lines,
and every expected strategy is the literal the design table holds, never a
value computed here.
"""

import pytest

import eddy_tool_calibration as etc


class _Namespace:
    def __init__(self, **attributes):
        for name, value in attributes.items():
            setattr(self, name, value)


def _kalico_development_motion_report():
    # Kalico development motion_report.py:210: the dictionary is trapqs.
    return _Namespace(trapqs={})


def _kalico_3b98cf51_motion_report():
    # Kalico 3b98cf51 motion_report.py:210: the dictionary is trapqs.
    return _Namespace(trapqs={})


def _klipper_v0130_motion_report():
    # Klipper v0.13.0 motion_report.py:137: the dictionary is trapqs.
    return _Namespace(trapqs={})


def _klipper_master_motion_report():
    # Klipper master motion_report.py:149: the dictionary is dtrapqs.
    return _Namespace(dtrapqs={})


def test_kalico_development_resolves_through_the_trapqs_attribute():
    strategy = etc.resolve_motion_queue(_kalico_development_motion_report())

    assert strategy == 'trapqs'


def test_kalico_3b98cf51_resolves_through_the_trapqs_attribute():
    strategy = etc.resolve_motion_queue(_kalico_3b98cf51_motion_report())

    assert strategy == 'trapqs'


def test_klipper_v0130_resolves_through_the_trapqs_attribute():
    strategy = etc.resolve_motion_queue(_klipper_v0130_motion_report())

    assert strategy == 'trapqs'


def test_klipper_master_resolves_through_the_dtrapqs_attribute():
    strategy = etc.resolve_motion_queue(_klipper_master_motion_report())

    assert strategy == 'dtrapqs'


def test_a_build_matching_no_strategy_is_refused_naming_every_strategy():
    with pytest.raises(ValueError) as excinfo:
        etc.resolve_motion_queue(_Namespace())
    message = str(excinfo.value)

    assert "motion queue" in message
    assert "strategies this plugin knows: trapqs, dtrapqs" in message
    assert "motion_report carries trapqs: no" in message
    assert "motion_report carries dtrapqs: no" in message
