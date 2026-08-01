#!/bin/sh
# Symlink eddy_tool_calibration.py into a Kalico install's klippy/plugins/
# directory. Kalico loads modules from klippy/plugins/ the same way it loads
# klippy/extras/ (see docs/design.md).
#
# Usage: ./install.sh [KALICO_DIR]
# KALICO_DIR can also be given via the KALICO_DIR environment variable.
# Defaults to $HOME/klipper, Kalico's documented clone location.

set -e

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PLUGIN_SRC="$SCRIPT_DIR/eddy_tool_calibration.py"

if [ ! -f "$PLUGIN_SRC" ]; then
    echo "install.sh: cannot find eddy_tool_calibration.py next to this script" >&2
    exit 1
fi

TARGET_DIR="${1:-${KALICO_DIR:-}}"

if [ -z "$TARGET_DIR" ]; then
    if [ -d "$HOME/klipper" ]; then
        TARGET_DIR="$HOME/klipper"
    else
        echo "install.sh: cannot find \$HOME/klipper; pass the Kalico directory as an argument or set KALICO_DIR" >&2
        exit 1
    fi
fi

if [ ! -d "$TARGET_DIR" ]; then
    echo "install.sh: Kalico directory not found: $TARGET_DIR" >&2
    exit 1
fi

PLUGINS_DIR="$TARGET_DIR/klippy/plugins"

if [ ! -d "$PLUGINS_DIR" ]; then
    mkdir -p "$PLUGINS_DIR" || {
        echo "install.sh: failed to create $PLUGINS_DIR" >&2
        exit 1
    }
    echo "install.sh: created $PLUGINS_DIR"
fi

ln -sf "$PLUGIN_SRC" "$PLUGINS_DIR/eddy_tool_calibration.py" || {
    echo "install.sh: failed to symlink into $PLUGINS_DIR" >&2
    exit 1
}

echo "install.sh: linked $PLUGIN_SRC -> $PLUGINS_DIR/eddy_tool_calibration.py"
echo "install.sh: restart Kalico's klippy service (or firmware restart) to load it"
