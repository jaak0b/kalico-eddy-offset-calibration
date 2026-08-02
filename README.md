# eddy_tool_calibration

A Kalico plugin that measures per-tool XYZ nozzle offsets on a toolchanger by
scanning each nozzle over a bed-mounted LDC1612 eddy-current coil, fitting the
symmetry center of the response for X and Y and a frequency-vs-height curve for
Z.

[![License: GPL v3](https://img.shields.io/badge/license-GPLv3-blue.svg)](LICENSE)

- **Non-contact.** The coil responds to metal, so plastic on the nozzle is
  expected to be invisible to it. Not yet validated on hardware (see Status and
  limitations).
- **Nothing on the toolhead.** One 4-wire board at the edge of the bed.
- **Plain I2C**, hardware or software bus. The BTT Eddy Coil carries no MCU, so
  there is nothing to flash.
- **Labeled offset rows** on the console, applied by your own macro lines if you
  write them.

[![A calibration run on the author's printer](https://img.youtube.com/vi/lr-eFiMrt0E/hqdefault.jpg)](https://www.youtube.com/watch?v=lr-eFiMrt0E)

A calibration run on the author's printer.

## Why this exists

Contact-pin offset calibration wants a spotless nozzle, so on a toolchanger you
wipe every hotend before every run, and a run that looks fine may have measured
a blob of PLA instead of brass. Camera systems want lighting, mounting and focus
work. An eddy-current coil sidesteps both.

## How it works

- **XY** comes from the symmetry center of the coil response, not its amplitude,
  so nozzle material does not shift the result.
- **Z** comes from a frequency-vs-height descent curve, anchored once per tool by
  a press on a contact switch next to the coil. The switch's own height never has
  to be known, because it cancels between tools.
- **The temperature is held for you.** A nozzle reads differently hot and cold,
  so each reference records the setpoint it was heated to, and an offset run
  holds every tool at its own recorded setpoint before measuring. The setpoint
  is what makes the thermal state reproducible: a hotend under PID control
  wanders around it, so the reading at any one moment is a sample of that
  wander. Each reference also records the reading observed while it was taken,
  as evidence that the tool did reach its setpoint.
- The fitting math is ported from chengxg's `tool_eddy_calibration` and Kalico's
  `probe_eddy_current`.

## Example output

Abridged from a run on the author's machine, with the Z rows and the stepper
microstep rows left out. Values are in mm:

```
tool: T1
center x: 99.0577
center y: -40.5779
baseline tool: T0
offset x: +0.0224
offset y: -0.2598
samples used: 6916
```

For the spread repeated runs produced on the author's machine, see
[Status and limitations](#status-and-limitations).

Every readout that prints a fitted center also prints what one microstep moves
each X and Y stepper, read off the kinematics, so a measured spread can be read
against the machine's own resolution. Each row is that one stepper's microstep
distance. On kinematics where an axis is driven by a combination of steppers,
CoreXY among them, the machine's positional quantum is a combination of the
listed values rather than either one.

`DEBUG=1` adds a block per scan pass: response type, sample counts, extremum
sample, fitted vertex offset, pass angle and peak position. A failed pass always
prints its full diagnostics, with or without `DEBUG=1`.

## Commands

`EDDY_CALIBRATE_Z` and `EDDY_CALIBRATE_OFFSET` take the same tool selection.
`T=` takes one tool number or a comma separated list with no spaces: `T=0`,
`T=0,1,2`. Leaving `T=` out runs every tool of the fleet and needs `tool_count`
and `toolchange_gcode`; a list of more than one tool needs `toolchange_gcode`
as well.

#### EDDY_QUERY

`EDDY_QUERY`: Print the current eddy sensor frequency reading, for a wiring
sanity check. Prints the sample count, mean, minimum, maximum and standard
deviation of the frequency over `query_time` seconds, plus the counts of any
discarded samples. Performs no motion.

#### EDDY_LOCATE

`EDDY_LOCATE [DEBUG=1]`: Scan over the configured coil position and store the
refined coil center for the rest of the session. Prints the measured center
next to the configured `coil_x` and `coil_y`. `DEBUG=1` prints each scan pass's
diagnostic rows.

#### EDDY_CALIBRATE_Z

`EDDY_CALIBRATE_Z [T=<list>] [DEBUG=1]`: Measure the one-time Z reference of
the tools named by `T=`, or of every tool in turn when `T=` is left out. Heats
each tool to `calibration_temp`, presses the contact switch, measures the
tool's descent curve and binds the two together. Requires `calibrate_z: True`
and the switch options. Run it again after changing a nozzle or a hotend, after
moving the coil or the switch, and after changing `calibration_temp`. A later
run refuses a reference whose `frequency` or `reg_drive_current` differs from
the settings in use, so re-run it after either of those as well. References
are written to `EddyToolCalibration/calibration_state.json` next to the printer
config as they are measured; there is no `SAVE_CONFIG` step. `DEBUG=1` prints
each press trigger height and each scan pass's diagnostic rows.

#### EDDY_CALIBRATE_OFFSET

`EDDY_CALIBRATE_OFFSET [T=<list>] [DEBUG=1]`: Measure the tools named by `T=`
over the coil and print their offsets relative to T0, or measure every tool in
turn when `T=` is left out. T0 is the baseline and is measured first whatever
order a list gives it in; a run that leaves T0 out requires that T0 was
measured earlier in the same session, because the baseline is not kept across a
restart. With `calibrate_z: True` every tool involved needs its
`EDDY_CALIBRATE_Z` reference, checked before the first move, and each tool is
then heated to the setpoint that reference was taken at. Each non-baseline
result is passed to `apply_offsets_gcode` when that option is set. A run over
more than one tool ends with a per-tool summary. `DEBUG=1` prints each scan
pass's diagnostic rows.

#### EDDY_REPEATABILITY

`EDDY_REPEATABILITY T=<tool> RUNS=<n> CYCLES=<n> [SKIP_Z=1] [DEBUG=1]`: Measure
one tool repeatedly and report how far the results spread. `T=`, `RUNS=` and
`CYCLES=` are all required. Each cycle mounts another tool and remounts the
measured one, then takes `RUNS` measurements without touching it again. Docking
needs `tool_count` and `toolchange_gcode`; without them the cycles still run,
and the summary says no docking was exercised. `SKIP_Z` defaults to 1 and skips
the Z descent even with `calibrate_z: True`; `SKIP_Z=0` needs `calibrate_z:
True` and the tool's `EDDY_CALIBRATE_Z` reference. The tool is heated only when
`calibrate_z: True` and it has that reference, in which case it is held at the
setpoint the reference was taken at. A progress row announces each measurement
as it runs; a single summary prints once the study finishes, giving the tool,
the run and cycle counts, the Z descent and docking state, then per axis the
measurement spread, the docking spread with its degrees of freedom and the
worst deviation from the mean. Every measurement is written to
`repeatability_T<n>_<index>.csv` in `log_dir`, with a fresh index for every
study.

#### LDC_CALIBRATE_DRIVE_CURRENT

`LDC_CALIBRATE_DRIVE_CURRENT CHIP=eddy_tool_calibration`: Kalico's own drive
current calibration, registered by the LDC1612 driver this plugin loads. Store
the printed value with `SAVE_CONFIG`.

## Reading the results from a macro

The plugin publishes its stored references and this session's measurements as
`printer.eddy_tool_calibration`, so a macro can act on a measurement instead of
reading it off the console. Tool numbers are the keys of the two nested
objects, written as decimal strings.

```jinja
{% set eddy = printer.eddy_tool_calibration %}
{% if eddy.tools['1'] is defined and eddy.tools['1'].offset_x is not none %}
  SET_GCODE_OFFSET X={eddy.tools['1'].offset_x} Y={eddy.tools['1'].offset_y}
{% endif %}
```

Top level:

- `calibrate_z`: whether Z calibration is switched on in the config section.
- `tool_count`: the configured number of tools, `null` when the option is not
  set.
- `baseline_tool`: the tool the published offsets are measured against, `null`
  until a baseline has been measured in this session.
- `session_id`: rises by one each time a baseline measurement replaces the
  previous one. Every published measurement carries the session it belongs to.
- `last_tool`: the tool the plugin mounted last, `null` before it mounted one.
- `anchors`: the stored Z references, one entry per tool that has one. These
  survive a restart.
- `tools`: this session's measurements, one entry per tool measured against
  the baseline currently in place. It is empty until a baseline is measured,
  and a run that replaces or clears the baseline takes the measurements that
  were compared against the old one out of it, so an offset here is never
  compared against a reference that has moved.

Each entry of `anchors`:

- `anchor_height`: the reference height above the switch trigger plane, in mm.
- `anchor_frequency`: the sensor frequency at that height, in Hz.
- `setpoint_temperature`: the nozzle setpoint the reference was measured at,
  and the one every later run of that tool is heated to.
- `observed_temperature`: the nozzle reading at the moment it was measured.
- `trigger_z`: the machine Z of the switch trigger plane it was pressed
  against.
- `updated`: when it was measured, UTC in `YYYY-MM-DDTHH:MM:SSZ`.

An anchor withholds the sensor settings it was taken with, `frequency` and
`reg_drive_current`. An anchor frequency describes a height only under those
settings, and the plugin compares them against the live ones itself and refuses
the reference when they differ, so a macro has no decision to make from them.

Each entry of `tools`:

- `offset_x`, `offset_y`, `offset_z`: the measured offsets against the baseline
  tool, in mm. All three are `null` for the baseline tool itself, whose offsets
  are zero by definition, and `offset_z` alone is `null` when no descent ran,
  which is every run with `calibrate_z: False`. A `null` is never a zero: it
  says the figure was not measured.
- `center_x`, `center_y`: the fitted coil center that measurement found, in
  machine coordinates.
- `z_crossing`: the machine Z at which the descent crossed the tool's anchor
  frequency, `null` when no descent ran.
- `session_id`: the session the measurement belongs to.
- `measured_time`: the host's monotonic clock when the measurement finished.
  It compares against other readings of that same clock, not against a wall
  clock time.

## Status and limitations

Pre-release. It works on the author's printer and validation is still running.
Read this section before wiring anything.

- The dirty-nozzle test has not been run yet. That test is the motivating
  use case of the whole project, and it is not proven. Everything below was
  measured with clean nozzles.
- Kalico only. The plugin is loaded from Kalico's `klippy/plugins/`
  directory, which stock Klipper does not have.
- Per-tool Z needs the contact switch. Without a switch you get X and Y and
  nothing else. Leave `calibrate_z` at its default of `False` and no descent
  runs at all, which makes a run faster.
- Z calibration heats the nozzles and waits. Every listed tool is brought to
  its calibration setpoint before the first measurement, so a run takes minutes
  longer, and longer still when a tool has to cool down into the band around
  it.
- Offsets are not persisted. Each session measures a fresh T0 baseline and
  prints the other tools against it. If you want them applied, write the lines
  in `apply_offsets_gcode`. Only the per-tool Z references are stored on disk.
- No toolchanger integration. The plugin runs the toolchange and apply lines
  you wrote and knows nothing else about your changer.
- Cartesian kinematics for the switch probing. The switch probing reads the
  kinematic Z limits and refuses to run without them.

- No drift figure yet. With `save_history` at its default of `True`, every
  completed measurement is appended to `history_T<n>.csv` in `log_dir`, and that
  log is what a claim about drift over time would be based on. None is claimed
  here until it covers enough time.

### Measured so far

Measured on one machine, one session each (a Voron with StealthChanger, a Manta
M8P and a BTT Eddy Coil), recorded in full in the project's decisions log. These
are one setup's figures, not a specification; a smaller coil, a different
mainboard or a different toolchanger will read differently.

`EDDY_REPEATABILITY` is the command that produces figures like these: it reports
the measurement spread, the docking spread and the worst deviation from the
mean, and writes every measurement to a CSV file.

| What | Measured | How |
|---|---|---|
| XY repeatability (standard deviation) | 4.6 um X, 2.5 um Y | six runs on one tool, no toolchange, 150 C |
| XY repeatability across dock and redock | about 5 um | four runs including a full toolchange |
| Contact switch press spread | 0 to 2.5 um | per anchor run of four presses |
| Agreement with the contact method | 14 to 66 um | fresh contact calibration, same day, 150 C |

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

```
cd ~
git clone https://github.com/jaak0b/kalico-eddy-offset-calibration
cd kalico-eddy-offset-calibration
sh install.sh
sudo service klipper restart
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
#   accepted from 2000000 to 40000000. The default is 12000000, which is the
#   BTT Eddy family's oscillator. Setting this option at all requires Kalico
#   from March 2026 or newer. A wrong value here scales every reported
#   frequency. Changing it invalidates every stored Z reference: run
#   EDDY_CALIBRATE_Z again for each tool afterwards.
#reg_drive_current:
#   The LDC1612 DRIVE_CURRENT0 register value, 0 to 31. The driver default
#   of 15 suits the BTT Eddy coil family. For any other coil, including the
#   crab board, determine the right value with
#   LDC_CALIBRATE_DRIVE_CURRENT CHIP=eddy_tool_calibration and store the
#   printed value with SAVE_CONFIG. Changing it invalidates every stored Z
#   reference: run EDDY_CALIBRATE_Z again for each tool afterwards. A run
#   that reads a reference taken at another drive current refuses to
#   measure rather than report an offset the setting has moved.
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

The plugin refuses at startup to load a config still carrying `z_offset_mode` or
`z_ref_t0` through `z_ref_t15`, options earlier versions had. Both are named in
the startup message with the command that replaces them.

Active gcode offsets do not affect any measurement. The plugin commands and
reads machine coordinates, below the gcode transform, so it can be run in
whatever state the printer is in.

### Toolchange and apply templates

Both templates are your own lines; the plugin renders and runs them and knows
nothing about your toolchanger.

With Contomo's `klipper-toolchanger-hard` fork, which provides `SET_TOOL_OFFSET`:

```ini
tool_count: 4
toolchange_gcode:
    T{tool}
apply_offsets_gcode:
    SET_TOOL_OFFSET T={tool} X={offset_x} Y={offset_y} Z={offset_z}
```

On the viesturz original, which has no `SET_TOOL_OFFSET`, use three lines:

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
```

Leave `frequency` out: the driver's 12 MHz default is this board's CLKIN. The
`coil_inner_diameter` above is an estimate, because BTT publishes no bore
specification; measure your coil with calipers. Leave `scan_length` out too;
its default follows `coil_inner_diameter`.

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
```

`scan_length` is left out here too; its default follows `coil_inner_diameter`.
The author's crab boards are still being assembled, so this variant is
unmeasured here. Sources: the
[upstream repository](https://github.com/chengxg/tool_eddy_calibration) and the
[EasyEDA project](https://oshwhub.com/cxg01/project_lbabffjk).

## Moonraker

Add to moonraker.conf:

```
[update_manager eddy_tool_calibration]
type: git_repo
path: ~/kalico-eddy-offset-calibration
origin: https://github.com/jaak0b/kalico-eddy-offset-calibration
primary_branch: main
is_system_service: False
```

For wiring and mounting, follow your sensor board's and mainboard's own manuals.

## License

GNU GPLv3, see [LICENSE](LICENSE). This plugin is a derivative work of chengxg's
GPLv3 [`tool_eddy_calibration`](https://github.com/chengxg/tool_eddy_calibration),
kept unmodified in `reference/` for provenance. Its algorithms are ported, not
vendored.
