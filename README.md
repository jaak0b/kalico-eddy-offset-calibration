# eddy_tool_calibration [![License: GPL v3](https://img.shields.io/badge/license-GPLv3-blue.svg)](LICENSE)

A Kalico and Klipper plugin that accurately measures per-tool XYZ nozzle offsets on a toolchanger, even when the nozzle is dirty.

Uses a bed-mounted LDC1612 eddy-current coil (e.g. BTT Eddy Coil/Duo/USB or a Cartographer flashed with Klipper firmware).

[![A calibration run on my printer](https://img.youtube.com/vi/lr-eFiMrt0E/hqdefault.jpg)](https://www.youtube.com/watch?v=lr-eFiMrt0E)

## What you need

- A toolchanger running Kalico or Klipper.
- An LDC1612 board (e.g. BTT Eddy Coil/Duo/Usb or Cartographer flashed with Klipper firmware) mounted to the bed, coil facing up.
- For Z offsets: a contact switch (sexbolt, sexball, any fixed Z endstop).
- Your own toolchange and offset-apply gcode to change tools and apply offsets. This plugin is universal and it is not targeting any specific toolchange plugin.

## How accurate is it?

These numbers come from my printer, a Voron 2.4 with StealthChanger and a BTT
Eddy Coil, measured with `EDDY_REPEATABILITY`.

![Screenshot of EDDY_REPEATABILITY output](/assets/images/repeatability.png)

## Getting started

1. Wire up the eddy coil. See your probe's manual.
2. Mount the Eddy Coil Sensor to the bed with the coil facing up. [BTT Eddy Mount.stl](BTT%20Eddy%20Mount.stl) can be used for BTT Eddy probes.
3. Install the plugin, see [Install](#install).
4. Add the config, see [Required](#required).
5. Run [`EDDY_QUERY`](#commands) to check the wiring before anything moves.
6. Run [`LDC_CALIBRATE_DRIVE_CURRENT CHIP=eddy_tool_calibration`](#sensor-hardware). Only needed on non-BTT EDDY boards.
7. Jog the nozzle over the coil until the paper drags on the coil top face,
   then put the X, Y and Z position into `coil_x`, `coil_y` and
   `coil_z`.
8. Run [`EDDY_LOCATE`](#commands), and put the values into `coil_x` and `coil_y`.
9. For Z offsets (Optional): jog the nozzle over the contact switch (Sexball, Sexbolt or any other fixed mounted z homing switch), put those values
   into `switch_x`, `switch_y` and `switch_probe_z_start`, and set
   `calibrate_z: True`.
10. For Z offsets (Optional): Run [`EDDY_CALIBRATE_Z`](#commands) once per tool. Rerun it after a nozzle change.
11. Run [`EDDY_CALIBRATE_OFFSET`](#commands).
12. Run [`EDDY_REPEATABILITY T=0 RUNS=5 CYCLES=3`](#commands) to check the
    numbers are stable.

## Install

Requires Kalico or stock Klipper v0.13.0 or newer, an LDC1612 board
reachable over I2C (e.g. BTT Eddy Coil/Duo/Usb or Cartographer flashed with Klipper firmware) and Python 3 with no third-party packages.

```
cd ~
git clone https://github.com/jaak0b/kalico-eddy-offset-calibration
sh ~/kalico-eddy-offset-calibration/install.sh
sudo service klipper restart
```

Update manager entry for moonraker.conf:

```
[update_manager eddy_tool_calibration]
type: git_repo
path: ~/kalico-eddy-offset-calibration
origin: https://github.com/jaak0b/kalico-eddy-offset-calibration
primary_branch: main
is_system_service: False
```

## Config reference

Uncommented options are required and have no default, everything commented out may be left out. The blocks below are all parts of the same `[eddy_tool_calibration]` section.

### Required

```
[eddy_tool_calibration]
i2c_software_scl_pin:
i2c_software_sda_pin:
#   The SCL and SDA pins of the software i2c bus the LDC1612 chip is wired
#   to. This parameter must be provided.
coil_x:
coil_y:
#   Approximate machine X and Y (in mm) of the coil center. This parameter
#   must be provided.
coil_z:
#   Machine Z (in mm) of the coil top face. This parameter must be
#   provided.
coil_inner_diameter:
#   Bore (in mm) of the sensing coil. Must be greater than 0. It sets the
#   default fit_window_radius and scan_length. This parameter must be
#   provided.
```

### Toolchanger

Both tool_count and toolchange_gcode are needed only to run a calibration
command without T=. With toolchange_gcode set, both calibration commands
mount the tool they are about to work on, with T= as well as without it;
without it they work on whatever tool is already mounted. Every
non-baseline result is passed through apply_offsets_gcode as the run
proceeds.

```
#tool_count:
#   The number of tools on the machine, 1 to 16. Tools are T0 through
#   T(tool_count-1) with no gaps.
#toolchange_gcode:
#   The gcode that mounts a tool, as a template with {tool} bound to the
#   tool number.
#apply_offsets_gcode:
#   The gcode that applies a measured offset, as a template with {tool},
#   {offset_x} and {offset_y} bound, and {offset_z} bound as well when
#   calibrate_z is True. The default is to only report the offsets.
#tool_extruders:
#   Comma separated heater section names, one per tool, in tool number
#   order. Names are resolved at startup, so a name that does not exist is
#   a startup error. The default is to assume T0 uses extruder, T1 uses
#   extruder1 and so on.
```

### Z offset via the contact switch

> [!IMPORTANT]
> The options below `calibrate_z` are ONLY required when `calibrate_z` is True.

> [!IMPORTANT]
> When `calibrate_z` is True, `EDDY_CALIBRATE_Z` must be run at least once, otherwise `EDDY_CALIBRATE_OFFSET` will refuse to measure the Z offset.

```
#calibrate_z: False
#   Set to True to measure Z. When set to True, `EDDY_CALIBRATE_Z` must be
#   run at least once.
#   The default is False.
switch_pin:
#   The endstop pin the contact switch is wired to. The switch itself is a plain
#   normally-open endstop switch; sexbolt and sexball style Z endstops work
#   well. The pin may be shared with an existing
#   [tools_calibrate] section.
#   This parameter must be provided.
switch_x:
#   The machine X position (in mm) the nozzle presses the switch at.
#   This parameter must be provided.
switch_y:
#   The machine Y position (in mm) the nozzle presses the switch at.
#   This parameter must be provided.
switch_probe_z_start:
#   The machine Z position (in mm) where each press starts from. Set it just above the
#   switch.
#   This parameter must be provided.
#switch_probe_speed: 5.0
#   Speed (in mm/s) of a downward press onto the switch. The default is
#   5.0mm/s.
#switch_probe_lift_speed:
#   Speed (in mm/s) of the retract between presses. The default is
#   switch_probe_speed.
#switch_probe_max_travel: 4.0
#   Distance (in mm) a press may travel down before the run is reported as
#   a missing trigger. The default is 4.0mm.
#switch_probe_sample_retract_dist: 2.0
#   Distance (in mm) the nozzle retracts after each press. Each press
#   starts from where the previous one retracted to, so this must be less
#   than switch_probe_max_travel. The default is 2.0mm.
#switch_probe_tolerance: 0.020
#   Distance (in mm) the three counted presses may disagree by before the
#   probing is reported as failed. The default is 0.020mm.
#calibration_temp: 150.0
#   Nozzle temperature (in Celsius) that every calibration measurement is
#   taken at. It must be above 0 when calibrate_z is True. The default is
#   150.0.
#calibration_temp_band: 2.0
#   Band (in Celsius) around the target temperature a nozzle has to read
#   within before the settle dwell starts. It applies both ways, so a tool
#   that has to cool only has to fall inside it. The default is 2.0.
#calibration_settle_time: 30.0
#   Dwell (in seconds) after a tool reaches the band, before measuring.
#   Both calibration commands dwell the same time, so both measure the
#   same thermal state. The default is 30.0 seconds.
#z_start: 2.5
#   Height (in mm) above the coil top face the downward Z move starts
#   from. The default is 2.5mm.
#z_stop: 0.5
#   Height (in mm) above the coil top face the downward Z move ends at.
#   Must be above the face, and below z_start. The default is 0.5mm.
#z_step: 0.05
#   Step size (in mm) of the downward Z move. Must be greater than 0, and
#   must divide the span from z_start to z_stop into a whole number of
#   steps. The default is 0.05mm.
```

### Sensor hardware

```
#i2c_address:
#i2c_mcu:
#i2c_bus:
#i2c_speed:
#   The remaining i2c settings for the LDC1612 chip. See the "common I2C
#   settings" section of Kalico's Config_Reference.md for a description of
#   these parameters. The chip's factory address is 42 decimal (0x2A).
#intb_pin:
#   The MCU gpio pin connected to the LDC1612 sensor's INTB pin, if it is
#   broken out. The default is to not use the INTB pin.
#frequency:
#   The external clock frequency (in Hz) fed to the LDC1612 CLKIN pin,
#   accepted from 2000000 to 40000000. It requires Kalico from March 2026,
#   or stock Klipper. The default is 12000000, which is correct for the
#   BTT Eddy family.
#reg_drive_current:
#   The LDC1612 DRIVE_CURRENT0 register value, 0 to 31. The default is 15,
#   which suits the BTT Eddy coil probe. For any other coil determine the right value with
#   `LDC_CALIBRATE_DRIVE_CURRENT CHIP=eddy_tool_calibration` and store the
#   printed value with SAVE_CONFIG. If a scan fails with a sensor
#   amplitude error ("Eddy current sensor error"), raise this value by one
#   and retry.
```

### Scan and fit tuning

```
#scan_height: 1.0
#   Height (in mm) above the coil top face the XY scan passes run at. Must
#   be above the face and below z_start. The default is 1.0mm.
#scan_safe_z: 2.0
#   Extra clearance (in mm) added above the scan height for travel moves.
#   Must be greater than 0. The default is 2.0mm.
#scan_speed: 4.0
#   Speed (in mm/s) of an XY scan pass. Lower it if a pass returns fewer
#   than samples_min samples. The default is 4.0mm/s.
#scan_length:
#   Length (in mm) of an XY scan pass. The default is 1.5 times
#   coil_inner_diameter: 12.0 mm for the 8 mm BTT Eddy Coil bore, 3.0 mm
#   for the 2 mm Little Crab bore.
#locate_scan_length:
#   Length (in mm) of the coarse EDDY_LOCATE pass. The default is three
#   times scan_length.
#travel_speed: 100.0
#   Speed (in mm/s) of XY travel moves between passes. The default is
#   100.0mm/s.
#z_speed: 10.0
#   Speed (in mm/s) of every Z leg except the switch presses, which use
#   switch_probe_speed and switch_probe_lift_speed. The default is
#   10.0mm/s.
#scan_angles: 45, 135
#   Comma separated scan directions (in degrees), where 0 runs along X+
#   and 90 along Y+. Two directions at least 30 degrees apart are needed
#   to get both axes. A repeated angle is a config error, and so is a pair
#   of opposite angles when pair_scans is enabled. The default is 45, 135.
#pair_scans: True
#   Set to True to scan the opposite of every configured angle as well and
#   average each pair, which cancels the shift each pass picks up along the
#   direction it travels. Pairing doubles the number of passes. The default
#   is True.
#samples_min: 100
#   Minimum usable samples per scan pass, at least 3. A pass below it is
#   reported as an error. The default is 100.
#query_time: 0.5
#   Time (in seconds) EDDY_QUERY collects samples for, at the driver's
#   250 Hz rate (400 Hz on Klipper master). The default is 0.5 seconds,
#   about 125 samples.
#freq_min: 1000000.0
#   Frequency (in Hz) below which samples are discarded as startup or
#   noise readings. The default is 1000000.0.
#edge_margin: 0.15
#   Fraction of each pass treated as its edge, above 0 and below 0.5. The
#   edges are left out of the search for the peak, and are used to tell
#   whether the pass peaks up or down. The default is 0.15.
#fit_window_radius:
#   Half width (in mm) of the sample window either side of the peak that
#   the curve is fitted to. The default is half of coil_inner_diameter.
#fit_sigma_fraction: 0.5
#   Standard deviation of the fit's weighting, as a fraction of the fit
#   window. It makes samples near the peak count for more than samples at
#   the window's edge. The default is 0.5.
#fit_vertex_limit: 0.5
#   Maximum distance between the fitted peak and the peak sample, as a
#   fraction of the fit window. A fit landing beyond it is reported as
#   failed rather than pulled back into range. The default is 0.5.
```

### Logging

```
#save_history: True
#   Set to True to append every completed measurement of a tool to
#   history_T<n>.csv in log_dir: the UTC timestamp, the command, the
#   fitted center, the offsets, the session of the baseline they were
#   measured against, the Z crossing and the height where the switch
#   clicks, the nozzle temperature the run was held at, the nozzle reading
#   observed while it ran, and the sample count. The default is True.
#save_csv: False
#   Set to True to write every scan pass's raw samples to a CSV file for
#   offline review. The default is False.
#log_dir: EddyToolCalibration/logs
#   Directory the drift logs and the repeatability study files are written
#   to, read against the printer config directory unless it is an absolute
#   path. These are the durable record. The default is
#   EddyToolCalibration/logs.
#csv_dir: EddyToolCalibration/data
#   Directory the raw scan dumps of save_csv are written to, read against
#   the printer config directory unless it is an absolute path. These are
#   working files, meant to be cleared once they have been looked at. The
#   default is EddyToolCalibration/data.
```

### Integration with Klipper Toolchanger plugin


Append the following config reference to `[eddy_tool_calibration]` for integration with [viesturz's klipper-toolchanger](https://github.com/viesturz/klipper-toolchanger)
```ini
toolchange_gcode:
    T{tool}
apply_offsets_gcode:
    SET_TOOL_PARAMETER T={tool} PARAMETER=gcode_x_offset VALUE={offset_x}
    SET_TOOL_PARAMETER T={tool} PARAMETER=gcode_y_offset VALUE={offset_y}
    SET_TOOL_PARAMETER T={tool} PARAMETER=gcode_z_offset VALUE={offset_z}
```

Append the following config reference to `[eddy_tool_calibration]` for integration with [klipper-toolchanger-hard](https://github.com/Contomo/klipper-toolchanger-hard)
```ini
toolchange_gcode:
    T{tool}
apply_offsets_gcode:
    SET_TOOL_OFFSET T={tool} X={offset_x} Y={offset_y} Z={offset_z}
```

## Supported hardware

Any eddy stlye probe with a LDC1612 works as long as it is flashed with stock Klipper firmware and reachable over I2C.

| Board |  |
|---|---|
| BTT Eddy Coil | Conected directly to the mainboard or a toolheadboard |
| BTT Eddy USB | Expected to work unmodified; I have not tried one. Its RP2040 runs standard Klipper firmware as a second MCU |
| BTT Eddy Duo | Expected to work in USB mode only. |
| [chengxg "Little Crab" dual-coil board](https://oshwhub.com/cxg01/project_lbabffjk) | The board this plugin's algorithm comes from; a sharper XY signal than the BTT coils. Mine are still being assembled, so unmeasured here |
| Cartographer | Only works if the board is flashed with Klipper firmware. Cartographer's own firmware will not work |

### BTT Eddy Coil

```ini
[eddy_tool_calibration]
i2c_mcu: mcu
i2c_software_scl_pin: 
i2c_software_sda_pin: 
i2c_address: 42
reg_drive_current: 15
coil_inner_diameter: 8.0
```

### BTT Eddy USB/DUO

```ini
[mcu eddy]
serial: /dev/serial/by-id/usb-Klipper_rp2040_XXXXXXXXXXXX-if00

[eddy_tool_calibration]
i2c_mcu: eddy
i2c_bus: i2c0f
i2c_software_scl_pin: 
i2c_software_sda_pin: 
i2c_address: 
reg_drive_current: 15
coil_inner_diameter: 8.0
```

### Little Crab

```ini
[eddy_tool_calibration]
i2c_mcu: mcu
i2c_software_scl_pin: 
i2c_software_sda_pin: 
frequency: 24000000
coil_inner_diameter: 2.0
reg_drive_current:
```

Sources: [upstream repository](https://github.com/chengxg/tool_eddy_calibration),
[EasyEDA project](https://oshwhub.com/cxg01/project_lbabffjk).

## Commands

> [!NOTE]
> `T=` takes one tool number or a comma separated list with no spaces (`T=0,1,2`). Leaving `T=` out runs every tool and needs `tool_count`; any run covering more than one tool needs `toolchange_gcode`.

### EDDY_QUERY

`EDDY_QUERY`: Print statistics of the sensor frequency over `query_time` seconds without motion. Use it as a wiring check.

### LDC_CALIBRATE_DRIVE_CURRENT

`LDC_CALIBRATE_DRIVE_CURRENT CHIP=eddy_tool_calibration` prints the correct `reg_drive_current` for the connected sensor.

### EDDY_LOCATE
`EDDY_LOCATE [DEBUG=1]`: Measures the coil center precisely and stages it as `coil_x` and `coil_y`.

### EDDY_CALIBRATE_Z

> [!NOTE]
> Requires `calibrate_z: True`

`EDDY_CALIBRATE_Z [T=<list>] [DEBUG=1]`: Correlates the sensor's readings with the nozzle's actual Z height using a z endstop/microswitch.

Must be run once per tool. Rerun the command when the toolhead (e.g. the nozzle, hotend) changes.

Results are written to calibration_state.json directly.

### EDDY_CALIBRATE_OFFSET

> [!NOTE]
> T0 is always measured after a Klipper restart. 

`EDDY_CALIBRATE_OFFSET [T=<list>] [DEBUG=1]`: Measure the listed tools offset relative to T0. Prints the offset in the console. Runs `apply_offsets_gcode` when provided. 

### EDDY_REPEATABILITY

`EDDY_REPEATABILITY T=<tool> RUNS=<n> CYCLES=<n> [SKIP_Z=1] [DEBUG=1]`: Measures offset calibration repeatedly.
`RUNS=<n>` measurements taken back to back without touching the tool. Shows the measurement's own noise.
`CYCLES=<n>` how many times to swap the tool away and back before taking another set of runs, needs tool_count and toolchange_gcode, without them nothing is docked and the summary says so.
`SKIP_Z=0` measures Z as well, and needs the tool's EDDY_CALIBRATE_Z result. The default is 1, XY only.

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
| `anchors` | what EDDY_CALIBRATE_Z stored, per tool; survives a restart. Each carries `anchor_height` (mm above the height where the switch clicks), `anchor_frequency` (Hz), `setpoint_temperature` (the temperature later runs heat to), `observed_temperature`, `trigger_z` (machine Z where the switch clicks) and `updated` (UTC) |
| `tools` | this session's measurements; a baseline replacement removes the measurements compared against the old one, so an offset here is never compared against a baseline that has moved. Each carries `offset_x/y/z` (mm; all `null` for the baseline itself, `offset_z` `null` when Z was not measured, and a `null` is never a zero), `center_x/y` (machine coordinates), `z_crossing` (machine Z where the downward move reached `anchor_frequency`), `session_id` and `measured_time` (host monotonic clock) |

## Measuring before each print

> [!IMPORTANT]
> Requires all [Toolchanger](#toolchanger) config options to be set.

> [!IMPORTANT]
> The macro assumes that your slicer passes tool temperature as `{if is_extruder_used[0]}T0_TEMP={first_layer_temperature[0]}{endif}` (Example for T0, use 1 for T1, 2 for T2,..) in your slicers machine start-gcode.

To calibrate each used tool before a print use the below macro and call `EDDY_CALIBRATE_USED_TOOLS {rawparams}` from your print_start macro.

```
[gcode_macro EDDY_CALIBRATE_USED_TOOLS]
description: Calibrate offsets for the tools this print actually uses
gcode:
    {% set sx = printer.toolhead.position.x %}
    {% set sy = printer.toolhead.position.y %}
    {% set sz = printer.toolhead.position.z %}
    {% set used = [] %}
    {% for key in params %}
        {% if key.startswith('T') and key.endswith('_TEMP') and key[1:-5].isdigit() %}
            {% set _ = used.append(key[1:-5]|int) %}
        {% endif %}
    {% endfor %}
    {% if used|length > 1 %}
        {% set tools = ([0] + used)|unique|list|sort %}
        EDDY_CALIBRATE_OFFSET T={tools|join(',')}
    {% endif %}
    G90
    G1 X{sx} Y{sy} F6000
    G1 Z{sz} F1200
```

## License

GNU GPLv3, see [LICENSE](LICENSE). The algorithms are reimplemented from
chengxg's GPLv3
[`tool_eddy_calibration`](https://github.com/chengxg/tool_eddy_calibration)
rather than copied from it, which makes this a derivative work under the
same license. His original file is kept unmodified in `reference/`.
