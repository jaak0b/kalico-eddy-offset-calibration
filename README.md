# eddy_tool_calibration

A Kalico and Klipper plugin that measures per-tool XYZ nozzle offsets on a
toolchanger with a bed-mounted LDC1612 eddy-current coil.

[![License: GPL v3](https://img.shields.io/badge/license-GPLv3-blue.svg)](LICENSE)

- **Non-contact.** The coil responds to metal, so plastic on the nozzle is
  invisible to it: a dirty nozzle measures the same as a clean one.
- **Nothing on the toolhead.** One 4-wire I2C board at the edge of the
  bed; offsets print as labeled console rows for your own macro lines.

[![A calibration run on the author's printer](https://img.youtube.com/vi/lr-eFiMrt0E/hqdefault.jpg)](https://www.youtube.com/watch?v=lr-eFiMrt0E)

## How it works

Contact pins want a spotless nozzle; cameras want lighting and mounting
work. An eddy-current coil sidesteps both. XY comes from the symmetry
center of the coil response, not its amplitude, so nozzle material does not
shift the result. Z comes from a frequency-vs-height descent curve,
anchored once per tool by a press on a contact switch next to the coil; the
switch's own height cancels between tools. Each Z reference records its
nozzle setpoint, and every later run holds the tool at it, because a nozzle
reads differently hot and cold.

Abridged example output of a run on the author's machine, in mm:

```
tool: T1
center x: 99.0577
center y: -40.5779
baseline tool: T0
offset x: +0.0224
offset y: -0.2598
samples used: 6916
```

Every readout with a fitted center also prints each stepper's microstep
distance. `DEBUG=1` adds per-pass diagnostics; a failed pass always does.

## Commands

`T=` takes one tool number or a comma separated list with no spaces
(`T=0,1,2`). Leaving `T=` out runs every tool and needs `tool_count`; any
run covering more than one tool needs `toolchange_gcode`.

`EDDY_QUERY`: Print statistics of the sensor frequency over `query_time`
seconds without motion, a wiring sanity check. `EDDY_LOCATE [DEBUG=1]`:
Scan over the configured coil position and store the refined coil center
for the rest of the session.

`EDDY_CALIBRATE_Z [T=<list>] [DEBUG=1]`: Measure the one-time Z reference of
each listed tool: heat to `calibration_temp`, press the contact switch,
measure the descent curve. Requires `calibrate_z: True` and the switch
options. References go to `EddyToolCalibration/calibration_state.json`
next to the printer config; no `SAVE_CONFIG` step. Run it again after
changing a nozzle, a hotend, the coil or switch position,
`calibration_temp`, `frequency` or `reg_drive_current`.

`EDDY_CALIBRATE_OFFSET [T=<list>] [DEBUG=1]`: Measure the listed tools and
print their offsets relative to T0, which is always measured first; a run
leaving T0 out requires a baseline from earlier in the same session. With
`calibrate_z: True` every tool needs its Z reference and is heated to its
setpoint; non-baseline results pass to `apply_offsets_gcode` when set.

`EDDY_REPEATABILITY T=<tool> RUNS=<n> CYCLES=<n> [SKIP_Z=1] [DEBUG=1]`:
Measure one tool repeatedly: each cycle docks and remounts it (without
`tool_count` and `toolchange_gcode` the summary says no docking was
exercised), then takes `RUNS` measurements. Reports per-axis measurement
spread, docking spread and worst deviation, and writes a CSV per study to
`log_dir`. `SKIP_Z=0` (default 1) needs the tool's Z reference.

`LDC_CALIBRATE_DRIVE_CURRENT` is also registered (see `reg_drive_current`).

## Reading the results from a macro

The plugin publishes `printer.eddy_tool_calibration` (keys are tool
numbers as decimal strings):

```jinja
{% set eddy = printer.eddy_tool_calibration %}
{% if eddy.tools['1'] is defined and eddy.tools['1'].offset_x is not none %}
  SET_GCODE_OFFSET X={eddy.tools['1'].offset_x} Y={eddy.tools['1'].offset_y}
{% endif %}
```

| Key | Meaning |
|---|---|
| `calibrate_z`, `tool_count` | the config section's values; `tool_count` `null` when unset |
| `baseline_tool` | tool the offsets are measured against, `null` until a baseline exists |
| `session_id` | rises by one each time a baseline replaces the previous one |
| `anchors` | stored Z references, per tool; survive a restart. Each carries `anchor_height` (mm above the switch trigger plane), `anchor_frequency` (Hz), `setpoint_temperature` (the setpoint later runs heat to), `observed_temperature`, `trigger_z` (machine Z of the trigger plane) and `updated` (UTC) |
| `tools` | this session's measurements; a baseline replacement removes the measurements compared against the old one, so an offset here is never compared against a reference that has moved. Each carries `offset_x/y/z` (mm; all `null` for the baseline itself, `offset_z` `null` when no descent ran, and a `null` is never a zero), `center_x/y` (machine coordinates), `z_crossing` (machine Z where the descent crossed the anchor frequency), `session_id` and `measured_time` (host monotonic clock) |

## Measured so far

Measured with `EDDY_REPEATABILITY` on one machine (Voron, StealthChanger,
Manta M8P, BTT Eddy Coil): one setup's figures, not a specification.

| What | Measured | How |
|---|---|---|
| XY repeatability (standard deviation) | 4.6 um X, 2.5 um Y | six runs on one tool, no toolchange, 150 C |
| XY repeatability across dock and redock | about 5 um | four runs including a full toolchange |
| Contact switch press spread | 0 to 2.5 um | per anchor run of four presses |
| Agreement with the contact method | 14 to 66 um | fresh contact calibration, same day, 150 C |

## Install

Requires Kalico or stock Klipper v0.13.0 or newer, an LDC1612 board
reachable over I2C, and Python 3 with no third-party packages.

```
cd ~
git clone https://github.com/jaak0b/kalico-eddy-offset-calibration
cd kalico-eddy-offset-calibration
sh install.sh
sudo service klipper restart
```

`install.sh` detects the firmware layout of `~/klipper` (pass another path
as an argument) and symlinks the plugin into `klippy/plugins/` on Kalico or
`klippy/extras/` on stock Klipper. Update manager entry for moonraker.conf:

```
[update_manager eddy_tool_calibration]
type: git_repo
path: ~/kalico-eddy-offset-calibration
origin: https://github.com/jaak0b/kalico-eddy-offset-calibration
primary_branch: main
is_system_service: False
```

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
#   accepted from 2000000 to 40000000. The default is 12000000, which is
#   correct for the BTT Eddy family (BTT publishes no figure; 12 MHz is the
#   Klipper driver's assumption, and it held up against the contact method
#   on the author's printer). Requires Kalico from March 2026, or stock
#   Klipper. A wrong value scales every reported frequency. Changing it
#   invalidates every stored Z reference: run EDDY_CALIBRATE_Z again for
#   each tool afterwards.
#reg_drive_current:
#   The LDC1612 DRIVE_CURRENT0 register value, 0 to 31. The driver default
#   of 15 suits the BTT Eddy coil family. For any other coil, including the
#   crab board, determine the right value with
#   LDC_CALIBRATE_DRIVE_CURRENT CHIP=eddy_tool_calibration and store the
#   printed value with SAVE_CONFIG. Changing it invalidates every stored Z
#   reference: run EDDY_CALIBRATE_Z again for each tool afterwards. A run
#   that reads a reference taken at another drive current refuses to
#   measure rather than report an offset the setting has moved. If a scan
#   fails with a sensor amplitude error ("Eddy current sensor error"),
#   raise this value by one and retry; BTT's own remedy for that error is
#   the same step, 15 to 16, and it typically means the coil-to-target
#   distance sat outside roughly 2 to 3 mm.
#coil_x:
#coil_y:
#   Required. Approximate machine X and Y of the coil center. A ruler
#   measurement is good enough: EDDY_LOCATE refines it, and every scan
#   starts from the refined center once it has been located in this
#   session.
#coil_z:
#   Required. Machine Z of the coil top face. This is the only vertical
#   option in this section given in machine coordinates; every other
#   height below is measured upward from this face. A coil_z set below the
#   real face drives the nozzle into the coil by that difference.
#coil_inner_diameter:
#   Required. Bore of the sensing coil, in mm. Must be greater than 0. It
#   sets the default fit_window_radius and scan_length, so a value below
#   the coil's real bore narrows the fit window and the scan pass below
#   the response they should cover.
#scan_height: 1.0
#   Height above the coil top face the XY scan passes run at. Must be
#   above the face and below z_start.
#scan_safe_z: 2.0
#   Extra clearance, in mm, added above the scan height for travel moves.
#   Must be greater than 0.
#z_start: 2.5
#   Height above the coil top face the Z descent starts from. The default
#   is where the sensor's usable range typically ends; raising it risks a
#   non-monotonic descent curve.
#z_stop: 0.5
#   Height above the coil top face the Z descent ends at. Must be above
#   the face, and below z_start.
#z_step: 0.05
#   Descent step size, in mm. Must be greater than 0, and must divide the
#   span from z_start to z_stop into a whole number of steps.
#scan_speed: 4.0
#   Speed, in mm/s, of an XY scan pass. Lower it if a pass returns fewer
#   than samples_min samples.
#scan_length:
#   Length, in mm, of an XY scan pass. The default is 1.5 times
#   coil_inner_diameter, so a pass crosses the whole response with margin
#   on both sides for the fit window: 12.0 mm for the 8 mm BTT Eddy Coil
#   bore, 3.0 mm for the 2 mm Little Crab bore.
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
#   Seconds EDDY_QUERY collects samples for. At the driver's 250 Hz rate
#   (400 Hz on Klipper master), the default gives about 125 samples.
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
#save_history: True
#   Append every completed measurement of a tool to history_T<n>.csv in
#   log_dir: the UTC timestamp, the command, the fitted center, the offsets,
#   the session of the baseline they were measured against, the Z crossing
#   and trigger plane, the nozzle setpoint the run was held at, the nozzle
#   reading observed while it ran, and the sample count. This is
#   the drift log, one line per measurement, and it is what a claim about
#   drift over time rests on. It is separate from save_csv, which dumps the
#   raw samples of each scan pass instead.
#csv_dir: EddyToolCalibration/data
#   Directory the raw scan dumps of save_csv are written to, read against the
#   printer config directory unless it is an absolute path. These are working
#   files, meant to be cleared once they have been looked at. It must be
#   neither the directory holding the calibration state file nor log_dir, so
#   that clearing the dumps cannot take the saved Z references or the logs
#   with them, and either spelling of a directory counts as that directory.
#log_dir: EddyToolCalibration/logs
#   Directory the drift logs and the repeatability study files are written
#   to, read against the printer config directory unless it is an absolute
#   path. These are the durable record, kept apart from the scan dumps so
#   clearing those cannot delete them. It must not be the directory holding
#   the calibration state file.
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
#   above the coil face. The switch itself is a plain normally-open endstop
#   switch; sexbolt and sexball style Z endstops work well, and the author
#   uses a sexbolt. switch_pin may share the pin with an existing
#   [tools_calibrate] section, since both sides mark the pin multi-use.
#   Repeatability of a decent switch is a few microns.
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
#calibration_temp: 150.0
#   Nozzle setpoint, in C, that every calibration measurement is taken at.
#   EDDY_CALIBRATE_Z heats to it and records it in the tool's reference, and
#   every later run of that tool is heated to the recorded setpoint, so
#   changing this option after anchoring is reported as a warning naming both
#   values. It must be above 0 when calibrate_z is True: a reference measured
#   cold could only be compared against a later run measured cold as well. The
#   default is hot enough for the hotend to be in its working state and cool
#   enough to limit oozing.
#calibration_temp_band: 2.0
#   How close to its setpoint, in C, a nozzle has to read before the settle
#   dwell starts. The band applies in both directions, so a tool that has to
#   cool only has to fall inside it. Do not set it tight: a hotend under PID
#   control wanders around its setpoint rather than sitting on it, and the
#   frequency shift across a couple of degrees is small beside the minutes a
#   narrow band spends waiting for an exact reading, most of all when the
#   nozzle has nothing but ambient air to cool it.
#calibration_settle_time: 30.0
#   Seconds to dwell after a tool reaches the band, before measuring. The
#   heater block reaches temperature well before the nozzle tip does, and both
#   commands dwell the same time so both measure the same thermal state.
#tool_extruders:
#   Comma separated heater section names, one per tool, in tool number order.
#   Set it whenever a tool does not use the equally numbered extruder: the
#   default assumes T0 uses extruder, T1 uses extruder1 and so on, and a fleet
#   that maps differently would heat the wrong hotend. Names are resolved at
#   startup, so a name that does not exist is a startup error.
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

A config still carrying the earlier options `z_offset_mode` or `z_ref_t0`
through `z_ref_t15` is refused at startup. Active gcode offsets never
affect a measurement: the plugin works in machine coordinates.

### Toolchange and apply templates

With Contomo's `klipper-toolchanger-hard` fork (provides `SET_TOOL_OFFSET`):

```ini
tool_count: 4
toolchange_gcode:
    T{tool}
apply_offsets_gcode:
    SET_TOOL_OFFSET T={tool} X={offset_x} Y={offset_y} Z={offset_z}
```

On the viesturz original, which has no `SET_TOOL_OFFSET`:

```ini
apply_offsets_gcode:
    SET_TOOL_PARAMETER T={tool} PARAMETER=gcode_x_offset VALUE={offset_x}
    SET_TOOL_PARAMETER T={tool} PARAMETER=gcode_y_offset VALUE={offset_y}
    SET_TOOL_PARAMETER T={tool} PARAMETER=gcode_z_offset VALUE={offset_z}
```

`{offset_z}` is bound only with `calibrate_z: True`.

## Supported hardware

Any board carrying an LDC1612 with its coil facing up that the stock
`ldc1612` driver can drive, over I2C (5V, GND, SCL, SDA). Check the supply
voltage and the pin order against your board's silkscreen before powering
it.

| Board | Verdict |
|---|---|
| BTT Eddy Coil on a mainboard or toolboard I2C | Tested by the author. No MCU on the board, nothing to flash |
| BTT Eddy USB | Expected to work unmodified, untested by the author. Its RP2040 runs standard Klipper firmware as a second MCU |
| BTT Eddy Duo | In USB mode, expected to work like the Eddy USB. Its CAN mode and second coil are undocumented by BTT, so unverified |
| chengxg "Little Crab" dual-coil board | The board this plugin's algorithm comes from; sharper XY response than the BTT coils. The author's boards are still being assembled, so unmeasured here |
| Cartographer, Scanner and similar probes | Not compatible: their LDC1612 sits behind proprietary firmware, so the stock `ldc1612` driver is never involved |

**BTT Eddy Coil**, software I2C on the Manta M8P V2.0's labeled I2C pins
(stock STM32H723 firmware does not compile in their `i2c3` hardware bus):

```ini
[eddy_tool_calibration]
i2c_mcu: mcu
i2c_software_scl_pin: PA8
i2c_software_sda_pin: PC9
i2c_address: 42
reg_drive_current: 15
coil_inner_diameter: 8.0
```

Leave `frequency` and `scan_length` out; `coil_inner_diameter` is an
estimate (BTT publishes no bore figure), measure your coil with calipers.

**BTT Eddy USB**, following BTT's own sample config:

```ini
[mcu eddy]
serial: /dev/serial/by-id/usb-Klipper_rp2040_XXXXXXXXXXXX-if00

[eddy_tool_calibration]
i2c_mcu: eddy
i2c_bus: i2c0f
```

**Little Crab.** Its 24 MHz CLKIN oscillator needs an explicit `frequency`:

```ini
[eddy_tool_calibration]
frequency: 24000000
coil_inner_diameter: 2.0
```

Sources: [upstream repository](https://github.com/chengxg/tool_eddy_calibration),
[EasyEDA project](https://oshwhub.com/cxg01/project_lbabffjk).

## License

GNU GPLv3, see [LICENSE](LICENSE). A derivative work of chengxg's GPLv3
[`tool_eddy_calibration`](https://github.com/chengxg/tool_eddy_calibration),
kept unmodified in `reference/`; its algorithms are ported, not vendored.
