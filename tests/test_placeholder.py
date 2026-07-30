"""Guards the standalone-import constraint of the plugin module.

The plugin module must import cleanly with no klippy installation present
(these tests run standalone on a dev machine), so eddy_tool_calibration.py
keeps every klippy import inside a function or method body rather than at
module scope. The test below reads the module source and asserts that no
top-level statement imports klippy.
"""

import ast
import pathlib
import sys

import eddy_tool_calibration as etc


def _module_source():
    path = pathlib.Path(etc.__file__)
    return path.read_text(encoding="utf-8")


def _toplevel_import_names(source):
    """Module names imported by statements at module scope only."""
    names = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    return names


def test_no_klippy_import_at_module_scope():
    # Arrange
    source = _module_source()

    # Act
    imported = _toplevel_import_names(source)

    # Assert: no module-scope import reaches into the klippy package.
    assert [name for name in imported if name.split(".")[0] == "klippy"] == []


def test_importing_the_plugin_does_not_pull_in_klippy():
    # Assert: importing the module (done at the top of this file, with no
    # klippy installed) left no klippy package loaded.
    assert not [name for name in sys.modules if name.split(".")[0] == "klippy"]
