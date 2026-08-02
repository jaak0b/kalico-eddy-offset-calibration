# EddyNozzleProbe plugin design

Status: draft for owner review. Target: Kalico plugin (`klippy/plugins/`), GPLv3.

## Scope

One plugin, one config section, calibrating per-tool XYZ nozzle offsets against a
bed-mounted LDC1612 eddy sensor board. Offsets are reported to the console only;
the plugin persists its own Z references, and there is no toolchanger
integration in v1.

Out of scope for v1: channel-2 (big coil) support, pressure-advance experiments,
Z homing/bed meshing (Kalico's probe_eddy_current already covers that use case),
automatic offset application.

## Module layout

```
eddy_nozzle_probe/
  eddy_tool_calibration.py    # the plugin (single module)
  reference/
    tool_eddy_calibration.py  # upstream file, unmodified, for algorithm reference
  install.sh                  # symlink into ~/klipper/klippy/plugins/
  integration_test.py         # runs the cases below against a Kalico checkout
  integration/                # config and gcode of the Kalico integration test
  docs/ tests/
```

Install: symlink `eddy_tool_calibration.py` into `klippy/plugins/`. Moonraker
update_manager entry documented in README.

## Firmware compatibility

The plugin has to run on more than one firmware build, and a handful of the
things it reaches for are spelled differently from one build to the next. The
firmware table is the one place those differences are written down: one row per
surface that differs, and each row names every way the plugin knows how to
reach that surface. What it buys is a single answer to "which parts of this
plugin depend on the firmware", and a build the plugin cannot use is refused at
startup by name instead of failing halfway through a command.

Every row is a closed set of named strategies plus an explicit unsupported
outcome. Rows resolve by inspecting what the object in front of them carries,
never by the firmware's name or its version string: the differences do not line
up with either. The firmware's name appears in messages only, never in a
decision.

| Row | Surface it covers | Strategies | What is inspected |
|---|---|---|---|
| sensor driver import | the Python name the `ldc1612` driver module imports under | `klippy_package`, `extras_package` | `klippy.extras.ldc1612` is tried first and `extras.ldc1612` second; the first that imports wins |
| motion queue | the dictionary of motion queues `motion_report` publishes, read once per scan | `trapqs`, `dtrapqs` | which of the two attribute names the `motion_report` object carries |
| preheat wait | the interruptible wait a nozzle preheat polls on | `printer_wait_while`, `reactor_poll` | whether `printer.wait_while` is callable |
| sensor clock | the LDC1612 reference clock in hertz, stored in every anchor as its fingerprint | `driver_clock_freq`, `driver_frequency`, `module_clock` | whether the driver carries `clock_freq`; failing that, whether it carries `frequency`; failing that, whether the `ldc1612` module carries `LDC1612_FREQ` |

Both preheat strategies also need `heaters._get_temp`, the M105 line the wait
emits on each poll, so a build without it fails that row whichever wait it has.
What the four reference builds resolve to, read from their sources:

| Row | Kalico development | Kalico `3b98cf51` (2025-12-14) | Klipper `v0.13.0` | Klipper master |
|---|---|---|---|---|
| sensor driver import | `klippy_package` (`klippy/__init__.py` present) | `klippy_package` (present) | `extras_package` (no `klippy/__init__.py`; `klippy.py:103` imports `extras.` plus the name) | `extras_package` (`klippy.py:103`) |
| motion queue | `trapqs` (`motion_report.py:210`) | `trapqs` (`:210`) | `trapqs` (`:137`) | `dtrapqs` (`:149`) |
| preheat wait | `printer_wait_while` (`printer.py:504`) | `printer_wait_while` (`printer.py:624`) | `reactor_poll` (no `wait_while` in `klippy.py`) | `reactor_poll` (none) |
| sensor clock | `driver_clock_freq` (`ldc1612.py:103`) | `module_clock` (`ldc1612.py:16`) | `driver_frequency` (`ldc1612.py:90`) | `driver_clock_freq` (`ldc1612.py:91`) |

The sensor clock row stamps the clock rather than the count-to-hertz
conversion, because the clock moves only when the hardware moves: a driver may
split the same physical scale differently between the clock and its sensor
divider from one build to the next (Kalico `3b98cf51` pairs 12 MHz with
divider 1, `ldc1612.py:223`; Kalico development pairs 12 MHz with divider 2,
`ldc1612.py:108`), and a fingerprint that folds the divider in would refuse
anchors whose frequencies are still physically valid after a mere firmware
update.
`reactor_poll` is stock Klipper's own heater wait loop (`heaters.py:348-352` in
`v0.13.0`, `:356-359` on master), polling `reactor.pause(eventtime + 1.)` while
the printer is not shut down and emitting the M105 line on each pass, with one
deviation: a wait that ends on a shutdown raises rather than returning, because
the caller measures as soon as it returns.

**A row that resolves to nothing is a startup error**, never a strategy that
absorbs whatever is left. The message names the row, every strategy the row
knows and what this build was found to carry, as labeled rows:

```
eddy_tool_calibration: the sensor clock cannot be resolved on this
firmware build.
strategies this plugin knows: driver_clock_freq, driver_frequency, module_clock
ldc1612 driver carries clock_freq: no
ldc1612 driver carries frequency: no
ldc1612 module carries LDC1612_FREQ: no
Report this with the firmware name and version, or install a firmware version
the requirements name.
```

**Where resolution happens.** Resolving at startup rather than on first use is
the whole point, because an unexpected exception inside a gcode handler shuts
the printer down instead of printing a message. The sensor driver import
resolves in the plugin's constructor, since the driver module is what
`LDC1612(config)` is built from; its failure is a config error at load. The
other three resolve at `klippy:connect`, in `_handle_connect`, whose call to
`_require_preheat_surfaces` becomes the preheat wait row and is not joined by a
second check: the method keeps exactly one resolution step, ahead of the
per-tool heater lookups it already does. It returns early with `calibrate_z`
False, and the preheat wait and sensor clock rows keep that gate, because
a machine measuring XY only heats nothing and stores no anchor. The motion
queue row resolves whatever `calibrate_z` says, because every scan reads it,
and a printer with no steppers carries no `motion_report` object and leaves it
unresolved, where a scan still reports `_get_trapq`'s existing error naming the
missing steppers. Every resolved strategy is logged at connect, so a support
question can be answered from `klippy.log` alone.

**What each row replaces.** The import row replaces the `from klippy.extras
import ldc1612` statement at `:2000`. The motion queue row replaces the literal
`motion_report.trapqs['toolhead']` at `:2146`; `_get_trapq` itself stays,
including its error for a printer with no motion queue. The preheat wait row
replaces `self.printer.wait_while(waiting)` at `:2498` and deletes
`_require_preheat_surfaces` (`:2361-2386`), its call at `:2355`, and the
constants `PREHEAT_PRINTER_METHODS` and `PREHEAT_HEATERS_METHODS`
(`:1783-1790`). The sensor clock row replaces the two `_sensor_freq_conv`
calls and deletes `_sensor_freq_conv` together with `DEFAULT_SENSOR_CLOCK`;
the two call sites read the clock resolved at connect instead.

**The sensor fingerprint is corrected in the process.** `_sensor_freq_conv`
falls back to `DEFAULT_SENSOR_CLOCK * 2 / 2^28`, that is 24000000 / 2^28, or
0.0894069671630859375 Hz per count. The real conversion of a build without
`freq_conv` is 12000000 / 2^28, or 0.0447034835815429688 Hz per count
(`Kalico 3b98cf51 ldc1612.py:194`; `Klipper v0.13.0 ldc1612.py:158` with the
default clock), exactly half, so on those builds the anchor readout prints
twice the driver's conversion and the doubled number goes into every stored
anchor. It is not wrong everywhere: Kalico development and Klipper master both
compute it for a 12 MHz clock, each pairing that clock with a sensor divider of
2 (`ldc1612.py:108-109`, `:95-96`), which is why the comparison has never fired
falsely. No measured offset moves, because the number is only ever compared
against itself and the reported frequencies do not change. The correction is
the fingerprint change itself: the anchor stores the resolved clock in hertz
instead of any conversion, so there is no fallback constant left to freeze and
nothing that doubles.

An anchor stored under the old `freq_conv` field is refused when the state
file is read, with a message naming the earlier plugin version and
`EDDY_CALIBRATE_Z T=<n>`, worded apart from the hardware-change refusal:
every read of a current anchor goes through `_anchor`, which calls
`anchor_sensor_mismatch` and raises its refusal naming the stored and the
live clock in hertz and `EDDY_CALIBRATE_Z T=<n>`. There is no migration and
none is wanted, for the reason the state file section already gives.

**Selection is unit-testable without a firmware.** Each row's selection is a
pure function of what an object exposes, so each gets a module-level function
taking plain objects and importing nothing from klippy: the motion queue row
takes the `motion_report` object, the preheat wait row the printer and heaters
objects, the sensor clock row the driver object and the driver module, and
the import row the callable that imports a module by name. Each returns a
strategy name or raises the unsupported text. Tests build stand-ins carrying
exactly the attribute set one reference build carries, one per build per row,
and assert the resolution the table above claims. Expected values are literals
read out of the firmware source at the file and line the table cites, never
obtained by calling the plugin: the clock tests assert the literal 12000000
every reference build defaults to, and a configured-clock test asserts the
crab board's 24000000. One further test asserts that a stand-in
matching no strategy raises, and that the text names the row, every strategy
and each attribute that was looked for.

## Config schema (single section)

```ini
[eddy_tool_calibration]
# --- sensor (embedded ldc1612, no separate section needed) ---
i2c_mcu: mcu                    # or a toolboard MCU name
i2c_software_scl_pin: PB6       # hardware i2c_bus also supported
i2c_software_sda_pin: PB7
i2c_address: 42                 # LDC1612 0x2A default
frequency: 24000000             # CLKIN of the crab board oscillator
reg_drive_current: 22           # from LDC_CALIBRATE_DRIVE_CURRENT
# --- geometry ---
coil_x: 350.0                   # approximate coil center (refined by EDDY_LOCATE)
coil_y: 5.0
coil_z: 0.0                     # machine Z of the coil top face
coil_inner_diameter: 2.0        # mm, coil bore; sets the default fit window and scan length
scan_height: 1.0                # mm above the coil top face during XY scans
scan_safe_z: 2.0                # mm above the scan height for travel moves
z_start: 2.5                    # descent start, mm above the coil top face
z_stop: 0.5                     # descent end, mm above the coil top face
z_step: 0.05                    # descent step; must divide z_start - z_stop
# --- scan tuning ---
scan_speed: 4.0                 # mm/s
scan_length: 3.0                # mm, default = 1.5 * coil bore
locate_scan_length: 9.0         # mm, EDDY_LOCATE coarse pass; 3 * scan_length
travel_speed: 100.0             # mm/s for XY travel between passes
z_speed: 10.0                   # mm/s for every Z leg
scan_angles: 45, 135            # degrees; two directions reconstruct X and Y
pair_scans: True                # forward+reverse averaging (latency cancellation)
samples_min: 100                # abort fit below this sample count per pass
query_time: 0.5                 # seconds EDDY_QUERY samples for
save_csv: False                 # dump raw scan data for analysis
save_history: True              # append every measurement to the per-tool drift log
csv_dir: EddyToolCalibration/data # folder, relative to the config dir, for scan dumps
log_dir: EddyToolCalibration/logs # folder for the drift logs and the study files
# --- fit tuning ---
fit_window_radius: 1.0          # mm each side of the extremum; coil_inner_diameter / 2
fit_sigma_fraction: 0.5         # Gaussian weight sigma as a fraction of the window
fit_vertex_limit: 0.5           # reject a vertex past this fraction of the window
edge_margin: 0.15               # fraction of each pass treated as edge
freq_min: 1000000.0             # Hz; samples below this are discarded as noise
# --- Z offsets ---
calibrate_z: False              # run the Z descent and report Z offsets
# --- nozzle temperature ---
calibration_temp: 150.0         # C setpoint every calibration measurement is taken at
calibration_temp_band: 2.0      # C either side of the setpoint that ends the wait
calibration_settle_time: 30.0   # s dwell after the band is reached
tool_extruders:                 # heater section names by tool number; default
                                # is extruder, extruder1, extruder2, ...
# --- contact switch: these four are required when calibrate_z is True ---
switch_pin: ^PA1                # endstop pin, invert and pullup prefixes allowed
switch_x: 340.0                 # machine X of the nozzle over the switch
switch_y: 5.0                   # machine Y of the nozzle over the switch
switch_probe_z_start: 3.0       # machine Z the probing move starts from
# --- switch probing tuning, all optional, defaults shown ---
switch_probe_speed: 5.0         # mm/s
switch_probe_lift_speed: 5.0    # default: switch_probe_speed
switch_probe_max_travel: 4.0    # mm below the press start height
switch_probe_sample_retract_dist: 2.0  # mm, must be below max_travel
switch_probe_tolerance: 0.020   # mm, spread across the counted presses
# --- fleet runs: required only for a command without T= ---
tool_count: 4                   # 1 to 16; tools are T0 .. T(tool_count-1), no holes
toolchange_gcode:               # the lines that mount a tool; {tool} is the number
    T{tool}
apply_offsets_gcode:            # optional; {tool}, {offset_x}, {offset_y}, {offset_z}
    SET_TOOL_OFFSET T={tool} X={offset_x} Y={offset_y} Z={offset_z}
```

`tool_count`, `toolchange_gcode` and `apply_offsets_gcode` are the fleet
options. `tool_count` above the 16 tools `T=` accepts is a config error. The two
gcode options are klippy templates, loaded through the standard `gcode_macro`
machinery, and the plugin only renders and runs them: it learns nothing about
the toolchanger. `{offset_z}` is available to `apply_offsets_gcode` only with
`calibrate_z: True`; with it False no descent ran, so the name is left out of
the template context rather than passed as a zero the machine would apply.

`switch_pin`, `switch_x`, `switch_y` and `switch_probe_z_start` are the four
options `EDDY_CALIBRATE_Z` cannot run without; it names whichever is missing.
The five tuning options all have defaults.

Each press starts from the height the previous press retracted to, so
`switch_probe_sample_retract_dist` must be smaller than
`switch_probe_max_travel`. A config where it is not is refused at load, because
otherwise the second press and every press after it would end short of the
switch and report a missing trigger instead of the real cause.

Per-tool Z references are not config options. They live in
`EddyToolCalibration/calibration_state.json` next to the printer config,
written by `EDDY_CALIBRATE_Z` as soon as a reference is measured.

The state file holds one JSON object per anchored tool under an `"anchors"`
key, keyed by decimal tool number as a string, alongside a top-level
`"version"` field (currently 1). Each entry stores `anchor_height`,
`anchor_frequency`, `setpoint_temperature`, `sensor_clock` and `drive_current`,
the five fields an offset run reads, plus diagnostic record fields that are
never fed back into a
measurement: `observed_temperature`, the reading the heater showed while the
anchor was taken, and `trigger_z`, `curve_low_z`, `curve_high_z`, `center_x`,
`center_y` and `updated`, which let a stale anchor be recognised later. The two
temperature fields are deliberately separate. `setpoint_temperature` is the
`calibration_temp` the anchor run heated to and is the only one that controls
anything; `observed_temperature` is a sample of the wander a hotend shows
around its setpoint, kept because it tells the owner whether the tool actually
reached that setpoint. Writes serialise to a
temporary file and `os.replace` it over the target, so an interrupted write
cannot leave a truncated state file; a fleet run rewrites the file after each
tool, so an abort partway through keeps the anchors already measured. A
missing file is normal and means no tool is anchored yet; a file that exists
but does not parse, carries a `version` this build does not handle, or is
missing a field this build needs, is a config error naming the path and the
command that rewrites the references from scratch. There is no migration
path: every field an anchor record carries comes out of one measurement, so a
record missing one cannot be completed without measuring again.

`sensor_clock` and `drive_current` are the sensor settings the anchor
frequency was measured with: the LDC1612 reference clock in hertz, resolved
per the firmware table's sensor clock row, and the LDC1612 drive current
register value. A coil's frequency at a given height depends on both, so an
anchor frequency describes a height only for the settings it was taken under.
Every read of a stored anchor compares the two
against what the firmware reports now and refuses the anchor on any difference,
naming the tool, both stored and current values, and `EDDY_CALIBRATE_Z T=<n>`.
`EDDY_CALIBRATE_OFFSET` and `EDDY_REPEATABILITY` make that comparison before
they move. Refusing rather than warning is deliberate: the alternative is a Z
offset wrong by an unknown amount.

The comparison is exact and carries no tolerance. Both are settings rather
than measurements, the drive current an integer register value and
`sensor_clock` an integer number of hertz, so unchanged settings give back an
identical number and any difference at all is a real change.

What this catches: an `LDC_CALIBRATE_DRIVE_CURRENT` run followed by
`SAVE_CONFIG`, which writes `reg_drive_current` straight back into this
plugin's own config section; a hand-edited `frequency`; and a move to a board
whose reference clock differs. What it cannot catch: a change that
leaves the scale alone. The 12 MHz driver default is paired with a divider of
2 and so yields the same conversion as a 24 MHz clock, and swapping to a
different but identically configured coil changes no number either side of the
comparison. Both stay covered by the standing advice to re-anchor after
changing hardware.

`coil_z` is the only vertical option in machine coordinates. `scan_height`,
`z_start` and `z_stop` are heights above the coil top face, so the plugin adds
`coil_z` to each of them and never commands a Z below the face. A geometry that
breaks that (a `z_stop` at or below the face, or a `scan_height` at or below
the face or at or above `z_start`) is a config error at load, never clamped.
Measured Z is the other direction: the descent curve stores the machine Z the
kinematics report, so the curve range, the switch trigger plane and the
reported crossing are all machine Z.

Active gcode offsets do not affect any measurement. The plugin commands and
reads machine coordinates, below the gcode transform, so calibration is valid
in whatever state the printer is in.

## Z offsets

`calibrate_z` decides whether a Z descent runs at all:

- **False** (the default): no descent runs. `EDDY_CALIBRATE_OFFSET` measures
  XY only and the readout carries no Z rows. The descent is the slowest part
  of a calibration, so skipping it saves that time outright.
- **True**: each tool carries its own Z reference, measured once by
  `EDDY_CALIBRATE_Z` against a contact switch mounted near the coil. The
  reference is the height of the tool's own curve midpoint above the switch
  trigger plane, plus the frequency there, so tools with different hotends or
  nozzle materials each get their own frequency reference and the comparison
  stays material-independent. The switch's own height cancels out of every
  offset. `EDDY_CALIBRATE_OFFSET` refuses to run, before any motion, for a
  tool that has no stored reference.

The switch is any rigidly mounted normally-open endstop switch the nozzle can
press straight down, a sexbolt or sexball style Z endstop among them, placed
near the coil and within reach of every tool.

The switch trigger point is one fixed physical plane, so the difference
between two tools' trigger heights is exactly their Z nozzle offset,
whatever height the switch happens to sit at: the switch's own height is a
constant that cancels in every difference. `EDDY_CALIBRATE_Z` anchors each
tool at the midpoint of its own measured descent range, the point furthest
from both ends and so the one that leaves the widest margin for a later
descent to still bracket the frequency. What is stored is not that machine Z
but its height above the trigger plane and the frequency measured there, a
switch-relative pair. A later session measures the tool's curve again, finds
the height at which it reaches the stored frequency, subtracts the stored
height to reconstruct the trigger plane, and differences that against the
baseline tool's reconstructed trigger plane to report the Z offset. The coil
face height never enters an offset; it only has to be small enough that the
switch probing and the descent are both reachable.

Display precision in every readout: millimetres to 4 decimals, frequencies to
3 decimals, temperatures to 1 decimal. Microstep distances are the exception, at
6 decimals, because a microstep is a few microns and 4 decimals would round most
machines' resolution away. Values are printed exactly as measured at that
precision, never rounded further and never clamped.

## Machine resolution

Every readout that prints a fitted center also prints the microstep distance of
each X and Y stepper, so a reader can judge a measured spread against the
machine's own resolution. The figure comes from the kinematics
(`toolhead.get_kinematics().get_steppers()` and each stepper's
`get_step_dist()`), never worked out from `rotation_distance`,
`full_steps_per_rotation` and `microsteps` here, so it is the distance the
machine itself steps by. Each row is one stepper's own microstep distance and
is labeled as such: on kinematics where an axis is driven by a combination of
steppers, CoreXY among them, the machine's positional quantum is a combination
of the listed values rather than either one, and no kinematics transform is
attempted here. Steppers that cannot be read produce no rows at all rather than
a guess, never fail the measurement they accompany, and log their reason.

## Measurement logs

Two CSV layouts, both written into `log_dir`. That directory is deliberately
not `csv_dir`: the scan dumps there are working files, cleared once they have
been looked at, and clearing them must not take the durable record with them.
Both are refused at load if they name the state file's own directory, and
`log_dir` is refused if it names `csv_dir`.

- `history_T<n>.csv`, one line per completed measurement of a tool, appended by
  `EDDY_CALIBRATE_OFFSET`, `EDDY_CALIBRATE_Z` and `EDDY_REPEATABILITY` alike.
  Columns: `timestamp`, `command`, `center_x`, `center_y`, `offset_x`,
  `offset_y`, `z_crossing`, `trigger_z`, `offset_z`, `baseline_session`,
  `setpoint_temperature`, `observed_temperature`, `samples_used`. The two
  temperature columns are the setpoint the run was held at, empty for a run
  that heated nothing, and the reading taken while it measured, so a row
  carries both the thermal state that was asked for and the one the tool was
  in. This is the drift log, the record a claim
  about drift over time rests on. `timestamp` is UTC in
  `YYYY-MM-DDTHH:MM:SSZ`, so a daylight saving rollback cannot reorder the
  file. `baseline_session` names the session whose baseline measurement the
  offsets on that line were taken against, so a baseline that moved between two
  sessions is visible as such rather than read as a drift of the tool. An
  anchor run leaves `z_crossing` empty, because its descent defines the anchor
  rather than being evaluated against one; what it contributes is the trigger
  plane it pressed. A measurement of the baseline tool leaves the offset
  columns empty, because those offsets are zero by definition. A value that was
  not measured is an empty field, never a zero. The log is governed by
  `save_history` and not by `save_csv`: the latter dumps the raw samples of
  each scan pass into `csv_dir`, a different concept.
- `repeatability_T<n>_<index>.csv`, one file per study, holding the same
  columns with `cycle` and `run` in front. The index is zero padded to three
  digits and one above the highest already in the directory, so a study never
  overwrites the data of one that ran before it.

A log file that does not exist yet and one that exists but is empty are both
written their header first, so an interrupted create cannot leave rows under no
columns. A file whose first line names other columns is a gcode error telling
the owner to move it aside, never appended to.

The drift log is written last in every command, after the measurement is
reported, the anchor is stored or the offsets are applied, so a log that cannot
be written never costs work that succeeded; its error names what is already in
place and says that only the log write failed. The deliberate cost of that
order is that a failed apply leaves no log row: a row would claim an offset the
machine never received.

## Nozzle temperature

How the frequency reads against height depends on how hot the nozzle is, so a
Z reference is only valid for a measurement taken in the thermal state the
reference was measured in. The reproducible name for that state is the heater
setpoint, not a reading: a hotend under PID control wanders around its
setpoint, so a reading taken at any one moment is a sample of that wander and
aiming a later run at it would chase a value the controller never targets. The
plugin holds the setpoint itself rather than trusting the machine to be in the
right state:

- `EDDY_CALIBRATE_Z` heats to `calibration_temp`, waits for the band, dwells
  `calibration_settle_time`, measures, and records that setpoint into the
  anchor as `setpoint_temperature`. The reading at that moment goes into
  `observed_temperature` beside it, printed as its own row and written to the
  drift log, because a tool that never reached its setpoint is visible there
  and nowhere else.
- `EDDY_CALIBRATE_OFFSET` reads each tool's recorded setpoint out of the state
  file and heats that tool back to it, so an offset run reproduces the setpoint
  its reference was taken at by construction. Its readout prints the anchor's
  setpoint, the anchor's observed reading and the reading of this run, so a
  descent taken in the wrong thermal state is visible in the rows rather than
  only in the offset it moved.
- A tool whose recorded setpoint differs from `calibration_temp` at all is
  reported as a console warning naming both values. Both are setpoints, so they
  differ only when the option was changed after the tool was anchored, and no
  margin absorbs that. The run still heats to the recorded setpoint: the anchor
  frequency says nothing about any other one. Re-running `EDDY_CALIBRATE_Z` is
  what moves a tool to a new setpoint.
- A preheat waits until each nozzle reads within `calibration_temp_band` of its
  setpoint, in either direction, and not until its controller has settled.
  Kalico's own heater wait is a settle test (near the target and nearly flat),
  which a hotend drifting around its setpoint can take minutes to satisfy,
  above all when it has to cool passively into a tight band. A tool already
  within a degree or two is in the thermal state the measurement needs. The
  wait polls `printer.wait_while`, the primitive Kalico's own heater wait uses,
  so a shutdown or a gcode interrupt ends it. It carries no deadline of its
  own: `verify_heater` already supervises every heater and shuts the printer
  down when one stops approaching its target, and that shutdown ends the wait,
  so a second deadline here would only race the machinery that owns the
  diagnosis.
- `calibration_settle_time` exists because the heater block reaches its
  setpoint well before the nozzle tip does. Both commands dwell the same time
  after the band is reached, so both measure the same thermal state.
- With `calibrate_z: False` nothing is heated at all. No anchor exists, no
  descent runs, and the XY center is amplitude-invariant.

`calibration_temp` above 0 is a config requirement when `calibrate_z` is True.
A cold anchor could only be compared against a later measurement taken cold as
well, and a nozzle that has been hot does not come back to cold inside a run.

The heater of a tool is found by section name, because a fleet preheat sets
targets on tools that are not mounted. `tool_extruders` names them in tool
number order; without it each tool follows Klipper's own extruder naming
(`extruder` for T0, `extruder1` for T1, and so on). Every tool's heater is
resolved at `klippy:connect`, where the heater sections exist and a wrong name
is still a startup failure rather than a surprise partway into a run.

The plugin instantiates Kalico's `extras.ldc1612.LDC1612` directly with its own
config wrapper, so users need no separate `[ldc1612]` section. (Fallback design
if the wrapper fights us: require a named `[ldc1612 eddy_cal]` section and
reference it; decide during implementation, wrapper preferred.)

## Commands

- `EDDY_QUERY`: print current frequency, sanity check wiring.
- `EDDY_LOCATE [DEBUG=1]`: coarse straight line scan passes over the configured
  coil position, finds and stores the refined coil center for the session;
  prints it.
  `DEBUG=1` also prints each scan pass's diagnostic rows.
- `EDDY_CALIBRATE_OFFSET [T=<list>] [DEBUG=1]`: full XY(+Z) measurement.
  `T=<n>` measures that one tool, `T=0,1,2` measures those three, and a
  missing `T=` measures every tool from T0 upward in turn. Any run over more
  than one tool ends with a summary table of their offsets. `T=0` measures the
  baseline and always replaces the session baseline with its result, reporting
  its own offsets as zero by definition; it is measured first whatever order
  the list gives it in. A list without `T=0` requires that `T=0` was
  calibrated in this session and errors otherwise. Each non-baseline tool's
  result is passed to `apply_offsets_gcode` if that option is set.
  `DEBUG=1` also prints each scan pass's diagnostic rows.
  1. XY: for each configured angle, scan through the current center estimate,
     forward and reverse; parabolic fit of the response extremum per pass;
     average pairs; least-squares reconstruct the center from the two
     directional results; iterate once more at the refined center.
  2. Z: hold the XY center, descend from z_start to z_stop stepwise, both
     heights above the coil top face (probe_eddy_current
     calibration-move pattern: step, dwell, window-average samples), producing a
     freq-vs-Z curve; report the Z at which frequency crosses the tool's own
     stored anchor frequency, and the switch trigger plane that implies. With
     `calibrate_z: False` this whole step is skipped and no descent runs.
  3. Print labeled results: raw center, the Z curve rows when a descent ran,
     and offsets relative to the T0 baseline.
- `EDDY_CALIBRATE_Z [T=<list>] [DEBUG=1]`: one-time per-tool Z reference. Presses
  the contact switch four times, discards the first press as a warm-up, takes
  the median of the remaining three as the trigger plane, then measures the
  tool's XY center and descent curve and stores the curve midpoint's height
  above that trigger plane together with the frequency there, the nozzle
  setpoint it was held at and the reading it observed. Written to the state
  file immediately. Requires
  `calibrate_z: True` and the switch options.
  A missing `T=` anchors every tool from T0 upward in turn. Anchoring a tool
  discards that tool's measurements from this session, and anchoring the
  baseline tool clears the session baseline as well, so the next offset run
  measures T0 again.
- `EDDY_REPEATABILITY T=<tool> RUNS=<n> CYCLES=<n> [SKIP_Z=1] [DEBUG=1]`:
  measure one tool repeatedly and report the spread. `T=`, `RUNS=` and `CYCLES=`
  are all required, with no defaults: what a study is worth depends entirely on
  how many measurements it took and how many dockings it covered, so neither
  count is guessed at. A cycle mounts another tool and remounts the measured
  one, then takes `RUNS` measurements without touching it in between. The tool
  it docks through is the lowest tool number of the fleet that is not the
  measured one, so docking needs `tool_count` and `toolchange_gcode`. Without
  either one the cycles still run and the summary says no docking was
  exercised and names what is missing; more than one cycle is then a
  control run whose cycles are separated by time rather than by a toolchange.
  Each cycle runs in its own retreat scope, so it ends at the lift height every
  toolchange starts from and the next cycle's docking begins there.
  `SKIP_Z` defaults to 1, which skips the descent even with `calibrate_z:
  True`; `SKIP_Z=0` with `calibrate_z: False` is an error naming `calibrate_z`
  rather than a silently ignored parameter. Heating has three cases: the
  tool's recorded anchor setpoint, once before the study rather than per
  cycle, when `calibrate_z` is True and the tool has an anchor; no heating
  when the tool has no anchor, whose spread is then not comparable with an
  offset run's; and no heating at all when `calibrate_z` is False. The study
  file is resolved before anything is heated, so a directory that cannot be
  written fails in a second rather than after minutes of heating. The command
  prints a progress row naming the cycle and the measurement about to run
  before each one starts, and one summary at the end of the whole study: the
  run and cycle counts, the descent and docking state, then per axis the
  measurement spread, the docking spread with its degrees of freedom, and the
  worst deviation from the mean. A study does not replace the session
  baseline; it repeats one measurement rather than establishing one, and a
  study of the baseline tool leaves the offset columns empty exactly as a
  calibration run of that tool does.
- Both calibration commands share one tool rule. `T=` takes one tool number or
  a comma separated list of them with no spaces (`T=0,1,2`); a duplicate, a
  tool outside the machine's range and a malformed entry are each an error
  naming the entry. Omitting `T=` runs the whole fleet, needs `tool_count`,
  and produces the same tool list a full `T=` would have. Any run that covers
  more than one tool needs `toolchange_gcode`, because it mounts each tool it
  measures. With `toolchange_gcode` set, the plugin mounts the tool it is about
  to work on in every case; without it, a single-tool run works on whatever
  tool is mounted.
  A failure inside a run over several tools lifts the toolhead clear and names
  the tool and the stage that failed, keeping the results and references
  already taken.
- Heating a list of tools is one parallel preheat before the per-tool loop:
  every setpoint is set first, then every tool is waited into its band, then
  the settle dwell runs once. A tool that has to cool into the band is waited
  for the same way, which is why the command prints each tool's current
  reading, its setpoint and the band before it starts waiting.
- All output as labeled raw-value rows, not prose.

## Status object

`get_status` publishes the stored Z references and this session's measurements
as `printer.eddy_tool_calibration`, so a macro can act on a measurement rather
than on a console line. The README carries the key list; the two rules the
document is built on are here.

A tool's measurement is published only while the session baseline it was
compared against is still in place. A baseline run raises the session number
and an anchor run of the baseline tool clears the baseline outright, and in
both cases the results measured against the old one leave the document rather
than staying in it with empty offsets, which would read as offsets of zero.

The anchors withhold the sensor settings they were measured with. An anchor
frequency describes a height only under the drive current and the clock it was
taken at, and the plugin compares those against the live settings itself and
refuses the reference when they differ, so a macro has no decision to make from
them.

## Algorithm notes (ported from upstream, with provenance)

- Directional scan + extremum parabolic fit: upstream tool_eddy_calibration
  (chengxg), GPLv3. Sub-sample precision from 3-point parabola around the peak
  of the frequency-vs-position curve.
- Pair cancellation: run each scan in both directions and average fitted centers;
  cancels the constant transport latency between sample timestamps and motion.
- Position mapping: `motion_report.get_trapq_position(print_time)` per sample
  (improvement over upstream's constant-velocity interpolation; handles accel
  ramps, allowing scans that start closer to the coil).
- Two angles (45/135) suffice to reconstruct XY; axis-aligned scans would
  couple an axis error into itself. Upstream default kept.
- Z curve: piecewise handling like probe_eddy_current's EddyCalibration; we fit
  the descent curve and evaluate the reference-frequency crossing. Per-tool
  reference makes the method material-independent for Z (brass vs steel each get
  their own anchor), which is the switch-anchored reference above.
- Material independence for XY comes free: symmetry center of any monotonic
  response is amplitude-invariant.
- Repeatability summary: the gauge repeatability and reproducibility
  decomposition of the AIAG measurement systems analysis manual, in its one-way
  analysis of variance form. The runs inside a cycle differ by the measurement
  alone, so their pooled standard deviation over `cycles * (runs - 1)` degrees
  of freedom is the measurement spread that is printed. The cycle means differ
  by the measurement plus whatever the docking adds; the docking's own
  component follows from subtracting the measurement's share of the cycle
  means' own variance, the within-cycle variance over the number of runs. That
  corrected component is what is printed, labeled the docking spread, with the
  `cycles - 1` degrees of freedom folded into the same row so two cycles read
  visibly as the one degree of freedom they are. Where the subtraction leaves
  nothing, the manual's convention is followed and the component is reported
  as zero, never as a negative variance; a study whose measurements never
  varied at all says exactly that, rather than blaming measurement noise it
  did not have. The worst deviation from the grand mean is printed alongside,
  because a worst case is what a user comparing against a contact probe asks
  about and a standard deviation does not answer. A value that is not finite is
  refused before any of this, naming the cycle and the run it sat in, rather
  than passing the ordered comparisons and coming back out as a confident zero
  spread beside a plausible range.

## Error handling

Every failure path raises a gcode error with an actionable message: no samples
(wiring/I2C), too few samples (scan too fast/short), fit residual too large
(dirty coil area, wrong height, metal nearby), no extremum inside scan window
(coil center estimate off: run EDDY_LOCATE), sensor amplitude errors from the
LDC1612 status register (drive current miscalibrated).

## Automated tests

`tests/` is the unit suite and imports no klippy, so it proves the fit math and
nothing about the plugin's Kalico-facing lines. `integration_test.py` covers
those: it builds the `linuxprocess` firmware dictionary from a Kalico checkout,
installs the plugin into that checkout's `klippy/plugins/`, and runs the cases
in `integration/` through Kalico's own `scripts/test_klippy.py` against the
simulated MCU. Both run on every push and pull request from
`.github/workflows/tests.yaml`, the integration job over two Kalico versions:
`main`, and commit `3b98cf51` of 2025-12-14, the era of the printer this is used
on. Neither leg is allowed to fail; a leg later expected to fail for a
documented reason gets `continue-on-error: true` with the reason beside it.

To run the same thing on a Linux host with `make` and a C compiler:

```bash
pip install -r requirements_test.txt
python -m pytest tests/
git clone https://github.com/KalicoCrew/kalico ~/kalico
pip install -r ~/kalico/scripts/klippy-requirements.txt
python integration_test.py ~/kalico
```

## Validation plan

1. Bring-up on BTT Eddy Coil (bigger coil, same electronics): EDDY_QUERY,
   EDDY_LOCATE, scan curves visually sane (save_csv + offline plot).
2. Repeatability: EDDY_REPEATABILITY on one tool, which reports the
   measurement spread, the docking spread and the worst deviation from the
   mean per axis. Target: XY stddev < 5 um on crab board.
3. Cross-check vs contact method (tools_calibrate / owner's pin) on 2+ tools;
   agreement within the contact method's own repeatability.
4. Dirty-nozzle test: repeat (2) with deliberately filthy nozzle; deltas vs
   clean runs are the headline metric of the whole project.
5. Kalico-update smoke: EDDY_QUERY + one EDDY_CALIBRATE_OFFSET after each update.
