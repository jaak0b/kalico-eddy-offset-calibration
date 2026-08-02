"""Unit tests for the preheat wait resolution over the reference firmwares
and for the reactor_poll wait loop.

Every stand-in below mirrors the wait-related attribute surface of one
reference build, read from that build's sources at the cited lines, and every
expected strategy is the literal the design table holds, never a value
computed here.
"""

import pytest

import eddy_tool_calibration as etc


class _Namespace:
    def __init__(self, **attributes):
        for name, value in attributes.items():
            setattr(self, name, value)


def _method():
    return lambda *args: None


def _kalico_development_printer():
    # Kalico development printer.py:504: wait_while is a printer method.
    return _Namespace(wait_while=_method())


def _kalico_3b98cf51_printer():
    # Kalico 3b98cf51 printer.py:624: wait_while is a printer method.
    return _Namespace(wait_while=_method())


def _klipper_printer():
    # Klipper v0.13.0 and Klipper master klippy.py: no wait_while at all.
    return _Namespace()


def _heaters_with_get_temp():
    # All four reference builds carry heaters._get_temp, the M105 line
    # (Klipper v0.13.0 heaters.py:323, master heaters.py:331, Kalico
    # development heaters.py:1442).
    return _Namespace(_get_temp=_method())


def test_kalico_development_resolves_through_printer_wait_while():
    strategy = etc.resolve_preheat_wait(
        _kalico_development_printer(), _heaters_with_get_temp())

    assert strategy == 'printer_wait_while'


def test_kalico_3b98cf51_resolves_through_printer_wait_while():
    strategy = etc.resolve_preheat_wait(
        _kalico_3b98cf51_printer(), _heaters_with_get_temp())

    assert strategy == 'printer_wait_while'


def test_klipper_v0130_resolves_through_the_reactor_poll():
    strategy = etc.resolve_preheat_wait(
        _klipper_printer(), _heaters_with_get_temp())

    assert strategy == 'reactor_poll'


def test_klipper_master_resolves_through_the_reactor_poll():
    strategy = etc.resolve_preheat_wait(
        _klipper_printer(), _heaters_with_get_temp())

    assert strategy == 'reactor_poll'


def test_a_build_matching_no_strategy_is_refused_naming_every_strategy():
    with pytest.raises(ValueError) as excinfo:
        etc.resolve_preheat_wait(_kalico_development_printer(), _Namespace())
    message = str(excinfo.value)

    assert "preheat wait" in message
    assert ("strategies this plugin knows: printer_wait_while, "
            "reactor_poll") in message
    assert "printer carries wait_while: yes" in message
    assert "heaters carries _get_temp: no" in message


def test_a_build_without_either_surface_reports_both_as_missing():
    with pytest.raises(ValueError) as excinfo:
        etc.resolve_preheat_wait(_klipper_printer(), _Namespace())
    message = str(excinfo.value)

    assert "printer carries wait_while: no" in message
    assert "heaters carries _get_temp: no" in message


class _Reactor:
    def __init__(self):
        self.now = 100.0
        self.pauses = []

    def monotonic(self):
        return self.now

    def pause(self, waketime):
        self.pauses.append(waketime)
        self.now = waketime
        return self.now


class _WaitError(Exception):
    pass


def test_the_poll_wait_returns_once_the_condition_clears():
    reactor = _Reactor()
    readings = [True, True, False]

    etc.preheat_poll_wait(
        reactor, lambda: False, lambda eventtime: readings.pop(0),
        _WaitError, "T0 to reach 210.0 C on the heater extruder")

    assert reactor.pauses == [101.0, 102.0]


def test_the_poll_wait_raises_when_the_printer_shuts_down():
    reactor = _Reactor()

    with pytest.raises(_WaitError) as excinfo:
        etc.preheat_poll_wait(
            reactor, lambda: True, lambda eventtime: True,
            _WaitError, "T0 to reach 210.0 C on the heater extruder")

    assert "shut down" in str(excinfo.value)
    assert "T0 to reach 210.0 C on the heater extruder" in str(excinfo.value)
