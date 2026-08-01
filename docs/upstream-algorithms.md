# Upstream algorithm extraction (research notes)

Source: subagent read of `reference/tool_eddy_calibration.py` (chengxg, GPLv3),
`kalico/klippy/extras/probe_eddy_current.py`, `kalico/klippy/extras/ldc1612.py`.
Verbatim extraction for the implementation spec; line numbers refer to those files.

## A. Upstream XY flow (tool_eddy_calibration.py)

**Overall sequence** (`EddyCalibrator.run_calibration`, lines 315-382):
1. `_ensure_homed()` (518-526): requires X, Y, Z homed via `kin_status['homed_axes']`, else gcode error.
2. Determine starting center: reuse `originX/originY` from `last_result` if nonzero (330), else `CENTER_XY` override (339), else config `coil_x/coil_y` (345).
3. If no stored origin: pre-scan pass using `pre_dir_angles` (default = `DIR`, itself defaulting to `"45,135"`), `repeats=1`, result stored as `originX/originY` (356-369).
4. If `PAIR_CANCEL=1`: auto-expand `dir_angles` to include each angle plus its +180 degree opposite, dedup via a set (372-377).
5. Main scan pass at the (pre-scan-refined) center, with `dir_angles`, `repeats=REPEATS` (default 1, max 10) (379-380).

**Per-direction scan** (`_scan_direction`, 528-569):
- `half_len = scan_length/2` (default 10 mm, range 2-20).
- Endpoints: `start = center - half_len*(cos t, sin t)`, `end = center + half_len*(cos t, sin t)`, t = `angle_deg` in degrees, 0 deg = X+, 90 deg = Y+.
- `effective_scan_height = scan_height (default 0.5, range -20..50) + scan_height_offset (gcode param, default 0)`; clamped to >= 0 relative to coil (554: if negative, warn and clamp to 0).
- `scan_z = coil_z + effective_scan_height`; `safe_z = scan_z + scan_safe_z` (default 2.0, range 0.5-10).
- Move sequence: to `(start_x, start_y, safe_z)` then down to `(start_x, start_y, scan_z)` then collect scan data while moving to `(end_x, end_y, scan_z)` then raise to `(end_x, end_y, safe_z)`.
- `_move_to_position` (571-577): X then Y (fast, `travel_speed_xy`, default 100 mm/s) then Z (slow, `travel_speed_z`, default 10 mm/s), each `manual_move` then `wait_moves()`.

**Sample collection** (`_collect_scan_data`, 579-662):
- `distance = |end - start|`; `scan_time = distance / scan_speed` (default 5.0 mm/s, range 0.5-20).
- Registers a `data_callback` via `sensor.add_client()` before the move, to avoid missing data.
- `reactor.pause(now + 0.05)` settle wait, then `toolhead.dwell(0.010)`.
- `move_start_print_time = toolhead.get_last_move_time()` is the reference instant for mapping timestamps to position.
- Executes the scan move at `scan_speed`, `wait_moves()`, then an extra `reactor.pause(now+0.2)` to catch trailing data.
- Timestamp-to-position mapping is linear interpolation along the constant-velocity nominal move, NOT `motion_report.get_trapq_position`:
  - `relative_time = samp_time - move_start_print_time`
  - reject if `relative_time < -0.05` or `> scan_time + 0.05` (skipped_before/after counters)
  - reject if `relative_time < 0` (redundant second check)
  - `t = relative_time / scan_time`; `x = start_x + t*(end_x-start_x)`, `y = start_y + t*(end_y-start_y)`. Assumes perfectly constant velocity across the whole move (no acceleration compensation), unlike Kalico's own `get_trapq_position`.
- Sample validity filter: `freq < 1_000_000` Hz treated as invalid/noise and dropped (hard-coded threshold, same one used in `TEST_LDC1612`).
- Warns if `len(samples) < 10` but does not abort here (abort happens later in `_find_peak`).

**Peak-type auto-detection** (`_auto_detect_peak_type`, 664-686):
- Edge region = first and last `n//7` samples (~14.3%, comment says 15%); center region = middle 30% (index `0.35n` to `0.65n`).
- `detected = 'peak' if center_avg > edge_avg else 'valley'`. Re-detected every scan (always auto, no config).

**Extremum search** (`_find_peak`, 688-732):
- Requires `len(samples) >= 10`, else returns `(None, None, None)` (caller then raises).
- Margin exclusion: `margin = len(freq_data)*15//100`; search over `[max(1,margin), len-margin)`.
- Linear scan for min (valley) or max (peak) within that window; gives `best_idx`, `best_freq`.
- Passes to `_refine_peak_position` for sub-sample refinement.

**Parabolic sub-sample fit** (`_refine_peak_position`, 759-890), weighted least squares, Gaussian-weighted:
- Window sizing: `effective_radius = (coil_inner_diameter - 0.5)/2` mm (default coil_inner_diameter=2.0 gives radius 0.75 mm); `samples_per_mm = sps/scan_speed`; `half_window = int(samples_per_mm * effective_radius)`. Fallback `half_window=50` if `sps<=0` or `scan_speed<=0`.
- Window = `samples[peak_idx-half_window : peak_idx+half_window+1]`; if fewer than 3 points, returns raw peak sample unfitted.
- Local coordinate: `x_norm = i - peak_local_idx` (integer sample offsets, not mm).
- Gaussian weight: `sigma = half_window/2`; `w_i = exp(-(x_norm_i)^2 / (2*sigma^2))`.
- Fits y = a*x^2 + b*x + c by weighted least squares via 3x3 normal equations (Cramer's rule) using weighted power sums `w_sum, w_sum_x .. w_sum_x4`, `w_sum_y, w_sum_xy, w_sum_x2y` (810-843):

```
det   = wx4*(wx2*w - wx*wx) - wx3*(wx3*w - wx*wx2) + wx2*(wx3*wx - wx2*wx2)
det_a = wx2y*(wx2*w - wx*wx) - wxy*(wx3*w - wx*wx2) + wy*(wx3*wx - wx2*wx2)
det_b = wx4*(wxy*w - wx*wy) - wx3*(wx2y*w - wx*wy) + wx2*(wx2y*wx - wxy*wx2)
det_c = wx4*(wx2*wy - wx*wxy) - wx3*(wx3*wy - wx*wx2y) + wx2*(wx3*wxy - wx2*wx2y)
a = det_a/det; b = det_b/det; c = det_c/det
```

  Degenerate if `abs(det) < 1e-10`: returns raw peak sample.
- Weighted R^2 computed and logged (845-852), diagnostics only, not a rejection gate.
- Concavity sanity check (854-865): if `abs(a) < 1e-10` (flat) use raw sample. If detected type is valley but fit opens downward (`a<0`), or peak but opens upward (`a>0`), reject fit, use raw peak sample.
- Vertex: `x_peak = -b/(2a)`, clamped to `+/- half_window*0.5` (867-871).
- Converts `x_peak` (fractional sample index) back to global window index, linear-interpolates the actual `(x,y)` sample coordinates between the two bracketing window samples (877-890).

**Multi-direction center reconstruction**, two exclusive modes selected by `PAIR_CANCEL`:

LSQ mode (`_compute_center_lsq`, 430-461), default: each direction's peak point is projected onto its own scan axis: `proj_i = peak_x*cos t_i + peak_y*sin t_i`. Solve overdetermined system `tx*cos t_i + ty*sin t_i = proj_i` by normal equations:

```
a11 = sum(cos^2 t), a12 = sum(cos t * sin t), a22 = sum(sin^2 t)
c1 = sum(cos t * proj), c2 = sum(sin t * proj)
det = a11*a22 - a12^2
tx = (a22*c1 - a12*c2)/det
ty = (a11*c2 - a12*c1)/det
```

Degenerate (`|det|<1e-12`) fallback: crude per-axis averaging using whichever of cos/sin dominates per point (453-457); only hit with pathological angle sets.

Paired-averaging mode (`_compute_center_paired_avg`, 463-516), used when `PAIR_CANCEL=1`: groups peaks by angle mod 360; for each angle with a 180-degree opposite present, averages the two directions' projections onto the same axis (`proj_avg = (proj_fwd+proj_bwd)/2`) to cancel one-directional motion lag/backlash bias, then feeds the paired (or unpaired leftover) projections into the identical LSQ solve as above.

**Repeat averaging** (`_measure_center`, 384-428): runs `repeats` full multi-direction scans, computes `avg_x, avg_y` and population std `std_x, std_y = sqrt(sum((x-avg)^2)/n)` (n, not n-1), reported but not used as a rejection gate. No outlier rejection or convergence loop: one pre-scan pass, then one multi-repeat main pass.

**Validation/rejection checks, exhaustive list with thresholds:**
- Not homed: error (523-526).
- Peak detection returns `None`: `"Failed to detect peak in DIR=%d direction"` (397-398).
- `< 10` valid samples: `(None,None,None)` from `_find_peak`, propagates to error (696-698; respond_info at 660 mid-collection).
- Sample frequency `< 1,000,000 Hz`: dropped as invalid/startup noise (636-638).
- Sample timestamp outside `[-0.05, scan_time+0.05]` s: dropped (642-647).
- Fit window `< 3` points: skip fit, use raw sample (781-782).
- `|det| < 1e-10` (normal equations singular): skip fit (824-825).
- `|a| < 1e-10` (flat fit): skip fit (855-856).
- Wrong concavity vs detected type: reject fit, use raw sample (859-865).
- `x_peak` clamp to `+/- half_window*0.5` (869-871): hard clamp, not rejection.
- Scan height clamped to >= 0 relative to coil with warning (548-553).
- No R^2-based rejection despite computing R^2 (diagnostic-only, line 851).

## B. Upstream Z flow

Not present in tool_eddy_calibration.py. `SET_TOOL_Z` (160-187) stores an externally
supplied Z verbatim into `last_result[tool_name]['z']`. No measurement, curve, or
reference anchoring. Z is delegated entirely to external means.

## C. probe_eddy_current.EddyCalibration (Kalico)

**Data structure** (lines 20-21, 45-48): parallel sorted lists `cal_freqs` (ascending) and `cal_zpos`, built from config string `"z:freq, z:freq, ..."` via `load_calibration`: `cal = sorted([(freq, z) for z, freq in parsed_pairs])` then unzipped. `is_calibrated()` requires `>2` points (42-43).

**Frequency-to-Z interpolation** (`apply_calibration`, 50-66), applied live to every incoming sample batch: `bisect.bisect(cal_freqs, freq)` finds insertion point; out-of-range clamps to sentinel `-99.9` / `99.9`; otherwise linear interpolation between bracketing calibration points:

```
gain = (this_zpos - prev_zpos) / (this_freq - prev_freq)
offset = prev_zpos - prev_freq * gain
zpos = freq * gain + offset
```

Result rounded to 6 decimals.

**Z-to-frequency** (`height_to_freq`, 68-83): reverses both lists, `bisect.bisect(rev_zpos, height)`, raises `"Invalid probe_eddy_current height"` if out of range, otherwise mirror-image linear interpolation.

**Calibration move pattern** (`do_calibration_moves`, 85-145):
- `max_z = 4.0` mm, `samp_dist = 0.040` mm step (hard-coded), so 101 target positions 0..4.0.
- Registers `handle_batch` client before moving; initial `toolhead.dwell(1.0)` settle.
- Each target: hop up to `zpos+0.500` first (always approach from above, reduces backlash), then descend to exact `zpos`, both at `move_speed`.
- Per-step timing window: `start_query_time = get_last_move_time() + 0.050`; `end_query_time = start_query_time + 0.100`; then `toolhead.dwell(0.200)`.
- Actual Z stored is the real kinematic position after `flush_step_generation()`: `kin.calc_position(kin_spos)`, not the commanded target.
- After all steps: `dwell(1.0)`, `wait_moves()`, stop collection.
- Correlates each `(query_time, freq)` sample to a step with a monotonic pointer matching `times[step][0] <= query_time <= times[step][1]` (136-140): explicit per-step time windows.
- Requires `len(cal) == len(times)` (every step got a sample) or raises `"Failed calibration - incomplete sensor data"` (141-144).

**Averaging and validation** (`calc_freqs` 147-156, `post_manual_probe` 158-200+): per-position mean freq and pooled variance/stddev; then walks positions sorted descending by Z and asserts strict monotonic frequency increase as Z decreases (`"Failed calibration - frequency not increasing each step"`, 182-186). `post_manual_probe` first manually probes the bed (contact) at the nozzle position, offsets by the probe's configured x/y/z_offset to place the sensor over that point, runs `do_calibration_moves`, stores each calibration Z relative to `probe_calibrate_z` (pos - probe_calibrate_z, line 199). This is the contact-reference anchoring pattern.

## D. ldc1612.py

**add_client batch dict** (`_process_batch`, 249-260):

```python
{
  "data": samples,       # list of (print_time, freq_hz, z); z is 999.9 raw, or calibrated if EddyCalibration attached
  "errors": self.last_error_count,      # cumulative per-sample error-bit count, reset each _start_measurements
  "overflows": self.ffreader.get_last_overflows(),
}
```

Empty dict `{}` if no samples this batch (252-253). `BATCH_UPDATES = 0.100` s cadence, `data_rate = 250` Hz fixed.

**Raw-to-Hz conversion** (`_convert_samples`, 200-208; setup 103-109):

```python
clock_freq = config frequency (default DEFAULT_LDC1612_FREQ = 12_000_000)
sensor_div = 1 if clock_freq != 12_000_000 else 2   # "assume 12MHz is BTT Eddy"
freq_conv = float(clock_freq * sensor_div) / (1 << 28)
mv = raw_val & 0x0FFFFFFF     # mask to 28 bits; mv != raw_val increments last_error_count
freq_hz = round(freq_conv * mv, 3)
```

Crab board is 24 MHz so takes the `sensor_div=1` branch (correct, but the selection is a value-equality check against the default constant, fragile by design).

**Errors/overflows**: `last_error_count` increments per sample whose upper-nibble error bits were set; `overflows` from `bulk_sensor.FixedFreqReader.get_last_overflows()` (buffer-level). Surfaced per batch, never raised; caller must check.

**Drive current calibration** (`DriveCurrentCalibrate`, 37-85): `LDC_CALIBRATE_DRIVE_CURRENT CHIP=<name>`: sets REG_CONFIG bit 9 (on-chip auto-calibrate), dwells, reads back `REG_DRIVE_CURRENT0`, `drive_cur = (reg >> 6) & 0x1F`, restores config, saves via configfile. On-chip feature, not a heuristic.

**Init** (`_start_measurements`, 211-241): `REG_RCOUNT0 = clock_freq/(16*data_rate)`; `REG_SETTLECOUNT0 = SETTLETIME*clock_freq/16`; device ID check against `LDC1612_MANUF_ID=0x5449` / `LDC1612_DEV_ID=0x3055`, raises command_error naming wiring/faulty chip.

## E. Constants: principled vs hand-tuned (scrutiny for our port)

Principled:
- `freq_conv = clock_freq*sensor_div/2^28`: register bit-width definition.
- `RCOUNT0`, `SETTLECOUNT0` formulas: datasheet-derived.

Hand-tuned or unexplained (must not be blindly copied; expose as config or derive):
- `MIN_FREQ = 1,000,000` Hz noise filter: bare literal in two places, no justification. Needs naming/config exposure.
- `effective_radius = (coil_inner_diameter - 0.5)/2`: the -0.5 mm shrink is an unexplained fudge on a physical quantity. Flag for replacement.
- `max_offset = half_window * 0.5` vertex clamp: arbitrary factor.
- `sigma = half_window/2` Gaussian weight width: convention, not derived from noise statistics.
- Edge margins: 15% extremum-search margin vs `n//7` (~14.3%) peak-type edge region: two hand-picked percentages for the same idea, inconsistent.
- Peak-type center band 35%-65%: arbitrary.
- Timestamp tolerance +/- 0.05 s: arbitrary slack.
- Settle waits 0.05 s / 0.2 s / dwell 0.010 s: empirical paddings.
- probe_eddy_current `max_z=4.0`, `samp_dist=0.040`: hard-coded, reasonable but not exposed.
- `sensor_div` selection by equality with the 12 MHz default: fragile sentinel comparison.
