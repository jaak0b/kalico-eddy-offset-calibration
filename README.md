# eddy_tool_calibration

Kalico (Klipper fork) plugin that calibrates per-tool XYZ nozzle offsets on
toolchanger 3D printers using a bed-mounted LDC1612 eddy-current sensor
board. Non-contact: the sensor sees only metal, so a dirty nozzle does not
affect the measurement. XY offsets come from directional scans over the
coil with parabolic sub-sample fitting of the symmetric frequency response
(material-independent). Z offset comes from a frequency-vs-height descent
curve anchored by a one-time per-tool contact reference. Kalico only; no
stock-Klipper support. Offsets are printed to the console in v1; the plugin
persists its Z references itself, and there is no toolchanger integration
yet.

**Status: pre-hardware, unvalidated, under active development.** The
sensor board has not yet been built and no measurement has been taken on
real hardware. Do not rely on this plugin for production calibration until
the validation plan in `docs/design.md` has been run and confirmed.

**Requirements:** the BTT Eddy Coil works on any Kalico with a
`klippy/plugins/` directory (2023 onward), as long as `frequency:` is left
out of the config. The Little Crab board needs Kalico from March 2026 or
newer, because that is when the `frequency` config option was added.

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
# --- geometry: every height below is measured from the coil top face ---
coil_x: 350.0
coil_y: 5.0
coil_z: 0.0                     # machine Z of the coil top face
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
csv_dir: EddyToolCalibration/data
# --- Z offsets: leave calibrate_z off to skip the Z descent entirely ---
calibrate_z: True
# --- contact switch, required when calibrate_z is True ---
switch_pin: ^PA1                # endstop pin the nozzle presses
switch_x: 340.0                 # machine X of the nozzle over the switch
switch_y: 5.0                   # machine Y of the nozzle over the switch
switch_probe_z_start: 3.0       # machine Z the press starts from
switch_probe_tolerance: 0.020   # mm the counted presses may disagree by
```

See `docs/design.md` for the full schema description and rationale.

## Commands

- `EDDY_QUERY`: print the current eddy sensor frequency reading, for a
  wiring sanity check.
- `EDDY_LOCATE [DEBUG=1]`: coarse raster scan over the configured coil
  position to find and store the refined coil center for the session.
  `DEBUG=1` also prints each scan pass's diagnostic rows.
- `EDDY_CALIBRATE_OFFSET T=<n> [DEBUG=1]`: measure the mounted tool over the
  coil and print its offsets relative to T0. Run `T=0` first with the
  baseline tool mounted; every later tool is measured against it. `DEBUG=1`
  also prints each scan pass's diagnostic rows.
- `EDDY_CALIBRATE_Z T=<n> [DEBUG=1]`: one-time Z reference setup for the
  mounted tool. Run it after changing a nozzle or a hotend, or after moving
  the coil or the switch. This is the setup step, not the routine
  measurement.

## Z offsets

Set `calibrate_z: True` and mount a contact switch next to the coil, the kind
of endstop a nozzle can press on. Leave `calibrate_z` off and no descent runs
at all, which makes each calibration noticeably faster; XY offsets are still
measured and reported.

Run `EDDY_CALIBRATE_Z T=<n>` once per tool before measuring offsets. It
presses the switch four times, discards the first press as a warm-up, takes
the median of the remaining three, and binds that height to the tool's own
eddy sensor reading over the coil.

The switch's own height does not have to be known or accurate. Every tool
presses the same switch, only the differences between tools matter, and
whatever height the switch sits at cancels out.

References are saved to `EddyToolCalibration/calibration_state.json` next to
your printer config as soon as they are measured. There is nothing to paste
and no `SAVE_CONFIG` to run.

Active gcode offsets do not affect any measurement. The plugin commands and
reads machine coordinates, below the gcode transform, so calibrate in
whatever state the printer is in.

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
