# eddy_tool_calibration

Kalico (Klipper fork) plugin that calibrates per-tool XYZ nozzle offsets on
toolchanger 3D printers using a bed-mounted LDC1612 eddy-current sensor
board. Non-contact: the sensor sees only metal, so a dirty nozzle does not
affect the measurement. XY offsets come from directional scans over the
coil with parabolic sub-sample fitting of the symmetric frequency response
(material-independent). Z offset comes from a frequency-vs-height descent
curve anchored by a one-time per-tool contact reference. Kalico only; no
stock-Klipper support. Offsets are printed to the console in v1; there is no
toolchanger integration or persistence yet.

**Status: pre-hardware, unvalidated, under active development.** The
sensor board has not yet been built and no measurement has been taken on
real hardware. Do not rely on this plugin for production calibration until
the validation plan in `docs/design.md` has been run and confirmed.

## Hardware

Base design: chengxg's open-source "Little Crab" dual-coil eddy-current
board.

- Upstream repo: https://github.com/chengxg/tool_eddy_calibration
- oshwhub project (EasyEDA source): https://oshwhub.com/cxg01/project_lbabffjk

## Install

```
cd ~
git clone <repo url placeholder> eddy_tool_calibration
cd eddy_tool_calibration
./install.sh
```

`install.sh` symlinks `eddy_tool_calibration.py` into your Kalico
installation's `klippy/plugins/` directory (default `~/kalico`, falls back
to `~/klipper`, or pass the directory as an argument). After installing,
issue a firmware restart (`FIRMWARE_RESTART`) so Kalico loads the module.

## Config example

```ini
[eddy_tool_calibration]
# --- sensor (embedded ldc1612, no separate section needed) ---
i2c_mcu: mcu
i2c_software_scl_pin: PB6
i2c_software_sda_pin: PB7
i2c_address: 42
frequency: 24000000
reg_drive_current: 22
# --- geometry ---
coil_x: 350.0
coil_y: 5.0
scan_height: 1.0
z_start: 5.0
z_stop: 0.5
# --- scan tuning ---
scan_speed: 4.0
scan_length: 4.0
scan_angles: 45, 135
pair_scans: True
samples_min: 100
save_csv: False
```

See `docs/design.md` for the full schema description and rationale.

## Commands

- `EDDY_QUERY`: print the current eddy sensor frequency reading, for a
  wiring sanity check.
- `EDDY_LOCATE`: coarse raster scan over the configured coil position to
  find and store the refined coil center for the session.
- `EDDY_CALIBRATE_TOOL [T=<n>]`: run the full XY and Z measurement for the
  mounted tool and print its offsets relative to the T0 baseline.
- `EDDY_SET_BASELINE`: declare the currently mounted tool as the T0
  baseline for this session.
- `EDDY_SET_Z_REF [T=<n>] Z=<real_offset>`: bind the current tool's
  measured frequency curve to a real Z offset obtained by another method.

## Moonraker update_manager

Add this block once the repo is published (the `origin` value below is a
placeholder until then):

```
[update_manager eddy_tool_calibration]
type: git_repo
path: ~/eddy_tool_calibration
origin: <repo url placeholder>
primary_branch: main
is_system_service: False
```

## License

GNU GPLv3, see `LICENSE`. This plugin is a derivative work of chengxg's
GPLv3 `tool_eddy_calibration` (kept unmodified in `reference/` for
provenance); its algorithms are ported, not vendored.
