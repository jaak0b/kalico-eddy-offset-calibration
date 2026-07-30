# EddyNozzleProbe plugin design

Status: draft for owner review. Target: Kalico plugin (`klippy/plugins/`), GPLv3.

## Scope

One plugin, one config section, calibrating per-tool XYZ nozzle offsets against a
bed-mounted LDC1612 eddy sensor board. Offsets are reported to the console only;
no toolchanger integration or persistence in v1.

Out of scope for v1: channel-2 (big coil) support, pressure-advance experiments,
Z homing/bed meshing (Kalico's probe_eddy_current already covers that use case),
automatic offset application.

## Module layout

```
eddy_nozzle_probe/
  eddy_tool_calibration.py    # the plugin (single module, ~600-800 lines)
  reference/
    tool_eddy_calibration.py  # upstream file, unmodified, for algorithm reference
  install.sh                  # symlink into ~/kalico/klippy/plugins/
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
scan_height: 1.0                # nozzle height above coil top during XY scans
scan_safe_z: 2.0                # mm above the scan height for travel moves
z_start: 5.0                    # Z descent start for Z curves
z_stop: 0.5                     # closest approach (never touches)
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
# --- fit tuning ---
fit_window_radius: 1.0          # mm each side of the extremum; coil_inner_diameter / 2
fit_sigma_fraction: 0.5         # Gaussian weight sigma as a fraction of the window
fit_vertex_limit: 0.5           # reject a vertex past this fraction of the window
edge_margin: 0.15               # fraction of each pass treated as edge
freq_min: 1000000.0             # Hz; samples below this are discarded as noise
# --- per-tool Z anchors, written by EDDY_SET_Z_REF ---
z_ref_t0: 0.2000:12345678.000   # "<z>:<reference frequency>", one per tool 0 to 15
```

Display precision in every readout: millimetres to 4 decimals, frequencies to
3 decimals. Values are printed exactly as measured at that precision, never
rounded further and never clamped.

The plugin instantiates Kalico's `extras.ldc1612.LDC1612` directly with its own
config wrapper, so users need no separate `[ldc1612]` section. (Fallback design
if the wrapper fights us: require a named `[ldc1612 eddy_cal]` section and
reference it; decide during implementation, wrapper preferred.)

## Commands

- `EDDY_QUERY`: print current frequency, sanity check wiring.
- `EDDY_LOCATE`: coarse raster over the configured coil position, finds and
  stores the refined coil center for the session; prints it.
- `EDDY_CALIBRATE_TOOL [T=<n>]`: full XY(+Z) measurement for the mounted tool:
  1. XY: for each configured angle, scan through the current center estimate,
     forward and reverse; parabolic fit of the response extremum per pass;
     average pairs; least-squares reconstruct the center from the two
     directional results; iterate once more at the refined center.
  2. Z: hold the XY center, descend z_start -> z_stop stepwise (probe_eddy_current
     calibration-move pattern: step, dwell, window-average samples), producing a
     freq-vs-Z curve; report the Z at which frequency crosses the tool's stored
     reference frequency (see EDDY_SET_Z_REF). Without a reference, prints the
     curve fit only.
  3. Print labeled results: raw center, and offsets relative to the stored T0
     baseline if one exists in this session.
- `EDDY_SET_BASELINE`: declare the currently mounted tool as T0 baseline
  (stores its center + Z curve for the session).
- `EDDY_SET_Z_REF [T=<n>] Z=<real_offset>`: one-time per-tool anchor: after the
  owner measures true Z by their existing method (paper/pin), this binds the
  measured frequency curve to reality; stored in memory and printed so the owner
  can persist it in config (`z_ref_t<n>:` option) for reuse.
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
  their own anchor).
- Material independence for XY comes free: symmetry center of any monotonic
  response is amplitude-invariant.

## Error handling

Every failure path raises a gcode error with an actionable message: no samples
(wiring/I2C), too few samples (scan too fast/short), fit residual too large
(dirty coil area, wrong height, metal nearby), no extremum inside scan window
(coil center estimate off: run EDDY_LOCATE), sensor amplitude errors from the
LDC1612 status register (drive current miscalibrated).

## Validation plan

1. Bring-up on BTT Eddy Coil (bigger coil, same electronics): EDDY_QUERY,
   EDDY_LOCATE, scan curves visually sane (save_csv + offline plot).
2. Repeatability: EDDY_CALIBRATE_TOOL x10 on one tool without toolchange;
   report min/max/stddev per axis. Target: XY stddev < 5 um on crab board.
3. Cross-check vs contact method (tools_calibrate / owner's pin) on 2+ tools;
   agreement within the contact method's own repeatability.
4. Dirty-nozzle test: repeat (2) with deliberately filthy nozzle; deltas vs
   clean runs are the headline metric of the whole project.
5. Kalico-update smoke: EDDY_QUERY + one EDDY_CALIBRATE_TOOL after each update.
