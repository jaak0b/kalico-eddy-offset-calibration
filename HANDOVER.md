# Session handover (2026-07-30)

Read `project.md` (hardware state + decisions) and `docs/design.md` (plugin
design) first; this file only adds session context they don't carry.

## Where we are

Hardware all ordered, nothing arrived yet. Software architecture decided and
designed; zero implementation code written. Next phase = scaffold + implement.

## Working directories

- `E:\Development\EddyNozzleProbe\` — THIS project (plugin + docs). Not yet a
  git repo. Not related to E:\Development\StlToStep (that CLAUDE.md doesn't
  govern here, but owner rules below do).
- `E:\Development\EddyNozzleProbe\kalico\` — shallow clone of Kalico @ 5fdf0dd
  (research reference; klippy/extras/ldc1612.py, probe_eddy_current.py,
  tools_calibrate.py, motion_report.py are the files we build against).
- `E:\Development\EddyNozzleProbe\tool_eddy_calibration.py` — upstream plugin
  copy (fetched from thunderkeys/tool_eddy_calibration branch translate-en);
  move into reference/ during scaffold.
- `E:\Development\crab-eddy-board\` — hardware artifacts: verified KiCad
  project (kicad/), author gerbers (gerber_author/), fab set
  (gerber_dualcoil_v2.zip, BOM/CPL csv), copper diff images.

## Owner context and rules

- Owner: hobbyist, no electronics background, capable maker (Voron toolchanger,
  StealthChanger today, cxchanger planned). Anxious about being misled: verify
  claims against source/datasheets, admit uncertainty, never bluff.
- Anthropic/Claude agents only (no grok/codex lanes) — standing rule.
- Caveman response mode is active (system reminder governs it).
- Owner wants: single config section, console-only offset output in v1.

## Key technical facts (all source-verified this session)

- Kalico loads plugins from gitignored `klippy/plugins/` natively
  (printer.py:185-198). Plugin, NOT fork (fable-advisor verdict).
- `ldc1612.py`: `LDC1612(config, calibration)`, `add_client(cb)` -> 0.1 s
  batches of `(print_time, freq_hz, dummy_z)`, 250 Hz fixed, `frequency`
  config = CLKIN (our board: 24000000), software I2C supported, addr 0x2A.
- `motion_report.get_trapq_position(print_time)` maps sample time -> commanded
  XYZ. Use it instead of upstream's constant-speed interpolation.
- Upstream plugin is Kalico-API-compatible except `from . import ldc1612` must
  become `from klippy.extras import ldc1612` inside plugins/.
- Crab board: LDC1612 ch0 only, 24 MHz CLKIN (BOM says "40MHz", part number
  and community BOM say 24 MHz — 24 is correct), 470 pF C0G tank, small 5 mm
  10 uH wound coil = measurement channel.
- Our plugin: GPLv3 (algorithm ported from chengxg's GPLv3 upstream).

## Next steps (agreed, in order)

1. Scaffold plugin repo in EddyNozzleProbe: git init, LICENSE (GPLv3),
   README, `eddy_tool_calibration.py` skeleton, `reference/` with upstream
   file, install.sh (symlink to klippy/plugins/), moonraker update block in
   README, move kalico clone path into .gitignore or document it.
2. Implement per docs/design.md: config wrapper embedding LDC1612, EDDY_QUERY,
   EDDY_LOCATE, scan engine (trapq-synced sampling), parabolic fit + pair
   averaging, EDDY_CALIBRATE_TOOL, Z curve + EDDY_SET_Z_REF.
   Port algorithm details from reference/tool_eddy_calibration.py (esp. its
   scan/fit functions, lines ~590-660 for sampling, fitting near extremum).
3. Offline testing before hardware: Kalico has no easy full sim, but fit math
   and scan-plan geometry are pure functions — unit-test those standalone
   (pytest, synthetic bell curves with known centers, noise, latency offsets).
4. Live bring-up when BTT Eddy Coil arrives (~Sat 2026-08-01): wire to printer
   I2C, EDDY_QUERY first. Eddy Coil CLKIN differs from crab board — BTT Eddy
   uses 12 MHz default in the driver; verify actual and set `frequency:`.
5. Crab boards: JLCPCB PCBs+stencil and DigiKey parts in transit; hotplate
   (Sn42Bi58) self-assembly; build guide to be written when boards near.

## Open questions / watch items

- LDC1612 `add_client` batch cadence (0.1 s) vs scan duration: a 4 mm @ 4 mm/s
  scan = 1 s = ~10 batches, 250 samples. Fine, but confirm no samples dropped
  at scan edges (overflows field in batch dict).
- EDDY_LOCATE raster: keep total runtime < 30 s; coarse grid then refine.
- Drive current for the small crab coil: run LDC_CALIBRATE_DRIVE_CURRENT
  equivalent; author config used reg_drive_current: 22 as starting point.
- Validation ladder targets in docs/design.md section "Validation plan".
