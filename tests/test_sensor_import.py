"""Unit tests for the sensor driver import resolution over the reference
firmwares.

Every stand-in below is an import callable mirroring the module layout of one
reference build, read from that build's tree at the cited places, and every
expected strategy is the literal the design table holds, never a value
computed here.
"""

import pytest

import eddy_tool_calibration as etc


class _Module:
    pass


def _importer(available):
    module = _Module()

    def import_module(path):
        if path in available:
            return module
        missing = path
        while missing not in available and '.' in missing:
            missing = missing.rsplit('.', 1)[0]
        raise ModuleNotFoundError(
            "No module named '%s'" % (missing,), name=missing)

    return import_module, module


def _kalico_importer():
    # Kalico development and Kalico 3b98cf51 both ship klippy/__init__.py, so
    # klippy.extras.ldc1612 imports.
    return _importer({'klippy', 'klippy.extras', 'klippy.extras.ldc1612'})


def _klipper_importer():
    # Klipper v0.13.0 and Klipper master ship no klippy/__init__.py, and
    # klippy.py:103 imports extras modules as extras. plus the name.
    return _importer({'extras', 'extras.ldc1612'})


def test_kalico_development_resolves_through_the_klippy_package():
    import_module, module = _kalico_importer()

    strategy, imported = etc.resolve_sensor_import(import_module)

    assert strategy == 'klippy_package'
    assert imported is module


def test_kalico_3b98cf51_resolves_through_the_klippy_package():
    import_module, module = _kalico_importer()

    strategy, imported = etc.resolve_sensor_import(import_module)

    assert strategy == 'klippy_package'
    assert imported is module


def test_klipper_v0130_resolves_through_the_extras_package():
    import_module, module = _klipper_importer()

    strategy, imported = etc.resolve_sensor_import(import_module)

    assert strategy == 'extras_package'
    assert imported is module


def test_klipper_master_resolves_through_the_extras_package():
    import_module, module = _klipper_importer()

    strategy, imported = etc.resolve_sensor_import(import_module)

    assert strategy == 'extras_package'
    assert imported is module


def test_a_driver_module_with_a_broken_dependency_fails_with_its_own_cause():
    # Arrange: the klippy package exists but the driver module itself raises
    # for a dependency of its own.
    def import_module(path):
        if path == 'klippy.extras.ldc1612':
            raise ModuleNotFoundError(
                "No module named 'bulk_sensor'", name='bulk_sensor')
        raise ModuleNotFoundError("No module named '%s'" % (path,), name=path)

    with pytest.raises(ImportError) as excinfo:
        etc.resolve_sensor_import(import_module)

    assert excinfo.value.name == 'bulk_sensor'


def test_a_build_matching_no_strategy_is_refused_naming_every_strategy():
    import_module, _module = _importer(set())

    with pytest.raises(ValueError) as excinfo:
        etc.resolve_sensor_import(import_module)
    message = str(excinfo.value)

    assert "sensor driver import" in message
    assert ("strategies this plugin knows: klippy_package, "
            "extras_package") in message
    assert "klippy.extras.ldc1612 imports: no" in message
    assert "extras.ldc1612 imports: no" in message
