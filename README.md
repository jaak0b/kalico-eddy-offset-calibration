# eddy_tool_calibration

A Kalico plugin that measures per-tool XYZ nozzle offsets on a toolchanger by
scanning each nozzle over a bed-mounted LDC1612 eddy-current coil, fitting the
symmetry center of the response for X and Y and a frequency-vs-height curve for
Z.

[![License: GPL v3](https://img.shields.io/badge/license-GPLv3-blue.svg)](LICENSE)

- Non-contact. The coil responds to metal, so molten plastic on the nozzle is
  expected to be invisible to it; that expectation is not yet validated on
  hardware (see Status and limitations).
- One 4-wire board at the edge of the bed. Nothing on the toolhead, no extra
  mass, no extra cable in the umbilical.
- Plain I2C to a pin you already have, hardware or software bus. The BTT Eddy
  Coil carries no MCU, so there is nothing to flash for that variant.
- Offsets are printed as labeled rows and, if you write the macro lines,
  applied to your toolchanger by your own template.

## Why this exists

Contact-pin offset calibration wants a spotless nozzle. On a toolchanger that
means wiping four hotends before every calibration run, and a run that looks
fine but measured a blob of PLA instead of brass. Camera systems want lighting,
mounting and focus work. An eddy-current coil sidesteps both. The LDC1612 sees
the change in coil inductance a piece of metal causes. Plastic is expected not
to change it, an expectation the dirty-nozzle test still has to confirm (see
Status and limitations).

The scan does not measure how strong the response is, it measures where the
response is symmetric. The scan locates the symmetry center of the response,
not its amplitude. Steel, brass and plated copper nozzles therefore produce
different amplitudes and the same center.

## How it works

**XY.** The plugin drives the nozzle across the coil along two directions
(45 and 135 degrees by default) at 4 mm/s while the LDC1612 streams samples at
250 Hz. Each sample is mapped to a real position by asking Kalico's
`motion_report` for the commanded position at that sample's `print_time`, not by
assuming a constant scan velocity. Each pass gets a Gaussian-weighted quadratic
least-squares fit around the response extremum, which gives the crossing point
to a fraction of a sample. Every direction is scanned forward and reverse and
the pair is averaged, which cancels the constant position bias transport latency
adds along the direction of travel. The per-axis projections then go into a
least-squares reconstruction of the center. The whole measurement runs twice,
the second time re-centered on the first result.

**Z.** A stepwise descent over the coil center records frequency against the
machine Z the kinematics report, in 0.05 mm steps by default. That curve is
anchored once per tool: `EDDY_CALIBRATE_Z` presses a contact switch next to the
coil four times, discards the first press as a warm-up, takes the median of the
remaining three, and binds that height to a frequency on the tool's own curve.
The switch's own height never has to be known. Every tool presses the same
switch, only differences between tools matter, and the switch height cancels
out.

Nothing is fitted to make a particular machine's numbers look right. The
parabolic sub-sample fit, the forward/reverse pair cancellation, the
least-squares center reconstruction and the curve evaluation are ported from
chengxg's `tool_eddy_calibration` and from Kalico's own `probe_eddy_current`,
and every tolerance is a documented config option rather than a buried
constant.

## Example output

The rows one `EDDY_CALIBRATE_OFFSET T=1` run prints with `calibrate_z: False`.
Values are in mm, and the ones below stand in for your machine's:

```
tool: T1
center x: 349.8127
center y: 5.1044
baseline tool: T0
offset x: -0.0431
offset y: +0.1187
samples used: 4820
```

For the spread repeated runs produced on the author's machine, see
[Status and limitations](#status-and-limitations).

`DEBUG=1` adds a block per scan pass: response type, sample count, discarded-
sample counts, extremum sample, fitted vertex offset, pass angle and the
pass's peak position. A failed pass always prints its full diagnostics,
whether or not `DEBUG=1` was given.

## Commands

#### EDDY_QUERY

`EDDY_QUERY`: Print the current eddy sensor frequency reading, for a wiring
sanity check. Prints sample count, mean, minimum, maximum and standard
deviation of the frequency over `query_time` seconds, plus the counts of any
discarded samples. Performs no motion.

#### EDDY_LOCATE

`EDDY_LOCATE [DEBUG=1]`: Run a coarse scan over the configured coil position
followed by a refining scan, and store the resulting coil center for the rest of
the session. Prints the measured center next to the configured `coil_x` and
`coil_y`. `DEBUG=1` prints each scan pass's diagnostic rows.

#### EDDY_CALIBRATE_Z

`EDDY_CALIBRATE_Z [T=<tool>] [DEBUG=1]`: One-time Z reference setup for the tool
named by `T=`, or for every tool in turn when `T=` is left out. Presses the
contact switch, measures the tool's descent curve, and binds the two together.
Requires `calibrate_z: True` and the switch options. Run it after changing a
nozzle or a hotend, or after moving the coil or the switch. References are
written to `EddyToolCalibration/calibration_state.json` next to the printer
config as soon as they are measured; there is no `SAVE_CONFIG` step. A run
without `T=` needs `tool_count` and `toolchange_gcode`. `DEBUG=1` prints each
press trigger height and each scan pass's diagnostic rows.

#### EDDY_CALIBRATE_OFFSET

`EDDY_CALIBRATE_OFFSET [T=<tool>] [DEBUG=1]`: Measure the tool named by `T=`
over the coil and print its offsets relative to T0, or measure every tool in
turn when `T=` is left out. Run `T=0` first: it is the baseline every other tool
is compared against, and it is not persisted across a restart. With
`calibrate_z: True` every tool involved needs its `EDDY_CALIBRATE_Z` reference
first, and the command stops before it moves if one is missing. Each
non-baseline result is passed to `apply_offsets_gcode` when that option is set.
A run without `T=` needs `tool_count` and `toolchange_gcode`, and ends with a
per-tool summary. `DEBUG=1` prints each scan pass's diagnostic rows.

The LDC1612 driver also registers Kalico's own
`LDC_CALIBRATE_DRIVE_CURRENT CHIP=eddy_tool_calibration`.

## Status and limitations

Pre-release. It works on the author's printer and validation is still running.
Read this section before wiring anything.

- The dirty-nozzle test has not been run yet. That test is the motivating
  use case of the whole project, and it is not proven. Everything below was
  measured with clean nozzles.
- Measured numbers, one machine, one session each. On a Voron with
  StealthChanger, a Manta M8P and a BTT Eddy Coil: XY repeatability standard
  deviation 4.6 um in X and 2.5 um in Y over repeated runs on one tool, about
  5 um across a dock and redock, contact switch press spread 0 to 2.5 um. The
  cross-check against the contact method agreed to within 14 to 66 um. The
  session is recorded in full in the project's decisions log. These are one
  setup's figures, not a specification, and a smaller coil, a different
  mainboard or a different toolchanger will read differently.
- Kalico only. The plugin is loaded from Kalico's `klippy/plugins/`
  directory, which stock Klipper does not have.
- Per-tool Z needs the contact switch. Without a switch you get X and Y and
  nothing else. Leave `calibrate_z` at its default of `False` and no descent
  runs at all, which makes a run faster.
- Offsets are not persisted. Each session measures a fresh T0 baseline and
  prints the other tools against it. If you want them applied, write the lines
  in `apply_offsets_gcode`. Only the per-tool Z references are stored on disk.
- No toolchanger integration. The plugin runs the toolchange and apply lines
  you wrote and knows nothing else about your changer.
- Cartesian kinematics for the switch probing. The switch probing reads the
  kinematic Z limits and refuses to run without them.

## Requirements

- Kalico with a `klippy/plugins/` directory. When `frequency:` is omitted (the
  BTT Eddy family, whose CLKIN is the driver's 12 MHz default) any Kalico with
  that directory works.
- Setting `frequency:` to another value, which any other CLKIN clock needs,
  requires Kalico from March 2026 or newer. That is when the option was added to
  the `ldc1612` driver; on an older build it is a startup error.
- An LDC1612 eddy-current board reachable over I2C from an MCU Kalico already
  talks to. See [Supported hardware](#supported-hardware).
- Python 3, no third-party packages. The plugin uses only the standard library.

## Install

Replace `OWNER` below with the real account once the repository is published.
**The URL below is a placeholder and does not resolve yet.**

On the printer host:

```
cd ~
git clone https://github.com/OWNER/eddy_tool_calibration
cd eddy_tool_calibration
./install.sh
```

`install.sh` symlinks `eddy_tool_calibration.py` into your Kalico checkout's
`klippy/plugins/` directory (it tries `~/kalico`, then `~/klipper`, or takes the
directory as an argument, or the `KALICO_DIR` environment variable). Prefer the
symlink: a git pull then updates the installed plugin. Copying the file works
too:

```
mkdir -p ~/kalico/klippy/plugins
cp eddy_tool_calibration.py ~/kalico/klippy/plugins/
```

Add the config section, then restart klippy so it loads the module:

```
FIRMWARE_RESTART
```

Do the first restart with the sensor disconnected. A traceback naming
`eddy_tool_calibration` at that point comes from the config or the file, not
from the wiring.

## Config reference

Every option and its default. Options shown commented out may be left out.

```
[eddy_tool_calibration]
#i2c_address:
#i2c_mcu:
#i2c_bus:
#i2c_software_scl_pin:
#i2c_software_sda_pin:
#i2c_speed:
#   The i2c settings for the LDC1612 chip. See the "common I2C settings"
#   section of Kalico's Config_Reference.md for a description of these
#   parameters. The chip's factory address is 42 decimal (0x2A).
#intb_pin:
#   MCU gpio pin connected to the LDC1612 sensor's INTB pin, if it is
#   broken out. The default is to not use the INTB pin.
#frequency:
#   The external clock frequency (in Hz) fed to the LDC1612 CLKIN pin,
#   accepted from 2000000 to 40000000. The default is 12000000, which is the
#   BTT Eddy family's oscillator. Setting this option at all requires Kalico
#   from March 2026 or newer. A wrong value here scales every reported
#   frequency.
#reg_drive_current: 15
#   The LDC1612 DRIVE_CURRENT0 register value, 0 to 31. Determine it with
#   LDC_CALIBRATE_DRIVE_CURRENT CHIP=eddy_tool_calibration and store the
#   printed value with SAVE_CONFIG.
#coil_x: 350.0
#coil_y: 5.0
#   Approximate machine X and Y of the coil center. A ruler measurement is
#   good enough: EDDY_LOCATE refines it, and every scan starts from the
#   refined center once it has been located in this session.
#coil_z: 0.0
#   Machine Z of the coil top face. This is the only vertical option in
#   this section given in machine coordinates; every other height below is
#   measured upward from this face. A coil_z set below the real face
#   drives the nozzle into the coil by that difference.
#coil_inner_diameter: 2.0
#   Bore of the sensing coil, in mm. Must be greater than 0. It sets the
#   default fit_window_radius, so a value below the coil's real bore
#   narrows the fit window below the response it should cover.
#scan_height: 1.0
#   Height above the coil top face the XY scan passes run at. Must be
#   above the face and below z_start.
#scan_safe_z: 2.0
#   Extra clearance, in mm, added above the scan height for travel moves.
#   Must be greater than 0.
#z_start: 5.0
#   Height above the coil top face the Z descent starts from.
#z_stop: 0.5
#   Height above the coil top face the Z descent ends at. Must be above
#   the face, and below z_start.
#z_step: 0.05
#   Descent step size, in mm. Must be greater than 0, and must divide the
#   span from z_start to z_stop into a whole number of steps.
#scan_speed: 4.0
#   Speed, in mm/s, of an XY scan pass. Lower it if a pass returns fewer
#   than samples_min samples.
#scan_length: 4.0
#   Length, in mm, of an XY scan pass. As a rule of thumb, roughly 1.5 times
#   the coil bore: 4.0 mm for the 2 mm crab board bore, 12.0 mm for the 8 mm
#   BTT Eddy Coil bore. It must comfortably exceed the coil diameter so both
#   edges of the response fall inside the pass.
#locate_scan_length:
#   Length, in mm, of the coarse EDDY_LOCATE pass. The default is three
#   times scan_length, because the coarse pass has to cover the error in
#   the configured coil position rather than the coil itself.
#travel_speed: 100.0
#   Speed, in mm/s, of XY travel moves between passes.
#z_speed: 10.0
#   Speed, in mm/s, of every Z leg except the switch presses (those use
#   switch_probe_speed and switch_probe_lift_speed).
#scan_angles: 45, 135
#   Comma separated scan directions in degrees, where 0 runs along X+ and
#   90 along Y+. Two directions at least 30 degrees apart are needed to
#   reconstruct both axes. A repeated angle is a config error, and so is a
#   pair of opposite angles when pair_scans is enabled.
#pair_scans: True
#   Scan the opposite of every configured angle as well and average each
#   pair, which cancels the position bias transport latency adds along the
#   direction of travel. Doubles the number of passes.
#samples_min: 100
#   Minimum usable samples per scan pass, at least 3. A pass below it is
#   an error rather than a fit on thin data.
#query_time: 0.5
#   Seconds EDDY_QUERY collects samples for. At the sensor's 250 Hz rate,
#   the default gives about 125 samples.
#freq_min: 1000000.0
#   Samples below this frequency, in Hz, are discarded as startup or noise
#   readings. The default sits well below any real LDC1612 coil resonance.
#edge_margin: 0.15
#   Fraction of each pass treated as its edge, above 0 and below 0.5. The
#   edges are excluded from the extremum search and used for peak-type
#   detection.
#fit_window_radius:
#   Half width, in mm, of the quadratic fit window either side of the
#   response extremum. The default is half of coil_inner_diameter.
#fit_sigma_fraction: 0.5
#   Standard deviation of the fit's Gaussian weighting, as a fraction of
#   the fit window.
#fit_vertex_limit: 0.5
#   A fitted vertex further from the extremum sample than this fraction of
#   the fit window is reported as a failed fit rather than clamped.
#save_csv: False
#   Write every scan pass's raw samples to a CSV file for offline review.
#csv_dir: EddyToolCalibration/data
#   Directory the scan CSV files are written to, relative to the printer
#   config directory. It must not be the directory holding the calibration
#   state file, so that clearing the dumps cannot take the saved Z
#   references with it.
#calibrate_z: False
#   Run the Z descent and report Z offsets. With it False no descent runs,
#   XY offsets are still measured, and none of the switch options below
#   are needed.
#switch_pin:
#switch_x:
#switch_y:
#switch_probe_z_start:
#   The contact switch EDDY_CALIBRATE_Z presses, required when
#   calibrate_z is True. switch_pin is an endstop pin and accepts the
#   usual inverting and pullup prefixes. switch_x and switch_y are the
#   machine X and Y the nozzle presses the switch at, and
#   switch_probe_z_start is the machine Z each press starts from: set it
#   just above the switch. All three are machine coordinates, not heights
#   above the coil face.
#switch_probe_speed: 5.0
#   Speed, in mm/s, of a downward press onto the switch.
#switch_probe_lift_speed:
#   Speed, in mm/s, of the retract between presses. The default is
#   switch_probe_speed.
#switch_probe_max_travel: 4.0
#   How far, in mm, a press may travel down before the run is reported as
#   a missing trigger.
#switch_probe_sample_retract_dist: 2.0
#   How far, in mm, the nozzle retracts after each press. Must be less
#   than switch_probe_max_travel, because each press starts from where the
#   previous one retracted to.
#switch_probe_tolerance: 0.020
#   How far, in mm, the three counted presses may disagree before the
#   probing is reported as failed. A switch that cannot repeat inside its
#   tolerance has a mechanical cause another press does not fix.
#tool_count:
#   How many tools the machine has, 1 to 16. Tools are T0 through
#   T(tool_count-1) with no gaps. Needed only to run a command without T=.
#toolchange_gcode:
#   The lines that mount a tool, as a template with {tool} bound to the
#   tool number. Needed only to run a command without T=. When it is set,
#   both calibration commands mount the tool they are about to work on,
#   with T= as well as without it. When it is not, they work on whatever
#   tool is already mounted.
#apply_offsets_gcode:
#   The lines that apply a measured offset, as a template with {tool},
#   {offset_x} and {offset_y} bound, and {offset_z} bound as well when
#   calibrate_z is True. Every non-baseline result is passed through it as
#   the run proceeds. Leave it out and the plugin only reports.
```

The plugin refuses at startup to load a config still carrying `z_offset_mode` or
`z_ref_t0` through `z_ref_t15`, options earlier versions had. Both are named in
the startup message with the command that replaces them.

Active gcode offsets do not affect any measurement. The plugin commands and
reads machine coordinates, below the gcode transform, so it can be run in
whatever state the printer is in.

### Toolchange and apply templates

Both templates are your own lines; the plugin renders and runs them and knows
nothing about your toolchanger.

```ini
tool_count: 4
toolchange_gcode:
    T{tool}
apply_offsets_gcode:
    SET_TOOL_OFFSET T={tool} X={offset_x} Y={offset_y} Z={offset_z}
```

`SET_TOOL_OFFSET` above is Contomo's `klipper-toolchanger-hard` fork. On the
viesturz original, which has no such command, use three lines:

```ini
apply_offsets_gcode:
    SET_TOOL_PARAMETER T={tool} PARAMETER=gcode_x_offset VALUE={offset_x}
    SET_TOOL_PARAMETER T={tool} PARAMETER=gcode_y_offset VALUE={offset_y}
    SET_TOOL_PARAMETER T={tool} PARAMETER=gcode_z_offset VALUE={offset_z}
```

Use `{offset_z}` only with `calibrate_z: True`. With it `False` no descent runs,
there is no measured Z, and the name is not bound in the template.

## Supported hardware

Any board carrying an LDC1612 with its coil facing up, reachable over I2C. Four
wires: 5V, GND, SCL, SDA. Check the supply voltage and the connector pin order
against your own board's silkscreen before powering it.

**BTT Eddy Coil, wired to a mainboard or toolboard I2C. Tested by the author.**
No MCU on the board, so nothing to flash. Software I2C on the Manta M8P V2.0's
labeled I2C connector pins, because stock STM32H723 firmware does not compile in
the `i2c3` hardware bus those pins belong to:

```ini
[eddy_tool_calibration]
i2c_mcu: mcu
i2c_software_scl_pin: PA8
i2c_software_sda_pin: PC9
i2c_address: 42
reg_drive_current: 15
coil_inner_diameter: 8.0
scan_length: 12.0
```

Leave `frequency` out: the driver's 12 MHz default is this board's CLKIN. The
`coil_inner_diameter` above is an estimate, because BTT publishes no bore
specification; measure your coil with calipers.

**BTT Eddy USB.** The board's RP2040 is its own MCU, so it is declared as one
and the sensor sits on its internal I2C bus, following BTT's own sample config.
Kalico has to be flashed to the RP2040 first. Not tested by the author:

```ini
[mcu eddy]
serial: /dev/serial/by-id/usb-Klipper_rp2040_XXXXXXXXXXXX-if00

[eddy_tool_calibration]
i2c_mcu: eddy
i2c_bus: i2c0f
```

**BTT Eddy Duo.** Expected to work wherever it presents an MCU with the LDC1612
on I2C, configured like one of the two above. Not verified by the author.

**chengxg "Little Crab" dual-coil board.** The board this plugin's algorithm
comes from: a small wound pancake coil, which gives a sharper XY response than
the larger BTT coils. Its CLKIN oscillator is 24 MHz, so it needs an explicit
`frequency` and therefore Kalico from March 2026 or newer:

```ini
[eddy_tool_calibration]
frequency: 24000000
coil_inner_diameter: 2.0
scan_length: 4.0
```

The author's crab boards are still being assembled, so this variant is
unmeasured here. Sources: the
[upstream repository](https://github.com/chengxg/tool_eddy_calibration) and the
[EasyEDA project](https://oshwhub.com/cxg01/project_lbabffjk).

## Moonraker

Available once the repository is public. Replace `OWNER` with the real account:

```
[update_manager eddy_tool_calibration]
type: git_repo
path: ~/eddy_tool_calibration
origin: https://github.com/OWNER/eddy_tool_calibration
primary_branch: main
is_system_service: False
```

## Documentation

- `docs/design.md`: the full design, config schema rationale and validation
  ladder.
- `docs/bringup.md`: step-by-step first bring-up on a BTT Eddy Coil, including
  the wiring cross-check against BTT's published documentation.
- `docs/z-probe-design.md`: the Z reference, the switch anchor math and the
  state file schema.

## License

GNU GPLv3, see [LICENSE](LICENSE). This plugin is a derivative work of chengxg's
GPLv3 [`tool_eddy_calibration`](https://github.com/chengxg/tool_eddy_calibration),
kept unmodified in `reference/` for provenance. Its algorithms are ported, not
vendored.
