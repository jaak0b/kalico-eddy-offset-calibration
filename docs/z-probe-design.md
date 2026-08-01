# Automatic Z reference and calibration orchestration

Status: approved design, two implementation rounds. Round 1 adds Z calibration
against a contact switch and a plugin-owned state file. Round 2 lets both
calibration commands sweep the whole tool fleet and publishes measured results
for macros. Supersedes the `z_offset_mode` section of `docs/design.md`.

## Part 1: For review

### What changes for you

**Round 1: the printer finds Z by itself.**

Today the plugin measures how far apart two tools sit in X and Y, but Z needs
you to do a paper test by hand and type the number in. With a small switch
mounted next to the sensor coil, the kind of endstop a nozzle can press on (a
sexbolt or a ball-style switch), the printer finds that number itself.

One command drives the nozzle onto the switch four times, throws away the first
press as a warm-up, takes the middle of the remaining three, and links that
height to the reading the eddy sensor makes over the coil. Do this once per tool
after a nozzle or hotend change and the plugin knows that tool's Z from then on.

The switch's own height does not have to be known or accurate. Every tool
presses the same switch, only the differences between tools matter, and whatever
height the switch happens to sit at cancels out.

Results are saved for you. The plugin keeps its own small file next to your
config and writes it as soon as a reference is measured. There is nothing to
paste and no `SAVE_CONFIG` to run. Your config file holds your setup; the
plugin's file holds its measurements.

**Round 2: calibrate every tool with one command.**

You tell the plugin how many tools you have and how to mount one, and either
command will then walk the whole set on its own: mount T0, do the work, mount
T1, do the work, and so on. Leave the tool number off a command and it means all
tools. Give it a tool number and it does that one tool.

If you also tell the plugin how to apply an offset, it applies each tool's
result as it goes, so the machine is calibrated when the command finishes. The
plugin never learns anything about your toolchanger; it just runs the lines you
gave it.

Measured results are also published where macros and web interfaces can read
them, so you can build your own reporting or your own apply step on top.

### The two commands

`EDDY_CALIBRATE_Z [T=<tool number>]`

The one-time Z setup, against the switch. Run it after you change a nozzle or a
hotend, or after you move the coil or the switch. With `T=` it sets up that one
tool; without `T=` it sets up every tool in turn. This is not the command you
run before a print. It exists so that the routine measurement has something to
measure against.

`EDDY_CALIBRATE_OFFSET [T=<tool number>]`

The routine measurement. It measures a tool over the coil and reports its
offsets against T0. With `T=` it measures that one tool; without `T=` it
measures every tool in turn and, if you configured an apply step, applies each
result as it goes.

Leaving `T=` off used to be a mistake worth refusing, because the plugin would
have had to guess which tool was mounted. It no longer guesses: an omitted tool
number now means the explicit request "all of them", and the plugin mounts each
one itself.

The plugin mounts tools whenever you have told it how. If you configured the
toolchange lines, both commands mount the tool they are about to work on, in
single-tool mode as well as in a sweep. If you did not, both commands assume the
tool is already mounted, which is how the plugin behaves today.

### New config options

Round 1:

- `calibrate_z`: turn Z calibration on or off. Off by default, because the Z
  descent is the slow part of a run and there is no point in it if you only want
  X and Y.
- `switch_pin`: which pin the switch is wired to.
- `switch_x`, `switch_y`: where the nozzle has to stand to press the switch.
- `switch_probe_z_start`: the height the nozzle drops to before it starts
  feeling for the switch. Set it just above the switch.
- `switch_probe_speed`, `switch_probe_lift_speed`, `switch_probe_max_travel`,
  `switch_probe_sample_retract_dist`: how fast, how far and how high the presses
  run. The defaults come from the existing toolchanger calibration tools and are
  fine to leave alone.
- `switch_probe_tolerance`: how far the three counted presses may disagree
  before the run is called a failure. Defaults to 0.02 mm.

Round 2:

- `tool_count`: how many tools the machine has. Tools are numbered T0 upward
  with no gaps. Required only if you want to run a command without `T=`.
- `toolchange_gcode`: the lines that mount a tool. The tool number is available
  to them. Required only if you want to run a command without `T=`.
- `apply_offsets_gcode`: optional. The lines that apply a measured offset to a
  tool. The tool number and the measured X, Y and Z offsets are available to
  them. Leave it out and the plugin only reports.

### What gets removed

- `EDDY_CALIBRATE_TOOL`, renamed to `EDDY_CALIBRATE_OFFSET`. Update your macros.
- `EDDY_SET_Z_REF`, the command for typing in a Z you measured yourself. Z
  calibration now requires the switch. Without a switch you still get X and Y.
- The `z_ref_t0` to `z_ref_t15` config lines. References live in the plugin's
  own file now. If those lines are still in your config the printer says so at
  startup and names the command to run instead.
- The `z_offset_mode` option, both of its settings. There is one way Z works
  now: each tool gets its own reference. The setting that assumed every tool
  carries an identical hotend is gone, because it quietly produced a wrong
  answer on machines where that was not true. If the option is still in your
  config the printer says so at startup instead of ignoring it.

### What happens when something goes wrong

Every failure stops with a message naming the cause and the fix, and never
prints a number that looks plausible but was not measured:

- The nozzle travels its full allowance and the switch never clicks: the message
  names the switch position and travel options.
- The switch already reads pressed before the nozzle moves: the message says so
  rather than quietly trying again, because a switch that is stuck closed is a
  fault worth seeing.
- The three counted presses disagree by more than you allowed: the message says
  by how much.
- You measure a tool that has no Z reference while `calibrate_z` is on: the run
  stops before it moves and names `EDDY_CALIBRATE_Z`.
- You leave `T=` off without having configured the tool count and the toolchange
  lines: the run stops and names the missing options.
- A tool fails partway through a sweep: the run stops, the nozzle is left clear
  of the coil and the switch, and the message names the tool that failed. Tools
  already finished keep their results.

### Where files live

Both under a single folder next to your printer config:

- `EddyToolCalibration/calibration_state.json`, the plugin's saved references.
- `EddyToolCalibration/data/`, the raw scan dumps `save_csv` writes.

The dumps sit in their own subfolder so clearing them out cannot take the saved
references with them.

## Part 2: Technical design

### Config schema

Round 1 additions:

```ini
[eddy_tool_calibration]
# --- Z calibration ---
calibrate_z: False              # run the Z descent and report Z offsets
# --- contact switch, required when calibrate_z is True ---
switch_pin: ^PA1                # endstop pin, invert and pullup prefixes allowed
switch_x: 340.0                 # machine X of the nozzle over the switch
switch_y: 5.0                   # machine Y of the nozzle over the switch
switch_probe_z_start: 3.0       # machine Z the probing move starts from
# --- probing tuning, defaults from tools_calibrate ---
switch_probe_speed: 5.0                  # mm/s
switch_probe_lift_speed: 5.0             # default: switch_probe_speed
switch_probe_max_travel: 4.0             # mm below switch_probe_z_start
switch_probe_sample_retract_dist: 2.0    # mm
switch_probe_tolerance: 0.020            # mm, spread across the counted presses
```

Round 2 additions:

```ini
tool_count: 4                   # tools are T0 .. T(tool_count-1), no holes
toolchange_gcode:
    T{tool}
apply_offsets_gcode:            # optional
    SET_TOOL_OFFSET T={tool} X={offset_x} Y={offset_y} Z={offset_z}
```

Changed option:

```ini
csv_dir: EddyToolCalibration/data    # was EddyToolCalibration
```

Removed options: `z_offset_mode`, `z_ref_t0` through `z_ref_t15`.

Option semantics and guards:

- `switch_x`, `switch_y` and `switch_probe_z_start` are machine coordinates, not
  heights above the coil top face, because the switch is a separate fixture with
  no fixed relation to the coil face.
- Travel to and from the switch uses the existing `scan_safe_z` as clearance
  above `switch_probe_z_start` and the existing `_move` helper, so no separate
  travel height option is added.
- Speeds and distances take the same `above`/`minval` guards
  `PrinterProbeMultiAxis` applies. `switch_probe_tolerance` takes `above=0.0`.
- `tool_count` is `config.getint('tool_count', None, minval=1, maxval=99)`. It
  is optional at load and required only by a run without `T=`. `MAX_TOOLS` stays
  as the bound on `T=` and, when `tool_count` is set, `tool_count` must not
  exceed it.
- `toolchange_gcode` and `apply_offsets_gcode` are loaded with klippy's standard
  template machinery, as `probe.py` and `toolchanger.py` do:
  `self.printer.load_object(config, 'gcode_macro')` followed by
  `gcode_macro.load_template(config, 'toolchange_gcode', '')`. Both default to
  the empty template.
- The five switch options are read at load but their absence is not a load
  error, because a machine that only wants XY never needs them.
  `EDDY_CALIBRATE_Z` raises a gcode error naming whichever is missing.

Load-time rejections, both `config.error`:

- `z_offset_mode` present: tell the owner to remove it and set `calibrate_z`.
- any `z_ref_t<n>` present: tell the owner to remove it and run
  `EDDY_CALIBRATE_Z T=<n>` once per tool, and name the state file path so it is
  clear where the value goes instead.

A removed option is never silently ignored.

### Geometry and math

Write `Zt_i` for the machine Z at which tool `i` triggers the switch, and
`F_i(z)` for tool `i`'s measured frequency-vs-machine-Z descent curve over the
coil. The switch trigger point is one fixed physical plane, so `Zt_i - Zt_j` is
exactly the Z nozzle offset between tools `i` and `j`, whatever height the
switch happens to sit at: the switch's own height is a constant that cancels in
every difference.

The anchor point is not configured. `EDDY_CALIBRATE_Z` takes it at the midpoint
by height of the tool's own measured descent range,
`zm_i = (z_low_i + z_high_i) / 2`. The midpoint is the height furthest from both
ends of that descent, so a reference taken there leaves the widest margin on
either side for a later descent to still bracket the frequency, which is exactly
the property `z_curve_shared_reference` already computes. What is stored is not
that machine Z but its height above the trigger plane,

```
h_i = zm_i - Zt_i
f_i = F_i(zm_i)
```

so the persisted pair `(h_i, f_i)` is switch-relative. Because each tool's
anchor sits at the middle of its own curve, `h_i` differs from tool to tool by
exactly the amount the tools differ in Z, and that difference is what the
persisted value carries forward.

Evaluation in any later session, with no switch involved: measure tool `i`'s
curve, find the height `zc_i` at which it reaches `f_i` (the existing
`z_curve_z_at_freq`), and reconstruct that tool's trigger plane as
`zc_i - h_i`. The reported Z offset of tool `i` against the baseline tool `b`
is

```
offset_z = (zc_i - h_i) - (zc_b - h_b)
```

In the session the anchors were taken this reduces to `Zt_i - Zt_b` by
construction, because `zc_i` is then `zm_i`. The coil face height `coil_z` never
enters an offset. Nothing about the coil-versus-switch height difference biases
the result; it only has to be small enough that the switch probing and the
descent are both reachable, and the automatic midpoint removes the one place a
configured height could have been set wrong.

Because each tool carries its own frequency reference, the comparison stays
independent of nozzle material, which is the property the removed
`identical_hotends` mode did not have.

### State file

Path: `<config dir>/EddyToolCalibration/calibration_state.json`, where
`<config dir>` is `os.path.dirname(os.path.abspath(printer.get_start_args()
['config_file']))`, the same derivation `_save_csv` already performs. That
derivation moves into one helper that both the state file and the CSV directory
call, so the config directory is resolved in a single place.

Schema, version 1:

```json
{
  "version": 1,
  "anchors": {
    "0": {
      "anchor_height": 4.2130,
      "anchor_frequency": 12345678.000,
      "trigger_z": 1.2340,
      "curve_low_z": 0.5000,
      "curve_high_z": 5.0000,
      "center_x": 349.8721,
      "center_y": 5.0413,
      "updated": "2026-08-01T14:03:22"
    }
  }
}
```

- `anchor_height` is `h_i` in millimetres above the switch trigger plane, and
  `anchor_frequency` is `f_i` in Hz. These two are the only fields the offset
  math reads.
- `trigger_z`, `curve_low_z`, `curve_high_z`, `center_x`, `center_y` and
  `updated` are diagnostic record: the machine Z the switch triggered at, the
  descent range the anchor was taken from, the XY center it was taken over, and
  when. They let a stale anchor be recognised after the coil or the switch
  moves. They are written and printed, never fed back into a measurement.
- Keys under `anchors` are decimal tool numbers as strings, because JSON object
  keys are strings.

Write path: serialise to a temporary file in the same directory and
`os.replace` it over the target, so an interrupted write cannot leave a
truncated state file. In a fleet run the file is rewritten after each tool, so
an abort at tool three keeps the anchors of tools zero to two. Failure to write
raises a gcode error naming the path and the permission fix; the in-memory
anchor is not kept in that case, because reporting an anchor that did not
persist would mislead the next session.

Read path: at `__init__`, load the file if it exists. A missing file is normal
and means no tool is anchored yet. A file that exists but does not parse, or
carries a `version` this build does not handle, is a `config.error` naming the
path and saying the file may be deleted to start over. Unknown fields inside an
anchor are ignored so a future version can add record fields; an unknown
top-level `version` is not, per the closed-set rule.

### The tool selection rule, stated once

Both commands take an optional `T=`. It is read with
`gcmd.get_int('T', None, minval=0, maxval=MAX_TOOLS - 1)` and dispatched over
exactly two cases:

- `T=<n>` given: the command runs for that one tool.
- `T` omitted: the command runs for `T0` through `T(tool_count-1)` in order.
  This requires `tool_count` and a non-empty `toolchange_gcode`; a missing
  either is a gcode error naming both options and explaining that without them
  the plugin cannot mount the tools a sweep needs. There is no third case and no
  fallback to a guessed tool.

Mounting is one rule shared by both commands and both cases: if
`toolchange_gcode` is configured, the plugin renders and runs it with `{tool}`
before working on that tool, in single-tool mode as well as in a sweep. If it is
not configured, the plugin assumes the tool is already mounted and works on
whatever is there, which is the plugin's existing behaviour. This keeps the
plugin ignorant of any toolchanger: it runs the lines the owner wrote and
nothing else.

The baseline rule is unchanged. `EDDY_CALIBRATE_OFFSET T=0` measures the
baseline and always replaces the session baseline. `EDDY_CALIBRATE_OFFSET T=<n>`
for `n > 0` still requires that `T0` was measured in this session. A sweep
satisfies that naturally by starting at `T0`.

### Command flows

**`EDDY_CALIBRATE_Z [T=<n>] [DEBUG=1]`**, the only anchor path. Help text:
"One-time Z reference setup for the tool named by T=, or for every tool when T=
is left out. Presses the contact switch and binds the result to the eddy
sensor's reading. Run it after changing a nozzle or a hotend, or after moving
the coil or the switch. This is the setup step, not the routine offset
measurement; that is EDDY_CALIBRATE_OFFSET."

Per tool:

1. Require `calibrate_z: True`, all axes homed, and the five switch options
   present. The error names whichever is missing.
2. Mount the tool per the mounting rule above.
3. Query the switch through `query_endstop` and refuse to move if it already
   reads triggered.
4. Move to `(switch_x, switch_y)` at `switch_probe_z_start + scan_safe_z`, then
   down to `switch_probe_z_start`.
5. Press the switch four times, retracting `switch_probe_sample_retract_dist`
   between presses. Discard the first press. The first press on a cold or
   unseated switch travels differently from the rest and is a warm-up, not a
   measurement, so it is dropped by position rather than by any test on its
   value. From the remaining three take the median as `Zt` and the spread
   `max - min` as the quality figure. Spread above `switch_probe_tolerance` is
   an error, not a retry: a switch that cannot repeat inside the tolerance has a
   mechanical cause that another press does not fix.
6. Lift to the travel height, run `_measure_xy`, then `_measure_z_curve` at the
   measured center.
7. Take the curve midpoint through `z_curve_shared_reference`, giving
   `(zm, f)`. Store `(zm - Zt, f)` plus the record fields, write the state file,
   and print labeled rows: the three counted trigger heights, the median, the
   spread and the tolerance, the curve range, the anchor height above the
   trigger plane, the anchor frequency, and the state file path.

The command runs its own XY and Z measurement rather than reusing a curve left
by an earlier `EDDY_CALIBRATE_OFFSET`. Reusing one would deadlock: with
`calibrate_z: True` every measured tool must already have an anchor, so
`EDDY_CALIBRATE_OFFSET` refuses to run for an unanchored tool and the anchor
command could never be reached on a fresh tool. Anchors are set once per nozzle
change, so the extra descent costs little.

**`EDDY_CALIBRATE_OFFSET [T=<n>] [DEBUG=1]`**, the routine measurement,
renamed from `EDDY_CALIBRATE_TOOL`. Per tool:

1. Require homed axes. With `calibrate_z: True`, check before any motion that
   the tool and the baseline tool both have a stored anchor. In a sweep, check
   every tool from `T0` to `T(tool_count-1)` up front and name every tool that
   lacks one, because a half-anchored machine should fail in a second rather
   than partway through a ten minute run. The error names
   `EDDY_CALIBRATE_Z T=<n>`.
2. Mount the tool per the mounting rule above.
3. Run `_measure_xy`, and `_measure_z_curve` when `calibrate_z` is True. With
   `calibrate_z: False` no descent runs and no Z rows are printed, which is the
   behaviour the absent `z_offset_mode` used to give.
4. Compute the offsets against the session baseline, the Z part by the formula
   in the geometry section, and print the labeled summary block.
5. If `apply_offsets_gcode` is configured and this is not the baseline tool,
   render and run it with `{tool}`, `{offset_x}`, `{offset_y}` and, when
   `calibrate_z` is True, `{offset_z}`. With `calibrate_z: False` the `offset_z`
   key is absent from the context rather than present as zero, so a template
   that references it fails loudly instead of silently applying a Z offset that
   was never measured. The baseline tool is skipped because its offsets are zero
   by definition and applying zeros would overwrite whatever the owner set.

A sweep ends with a final table of every tool's offsets after the per-tool
blocks.

Abort behaviour, both commands: any error inside a sweep propagates after the
toolhead is lifted clear to `_machine_z(self.z_start + Z_APPROACH_HOP)`, the
same retreat height the descent already ends at. The mounted tool is left
mounted, because unmounting is `toolchange_gcode`'s business and this plugin has
no dock knowledge. The message names the failing tool number and the phase it
failed in (toolchange, switch probing, measurement, or apply), and the
underlying error text is preserved. Results and anchors for tools already
finished are kept, in memory and, for anchors, on disk.

### Status readout

`get_status(eventtime)` on the plugin object, published under the section name:

```python
{
  'calibrate_z': True,
  'tool_count': 4,
  'baseline_tool': 0,          # or None if no baseline this session
  'session_id': 3,             # increments each time a baseline is taken
  'last_tool': 2,              # or None
  'anchors': {
      '0': {'anchor_height': 4.2130, 'anchor_frequency': 12345678.0,
            'trigger_z': 1.2340, 'updated': '2026-08-01T14:03:22'},
  },
  'tools': {
      '2': {'session_id': 3,
            'center_x': 349.8721, 'center_y': 5.0413,
            'z_crossing': 2.7412,      # None when calibrate_z is False
            'offset_x': -0.0431, 'offset_y': 0.0122,
            'offset_z': 0.0157,        # None when calibrate_z is False
            'measured_time': 1234.567},
  },
}
```

- `tools` holds only tools measured in the current session, keyed by decimal
  tool number as a string so the dict survives JSON transport to Moonraker
  unchanged. A macro reads
  `printer.eddy_tool_calibration.tools['2'].offset_z`.
- `session_id` increments on every baseline run and is copied into each tool
  entry, so a consumer can tell whether two tools were measured against the same
  baseline without comparing timestamps. A tool entry whose `session_id` differs
  from the top-level one is stale.
- `measured_time` is the reactor `eventtime` the measurement finished at, the
  same clock other klippy status fields use.
- `anchors` mirrors the state file's measurement fields, so a macro can check
  whether a tool is anchored before invoking a measurement.
- The dict is rebuilt from `self.results`, `self.baseline` and the loaded state
  on each call. It is a view, never a second place a number is computed:
  `offset_x`, `offset_y` and `offset_z` come from the same helper that formats
  the printed offset rows and the `apply_offsets_gcode` context.

### Ported and reused, with sources

- Probing move and travel clamp: ported from
  `kalico/klippy/extras/tools_calibrate.py` `PrinterProbeMultiAxis._probe`
  (lines 317-331) and `_get_target_position` (333-356). The target is
  `pos[2] = max(pos[2] - max_travel, kin_status["axis_minimum"][2])` and the
  move is `phoming.probing_move(mcu_probe, pos, speed)`. That class itself is
  not instantiated: its constructor calls
  `printer.lookup_object("pins").register_chip("probe_multi_axis", self)`
  (301-303), a process-global name that would collide with an existing
  `[tools_calibrate]` section.
- Sample loop and retract: ported from the same file's `run_probe` (376-434).
  The aggregation is not ported: `_calc_mean` (361-365), `_calc_median`
  (367-374) and the tolerance-retry loop are replaced by the fixed four-press,
  discard-first, median-of-three scheme with a single tolerance check. A median
  of three is unambiguous and needs no even-count rule.
- Endstop wrapper: `tools_calibrate.ProbeEndstopWrapper` (438-476) is reused
  directly, constructed as `ProbeEndstopWrapper(wrapped_config, "z")`. It reads
  only `pin`, calls `allow_multi_use_pin` so the same switch can also serve an
  existing `[tools_calibrate]`, registers no gcode commands and no pin chip, and
  its `get_position_endstop()` returns `0.0`. A thin read-only config wrapper
  presents our `switch_pin` option under the name `pin`, so the option name in
  our section stays descriptive.
- Deliberately not reused: `kalico/klippy/extras/probe.py` `ProbeEndstopWrapper`
  (line 743). It requires a `z_offset` option whose meaning is the Z coordinate
  the kinematic rail is assigned when the probe triggers during a real home,
  which is not a quantity this plugin has or wants, and it runs
  `activate_gcode`/`deactivate_gcode` templates around every probe.
- Trigger position: `homing.PrinterHoming.probing_move` (`homing.py` line 441)
  returns the trigger position computed from step counts at trigger time, and
  raises `"Probe triggered prior to movement"` when `check_no_movement()`
  reports the endstop was already closed. `"No trigger on probe after full
  movement"` comes from `HomingMove.homing_move` (line 156).
- Deliberately not ported: the upstream fork's `TOOL_CALIBRATE_PROBE_OFFSET`
  catches `"Probe triggered prior to movement"`, dwells 0.5 s and probes again,
  a workaround for a bouncy contact probe. Retrying an already-triggered switch
  turns a stuck switch or a start height below the trigger point into a silent
  second attempt. The condition is raised here instead.
- Gcode templates: klippy's `gcode_macro` object, obtained with
  `printer.load_object(config, 'gcode_macro')` and used through
  `load_template(config, name, '')` and `run_gcode_from_command(context)`, the
  same pattern `probe.py` uses for `activate_gcode`.
- Descent, curve fitting and anchor evaluation: unchanged, the plugin's existing
  `_measure_z_curve`, `build_z_curve`, `z_curve_freq_at`, `z_curve_z_at_freq`
  and `z_curve_shared_reference`. The last of those is kept, not deleted: it is
  the midpoint-of-a-curve computation the automatic anchor placement needs, and
  its docstring is rewritten to describe that role rather than the removed
  shared-reference mode.

### Applying offsets: verified command syntax

The example in `apply_offsets_gcode` targets Contomo's
`klipper-toolchanger-hard` fork (verified against commit `854e96b`,
2026-06-13). That fork registers a dedicated offset command in
`klipper/extras/toolchanger.py`:

```python
self.gcode.register_command("SET_TOOL_OFFSET",
                            self.cmd_SET_TOOL_OFFSET)
```

```python
def cmd_SET_TOOL_OFFSET(self, gcmd):
    tool = self._get_tool_from_gcmd(gcmd)
    _x = gcmd.get_float("X", None)
    _y = gcmd.get_float("Y", None)
    _z = gcmd.get_float("Z", None)
    if _x is None and _y is None and _z is None:
        raise gcmd.error('SET_TOOL_OFFSET requires atleast one paramter of X, Y, Z')
    tool.gcode_x_offset = x = gcmd.get_float("X", tool.gcode_x_offset)
    tool.gcode_y_offset = y = gcmd.get_float("Y", tool.gcode_y_offset)
    tool.gcode_z_offset = z = gcmd.get_float("Z", tool.gcode_z_offset)
```

`_get_tool_from_gcmd` accepts `TOOL=<section name>` or `T=<number>` and falls
back to the active tool when neither is given. Their `docs/toolchanger.md`
documents the signature as `SET_TOOL_OFFSET [T=<n>] [X=<float>] [Y=<float>]
[Z=<float>]`, with `SAVE_TOOL_OFFSET [T=<n>] [X] [Y] [Z]` writing the same
values into pending config changes. So the correct example for that fork is

```ini
apply_offsets_gcode:
    SET_TOOL_OFFSET T={tool} X={offset_x} Y={offset_y} Z={offset_z}
```

The two forks differ here, and the difference matters to anyone copying the
example. The viesturz original has no `SET_TOOL_OFFSET` and no
`SAVE_TOOL_OFFSET`; offsets are reached through the generic parameter command,
whose handler is

```python
def cmd_SET_TOOL_PARAMETER(self, gcmd):
    tool = self._get_tool_from_gcmd(gcmd)
    name = gcmd.get("PARAMETER")
    value = ast.literal_eval(gcmd.get("VALUE"))
    tool.set_parameter(name, value)
```

and whose `tool.py` `_apply_param` recognises `gcode_x_offset`,
`gcode_y_offset` and `gcode_z_offset` by name. The equivalent there is three
commands:

```ini
apply_offsets_gcode:
    SET_TOOL_PARAMETER T={tool} PARAMETER=gcode_x_offset VALUE={offset_x}
    SET_TOOL_PARAMETER T={tool} PARAMETER=gcode_y_offset VALUE={offset_y}
    SET_TOOL_PARAMETER T={tool} PARAMETER=gcode_z_offset VALUE={offset_z}
```

`SET_TOOL_PARAMETER` exists in both forks with the same signature,
`SET_TOOL_PARAMETER [T=<n>] PARAMETER=<name> VALUE=<literal>`, and passes
`VALUE` through `ast.literal_eval`, so the value must be a Python literal. The
config reference documents both forms and says which is which. The plugin itself
stays ignorant of either: it only renders the template the owner wrote.

### Error paths

Every one raises a gcode error naming the fix:

- `calibrate_z` is False and `EDDY_CALIBRATE_Z` is run: name the option.
- Any of the five switch options missing: name the option.
- Axes not homed: the existing `_ensure_homed` message.
- `T=` omitted and `tool_count` or `toolchange_gcode` absent: name both options
  and say that `T=<n>` runs a single tool without them.
- `tool_count` set above `MAX_TOOLS`: `config.error` at load naming both.
- Switch reads triggered before the move: name a stuck switch, an inverted pin,
  or a `switch_probe_z_start` below the trigger point.
- No trigger within `switch_probe_max_travel` or before the kinematic Z minimum:
  name `switch_x`, `switch_y`, `switch_probe_z_start` and
  `switch_probe_max_travel`, and print the travel actually attempted.
- Spread of the three counted presses above `switch_probe_tolerance`: print the
  three heights, the spread and the tolerance.
- The state file cannot be written: name the path and the permission fix, and do
  not keep the anchor in memory.
- The state file exists but does not parse, or carries an unknown `version`:
  `config.error` at load naming the path.
- `calibrate_z: True` and a tool to be measured, or the baseline tool, has no
  anchor: name `EDDY_CALIBRATE_Z T=<n>`, before any motion. In a sweep, list
  every unanchored tool in one message.
- A stored anchor frequency lies outside a freshly measured descent: print the
  measured range and both the stored and required frequencies, and name
  `EDDY_CALIBRATE_Z T=<n>` as the fix, since the usual cause is that the coil or
  the switch moved.
- `EDDY_CALIBRATE_OFFSET T=<n>` for `n > 0` with no baseline this session: the
  existing message, which names `EDDY_CALIBRATE_OFFSET T=0`.
- Any failure inside a sweep: name the tool, the phase, and the original error.
- Non-cartesian kinematics, that is `axis_minimum`/`axis_maximum` absent from
  the kinematic status: refuse, as `_get_target_position` does.
- `apply_offsets_gcode` referencing `offset_z` while `calibrate_z` is False:
  the template rendering raises on the undefined name, and that error is wrapped
  with the tool number and a line naming `calibrate_z`.

### Removals

- The `z_offset_mode` config option, its constants
  `Z_OFFSET_MODE_IDENTICAL`, `Z_OFFSET_MODE_MIXED`, `Z_OFFSET_MODES`, and its
  load-time validation.
- `_require_anchor_mode`, `_unhandled_mode_error`, `_measures_z`,
  `_z_rows_identical`, and the mode dispatch inside `_z_rows`.
- The `EDDY_SET_Z_REF` command, its help text and its registration.
- The `EDDY_CALIBRATE_TOOL` command name, renamed to `EDDY_CALIBRATE_OFFSET`.
  The old name is not kept as an alias: two names for one command is a second
  place the behaviour has to be documented, and a rename that fails loudly is
  easier to act on than one that works silently until it is removed.
- `_required_tool_index`, whose only job was to reject a missing `T=`. The
  missing `T=` is now a meaningful request.
- The `z_ref_t<n>` config options and their parsing loop, replaced by the state
  file loader. The load-time rejection of `z_ref_t<n>` under the identical mode
  is replaced by an unconditional rejection with a different message.
- The `"not available"` Z rows in `_z_rows_mixed`, which the pre-flight anchor
  check makes unreachable, and the `EDDY_SET_Z_REF` references inside
  `_z_crossing_at_anchor`.
- The `z_offset_mode`, `EDDY_SET_Z_REF` and `EDDY_CALIBRATE_TOOL` sections of
  `docs/design.md`, and its `csv_dir` default.

`z_curve_shared_reference` is explicitly not removed; see the reuse list.

### Test plan

Pure math, unit-testable with no hardware:

- Press aggregation: discard-first and median-of-three over hand-built press
  lists with known answers, and the tolerance check firing at a known spread.
  The oracle is the literal list, not a call into the aggregation.
- Anchor construction against a synthetic curve of known analytic form: assert
  the returned anchor height equals `curve midpoint - trigger Z` and the anchor
  frequency equals the analytic value at the midpoint.
- Anchor evaluation: on the same synthetic curve, the crossing at the stored
  frequency returns the midpoint, so the reconstructed trigger plane returns the
  trigger Z it was built from.
- End-to-end offset arithmetic with an independent oracle: build tool B's curve
  as tool A's curve translated by a chosen delta and its trigger Z translated by
  the same delta, anchor both, then assert the reported Z offset equals that
  delta. Build a second case where B's curve is translated by a different amount
  from its trigger Z, which is the mixed-hotend case, and assert the reported
  offset still equals the trigger-Z delta. The expected values come from the
  construction, never from the code under test.
- Anchor out of range: a stored frequency outside a supplied curve raises, and
  the message carries the range.
- State file round trip: write a state dict, read it back, assert equality;
  assert a truncated or unparseable file raises; assert an unknown `version`
  raises; assert an unknown field inside an anchor is tolerated.
- Tool sweep list: the tool order derived from `tool_count` is `0 .. n-1`, and
  the derivation raises when `tool_count` is absent. This is a pure list
  function, so the loop's ordering is testable without a printer.
- Config validation: `z_offset_mode` present raises, `z_ref_t2` present raises,
  `calibrate_z: True` alone loads, `tool_count` above `MAX_TOOLS` raises,
  `csv_dir` colliding with the state file directory raises.
- Status dict shape: given a stubbed results and baseline state, assert the
  keys, the string tool keys, the `None` Z fields under `calibrate_z: False`,
  and that the offsets equal the printed rows' values.

Hardware, on the owner's printer:

- Switch repeatability: run `EDDY_CALIBRATE_Z T=0` ten times without a
  toolchange and report min, max and stddev of the trigger Z and of the anchor
  height. This is a self-consistency figure, not an accuracy figure.
- No-trigger and pre-triggered paths, provoked deliberately.
- Cross-check the anchored Z offset for a tool pair against the paper test, per
  the validation ladder in `docs/design.md`.
- Cross-session check: anchor two tools, restart the printer, re-run
  `EDDY_CALIBRATE_OFFSET` for both and compare against a fresh switch-only
  comparison of the same pair. This is the test that decides whether the state
  file's promise holds.
- Fleet runs end to end on the real toolchanger: `EDDY_CALIBRATE_Z` with no
  `T=`, then `EDDY_CALIBRATE_OFFSET` with no `T=`, including a deliberately
  failed toolchange partway through to confirm the abort leaves the machine
  clear, names the right tool, and keeps the anchors already written.

### Migration notes

- A config carrying `z_offset_mode` or any `z_ref_t<n>` fails to load with a
  message naming the replacement. Nothing is auto-converted: a `z_ref_t<n>`
  value is a machine Z anchored to a coil position and a paper test, and the new
  anchor is a height above a switch trigger plane, so there is no arithmetic
  that turns one into the other without the switch measurement that defines it.
  The message says exactly that and names `EDDY_CALIBRATE_Z T=<n>`.
- Macros calling `EDDY_CALIBRATE_TOOL` fail with klippy's unknown-command error
  after the rename. The README and the config reference carry the new names, and
  the change is called out in the release notes.
- `csv_dir` defaults to a new path. An owner who set it explicitly keeps their
  setting; the only requirement the plugin enforces is that the state file and
  the CSV directory are not the same directory, which the new default guarantees
  but an explicit `csv_dir: EddyToolCalibration` would break. That case is a
  `config.error` naming the collision.
- Owners who ran `EDDY_SET_Z_REF` re-anchor with `EDDY_CALIBRATE_Z` once per
  tool. There is no migration path without a switch, which is the direct
  consequence of removing the manual anchor.

### Open questions

None. The remaining unknowns are hardware measurements on the validation ladder,
not design choices: whether the anchor frequency survives a session change
within tolerance, and what press-to-press spread the owner's switch actually
achieves against the 0.02 mm default.
