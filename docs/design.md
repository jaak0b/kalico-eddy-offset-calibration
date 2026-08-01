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
  eddy_tool_calibration.py    # the plugin (single module, ~600-800 lines)
  reference/
    tool_eddy_calibration.py  # upstream file, unmodified, for algorithm reference
  install.sh                  # symlink into ~/klipper/klippy/plugins/
  docs/ examples/ tests/
```

Install: symlink `eddy_tool_calibration.py` into `klippy/plugins/`. Moonraker
update_manager entry documented in README.

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
coil_inner_diameter: 2.0        # mm, coil bore; sets the default fit window
scan_height: 1.0                # mm above the coil top face during XY scans
scan_safe_z: 2.0                # mm above the scan height for travel moves
z_start: 5.0                    # descent start, mm above the coil top face
z_stop: 0.5                     # descent end, mm above the coil top face
z_step: 0.05                    # descent step; must divide z_start - z_stop
# --- scan tuning ---
scan_speed: 4.0                 # mm/s
scan_length: 4.0                # mm, must exceed coil diameter
locate_scan_length: 12.0        # mm, EDDY_LOCATE coarse pass; 3 * scan_length
travel_speed: 100.0             # mm/s for XY travel between passes
z_speed: 10.0                   # mm/s for every Z leg
scan_angles: 45, 135            # degrees; two directions reconstruct X and Y
pair_scans: True                # forward+reverse averaging (latency cancellation)
samples_min: 100                # abort fit below this sample count per pass
query_time: 0.5                 # seconds EDDY_QUERY samples for
save_csv: False                 # dump raw scan data for analysis
save_history: True              # append every measurement to the per-tool drift log
csv_dir: EddyToolCalibration/data # folder, relative to the config dir, for scan CSV files
# --- fit tuning ---
fit_window_radius: 1.0          # mm each side of the extremum; coil_inner_diameter / 2
fit_sigma_fraction: 0.5         # Gaussian weight sigma as a fraction of the window
fit_vertex_limit: 0.5           # reject a vertex past this fraction of the window
edge_margin: 0.15               # fraction of each pass treated as edge
freq_min: 1000000.0             # Hz; samples below this are discarded as noise
# --- Z offsets ---
calibrate_z: False              # run the Z descent and report Z offsets
# --- nozzle temperature ---
calibration_temp: 150.0         # C every calibration measurement is taken at
calibration_settle_time: 30.0   # s dwell after the target is reached
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
tool_count: 4                   # 1 to 99; tools are T0 .. T(tool_count-1), no holes
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
`anchor_frequency` and `temperature`, the three fields an offset run reads,
plus diagnostic record fields (`trigger_z`, `curve_low_z`, `curve_high_z`,
`center_x`, `center_y`, `updated`) that let a stale anchor be recognised
later but are never fed back into a measurement. Writes serialise to a
temporary file and `os.replace` it over the target, so an interrupted write
cannot leave a truncated state file; a fleet run rewrites the file after each
tool, so an abort partway through keeps the anchors already measured. A
missing file is normal and means no tool is anchored yet; a file that exists
but does not parse, carries a `version` this build does not handle, or is
missing a field this build needs, is a config error naming the path and the
command that rewrites the references from scratch. There is no migration
path: every field an anchor record carries comes out of one measurement, so a
record missing one cannot be completed without measuring again.

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
3 decimals, temperatures to 1 decimal. Step distances are the one exception, at
6 decimals, because a microstep is a few microns and 4 decimals would round most
machines' resolution away. Values are printed exactly as measured at that
precision, never rounded further and never clamped.

## Machine resolution

Every readout that prints a fitted center also prints the step distance of each
X and Y stepper, so a reader can judge a measured spread against the machine's
own resolution. The figure comes from the kinematics
(`toolhead.get_kinematics().get_steppers()` and each stepper's
`get_step_dist()`), never worked out from `rotation_distance`,
`full_steps_per_rotation` and `microsteps` here, so it is the distance the
machine itself steps by. Steppers that cannot be read produce no rows at all
rather than a guess, and never fail the measurement they accompany.

## Measurement logs

Two CSV layouts, both written into `csv_dir`:

- `history_T<n>.csv`, one line per completed measurement of a tool, appended by
  `EDDY_CALIBRATE_OFFSET`, `EDDY_CALIBRATE_Z` and `EDDY_REPEATABILITY` alike.
  Columns: `timestamp`, `command`, `center_x`, `center_y`, `offset_x`,
  `offset_y`, `z_crossing`, `trigger_z`, `offset_z`, `temperature`,
  `samples_used`. This is the drift log, the record a claim about drift over
  time rests on. An anchor run leaves `z_crossing` empty, because its descent
  defines the anchor rather than being evaluated against one; what it
  contributes is the trigger plane it pressed. A value that was not measured is
  an empty field, never a zero. The log is governed by `save_history` and not by
  `save_csv`: the latter dumps the raw samples of each scan pass, a different
  concept. A write that fails is a gcode error naming the path, raised after the
  measurement's own rows are printed, so a failed log never costs a measurement
  that succeeded.
- `repeatability_T<n>_<index>.csv`, one file per study, holding the same columns
  with `cycle` and `run` in front. The index is one above the highest already in
  the directory, so a study never overwrites the data of one that ran before it.

## Nozzle temperature

How the frequency reads against height depends on how hot the nozzle is, so a
Z reference is only valid for a measurement taken at the temperature the
reference was measured at. The plugin holds that temperature itself rather
than trusting the machine to be in the right state:

- `EDDY_CALIBRATE_Z` heats to `calibration_temp`, waits for the target, dwells
  `calibration_settle_time`, measures, and records into the anchor what the
  heater actually read rather than the target it was given.
- `EDDY_CALIBRATE_OFFSET` reads each tool's recorded temperature out of the
  state file and heats that tool back to it. It never measures at
  `calibration_temp`, so an offset run reproduces the thermal state of the
  anchor it evaluates against by construction.
- A tool whose recorded temperature differs from `calibration_temp` by more
  than 1 C is reported as a console warning naming both values. The run still
  measures at the recorded temperature: the anchor frequency says nothing
  about any other one. Re-running `EDDY_CALIBRATE_Z` is what moves a tool to a
  new temperature.
- `calibration_settle_time` exists because the heater block reaches its target
  well before the nozzle tip does. Both commands dwell the same time after the
  target is reached, so both measure the same thermal state.
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
- `EDDY_LOCATE [DEBUG=1]`: coarse raster over the configured coil position,
  finds and stores the refined coil center for the session; prints it.
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
  above that trigger plane together with the frequency there, and the nozzle
  temperature it measured at. Written to the state file immediately. Requires
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
  measured one, so `CYCLES` above 1 needs `tool_count` and `toolchange_gcode`
  and names both when either is missing. `CYCLES=1` runs anywhere and exercises
  no docking, which the plan and the summary both say. `SKIP_Z` defaults to 1,
  which skips the descent even with `calibrate_z: True`; `SKIP_Z=0` with
  `calibrate_z: False` is an error naming `calibrate_z` rather than a silently
  ignored parameter. Heating follows `EDDY_CALIBRATE_OFFSET`: the tool's
  recorded anchor temperature, once before the study rather than per cycle, and
  nothing at all when the tool has no anchor. The command prints its plan and a
  rough run time before it moves, each cycle's mean and spread as that cycle
  ends, and the summary below at the end. A study does not replace the session
  baseline; it repeats one measurement rather than establishing one.
- Both calibration commands share one tool rule. `T=` takes one tool number or
  a comma separated list of them with no spaces (`T=0,1,2`); a duplicate, a
  tool outside the machine's range and a malformed entry are each an error
  naming the entry. Omitting `T=` runs the whole fleet and needs `tool_count`
  and `toolchange_gcode`, naming both when either is missing, and produces the
  same tool list a full `T=` would have. A list of more than one tool needs
  `toolchange_gcode` as well, because it mounts each tool it measures. With
  `toolchange_gcode` set, the plugin mounts the tool it is about to work on in
  every case; without it, a single-tool run works on whatever tool is mounted.
  A failure inside a run over several tools lifts the toolhead clear and names
  the tool and the stage that failed, keeping the results and references
  already taken.
- Heating a list of tools is one parallel preheat before the per-tool loop:
  every target is set first, then every tool is waited for, then the settle
  dwell runs once. A tool that has to cool to its target is waited for the same
  way, which is why the command prints its targets before it starts waiting.
- All output as labeled raw-value rows, not prose.

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
  of freedom is the within-cycle spread. The cycle means differ by the
  measurement plus whatever the docking adds, so their standard deviation over
  `cycles - 1` degrees of freedom is reported as measured, and the docking's own
  component follows from subtracting the measurement's share of it, the
  within-cycle variance over the number of runs. Both are printed: the raw
  spread of the cycle means and the corrected docking component beside it, so
  the correction is visible rather than assumed. Where the subtraction leaves
  nothing, the manual's convention is followed and the component is reported as
  zero, labeled as a docking the data does not resolve, never as a negative
  variance. The range and the largest deviation from the grand mean are printed
  alongside, because a worst case is what a user comparing against a contact
  probe asks about and a standard deviation does not answer.

## Error handling

Every failure path raises a gcode error with an actionable message: no samples
(wiring/I2C), too few samples (scan too fast/short), fit residual too large
(dirty coil area, wrong height, metal nearby), no extremum inside scan window
(coil center estimate off: run EDDY_LOCATE), sensor amplitude errors from the
LDC1612 status register (drive current miscalibrated).

## Validation plan

1. Bring-up on BTT Eddy Coil (bigger coil, same electronics): EDDY_QUERY,
   EDDY_LOCATE, scan curves visually sane (save_csv + offline plot).
2. Repeatability: EDDY_REPEATABILITY on one tool, which reports the
   within-cycle spread, the between-cycle spread, the range and the largest
   deviation from the mean per axis. Target: XY stddev < 5 um on crab board.
3. Cross-check vs contact method (tools_calibrate / owner's pin) on 2+ tools;
   agreement within the contact method's own repeatability.
4. Dirty-nozzle test: repeat (2) with deliberately filthy nozzle; deltas vs
   clean runs are the headline metric of the whole project.
5. Kalico-update smoke: EDDY_QUERY + one EDDY_CALIBRATE_OFFSET after each update.
