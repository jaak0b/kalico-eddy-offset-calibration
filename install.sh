#!/bin/sh
# Symlink eddy_tool_calibration.py into a firmware checkout's module
# directory: klippy/plugins/ on Kalico, klippy/extras/ on stock Klipper
# (see docs/design.md).
#
# Usage: ./install.sh [FIRMWARE_DIR]
# FIRMWARE_DIR can also be given via the KALICO_DIR environment variable.
# Defaults to $HOME/klipper, the documented clone location of both firmwares.

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
        echo "install.sh: cannot find \$HOME/klipper; pass the firmware directory as an argument or set KALICO_DIR" >&2
        exit 1
    fi
fi

if [ ! -d "$TARGET_DIR" ]; then
    echo "install.sh: firmware directory not found: $TARGET_DIR" >&2
    exit 1
fi

# Kalico's module loader is klippy/printer.py scanning "klippy.plugins."
# (printer.py:194 on development, :282 at 3b98cf51); the plugins directory
# itself may not exist yet in a fresh checkout. Stock Klipper has no
# printer.py and klippy/klippy.py:90-103 loads only from klippy/extras/.
if [ -f "$TARGET_DIR/klippy/printer.py" ] \
        && grep -q 'klippy\.plugins' "$TARGET_DIR/klippy/printer.py"; then
    INSTALL_DIR="$TARGET_DIR/klippy/plugins"
elif [ -f "$TARGET_DIR/klippy/klippy.py" ] \
        && [ -d "$TARGET_DIR/klippy/extras" ]; then
    INSTALL_DIR="$TARGET_DIR/klippy/extras"
    echo "install.sh: $TARGET_DIR is a stock Klipper checkout, which loads modules only from klippy/extras/"
    echo "install.sh: the symlink is an untracked file there, so git and Moonraker's update manager will report the checkout as dirty until it is removed"
else
    echo "install.sh: $TARGET_DIR is neither a Kalico nor a stock Klipper checkout: it has no klippy/printer.py loading klippy.plugins, and no klippy/klippy.py with a klippy/extras/ directory" >&2
    exit 1
fi

if [ ! -d "$INSTALL_DIR" ]; then
    mkdir -p "$INSTALL_DIR" || {
        echo "install.sh: failed to create $INSTALL_DIR" >&2
        exit 1
    }
    echo "install.sh: created $INSTALL_DIR"
fi

ln -sf "$PLUGIN_SRC" "$INSTALL_DIR/eddy_tool_calibration.py" || {
    echo "install.sh: failed to symlink into $INSTALL_DIR" >&2
    exit 1
}

echo "install.sh: linked $PLUGIN_SRC -> $INSTALL_DIR/eddy_tool_calibration.py"
echo "install.sh: restart the klippy service (or firmware restart) to load it"
