"""Placeholder test: confirms the plugin module imports without klippy.

The plugin module must import cleanly with no klippy installation present
(these tests run standalone on a dev machine), so eddy_tool_calibration.py
keeps all klippy imports inside function/method bodies rather than at module
scope. This test just exercises that constraint; real math-function tests
land alongside the ported algorithms.
"""

import importlib.util
import pathlib


def _load_plugin_module():
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    module_path = repo_root / "eddy_tool_calibration.py"
    spec = importlib.util.spec_from_file_location(
        "eddy_tool_calibration", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plugin_module_imports():
    module = _load_plugin_module()
    assert hasattr(module, "EddyToolCalibration")
    assert hasattr(module, "load_config")
