"""Unit tests for the sensor clock resolution over the reference firmwares.

Every stand-in below mirrors the clock-related attribute surface of one
reference build, read from that build's ldc1612.py at the cited lines, and
every expected clock is the literal those lines hold, never a value computed
here.
"""

import pytest

import eddy_tool_calibration as etc


class _Namespace:
    def __init__(self, **attributes):
        for name, value in attributes.items():
            setattr(self, name, value)


def _kalico_development_driver():
    # Kalico development ldc1612.py:103-109: clock_freq holds the configured
    # clock (default DEFAULT_LDC1612_FREQ = 12000000, :16), beside sensor_div
    # and freq_conv.
    return _Namespace(clock_freq=12000000, sensor_div=2,
                      freq_conv=0.0894069671630859375)


def _kalico_3b98cf51_driver():
    # Kalico 3b98cf51 ldc1612.py: the driver object carries no clock
    # attribute at all; the clock lives only in the module constant.
    return _Namespace()


def _klipper_v0130_driver():
    # Klipper v0.13.0 ldc1612.py:90: frequency holds the configured clock
    # (default DEFAULT_LDC1612_FREQ = 12000000, :15).
    return _Namespace(frequency=12000000)


def _klipper_master_driver():
    # Klipper master ldc1612.py:91-96: clock_freq holds the configured clock
    # (default DEFAULT_LDC1612_FREQ = 12000000, :15), beside sensor_div and
    # freq_conv.
    return _Namespace(clock_freq=12000000, sensor_div=2,
                      freq_conv=0.0894069671630859375)


def _module_with_constant():
    # Kalico 3b98cf51 ldc1612.py:16: LDC1612_FREQ = 12000000.
    return _Namespace(LDC1612_FREQ=12000000)


def _module_without_constant():
    # Kalico development, Klipper v0.13.0 and Klipper master all name their
    # module constant DEFAULT_LDC1612_FREQ, not LDC1612_FREQ.
    return _Namespace(DEFAULT_LDC1612_FREQ=12000000)


def test_kalico_development_resolves_through_the_clock_freq_attribute():
    strategy, clock = etc.resolve_sensor_clock(
        _kalico_development_driver(), _module_without_constant())

    assert strategy == 'driver_clock_freq'
    assert clock == 12000000.0


def test_klipper_master_resolves_through_the_clock_freq_attribute():
    strategy, clock = etc.resolve_sensor_clock(
        _klipper_master_driver(), _module_without_constant())

    assert strategy == 'driver_clock_freq'
    assert clock == 12000000.0


def test_klipper_v0130_resolves_through_the_frequency_attribute():
    strategy, clock = etc.resolve_sensor_clock(
        _klipper_v0130_driver(), _module_without_constant())

    assert strategy == 'driver_frequency'
    assert clock == 12000000.0


def test_kalico_3b98cf51_resolves_through_the_module_constant():
    strategy, clock = etc.resolve_sensor_clock(
        _kalico_3b98cf51_driver(), _module_with_constant())

    assert strategy == 'module_clock'
    assert clock == 12000000.0


def test_a_configured_clock_is_read_rather_than_the_default():
    # Arrange: the crab board's frequency option puts 24000000 in clock_freq
    # on a build that carries the option.
    driver = _Namespace(clock_freq=24000000, sensor_div=1,
                        freq_conv=0.0894069671630859375)

    strategy, clock = etc.resolve_sensor_clock(
        driver, _module_without_constant())

    assert strategy == 'driver_clock_freq'
    assert clock == 24000000.0


def test_a_build_matching_no_strategy_is_refused_naming_every_strategy():
    with pytest.raises(ValueError) as excinfo:
        etc.resolve_sensor_clock(_Namespace(), _module_without_constant())
    message = str(excinfo.value)

    assert "sensor clock" in message
    assert ("strategies this plugin knows: driver_clock_freq, "
            "driver_frequency, module_clock") in message
    assert "ldc1612 driver carries clock_freq: no" in message
    assert "ldc1612 driver carries frequency: no" in message
    assert "ldc1612 module carries LDC1612_FREQ: no" in message
