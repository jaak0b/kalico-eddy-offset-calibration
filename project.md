# EddyNozzleProbe

Automatic XYZ nozzle offset calibration for a Voron toolchanger, using a bed-mounted
eddy-current sensor board. Works with dirty nozzles: the sensor sees only metal, molten
plastic is invisible to it. Target firmware: Kalico (Klipper fork) only.

## Concept

A stationary sensor board at the bed edge, coil facing up. Each tool's nozzle is scanned
above the coil; the LDC1612 inductance-to-digital chip measures the resonant frequency
shift caused by the metal nozzle.

- **XY offset:** scan the nozzle across the coil, fit the symmetric response curve, take
  the symmetry center. Material-independent (brass, steel, plated copper all work) since
  it relies on geometry, not amplitude. Forward/reverse scans are averaged to cancel
  latency bias.
- **Z offset:** descend over the coil center, find where frequency crosses a per-tool
  calibrated reference. One-time contact calibration per tool material; automatic after.
- Claimed repeatability of the upstream project: 5 um same tool, ~20 um across tools.

## Hardware (ordered 2026-07-30, in transit)

Base design: chengxg's open-source "Little Crab" eddy XY calibration module
(GPLv3), dual-coil V2 board (2026-07 revision).

- Upstream repo: https://github.com/chengxg/tool_eddy_calibration
  (English fork: thunderkeys/tool_eddy_calibration, branch translate-en)
- oshwhub project (EasyEDA source): https://oshwhub.com/cxg01/project_lbabffjk
- Local verified copy: `E:\Development\crab-eddy-board\` — KiCad 10 import of the
  EasyEDA project (`kicad/crab_dualcoil.kicad_pcb|sch`), verified against the author's
  Gerbers by raster XOR (bottom copper 0.18% mismatch = render noise; top matches).
  Fab files: `gerber_dualcoil_v2.zip`, `BOM_dualcoil_v2.csv`, `CPL_dualcoil_v2.csv`.

### Board summary

LDC1612 (I2C, addr jumper, INTB broken out), 24 MHz 3225 active oscillator on CLKIN
(part labeled "40MHz" in author BOM/silkscreen but SMAF-024000 = 24.000 MHz; community
DigiKey BOM agrees on 24 MHz), SPX3819 3.3 V LDO fed by 5 V through 1N5819W, XH2.54-4P
connector (5V GND SCL SDA), two sensing channels:

- Channel 1 ("通道1"): small 5 mm 10 uH wound pancake coil (XKT-L111), hand-soldered.
  This is the XY/Z measurement coil (~2.3 MHz resonance with 470 pF C0G).
- Channel 2 ("通道2"): large spiral coil etched into the top copper. Reserved for future
  pressure-advance experiments; no firmware support yet. Unpopulated cap OK.

Board is 42.41 x 15.16 mm, all components on the bottom side, coils face up.
Two solder jumpers (J1 address, J2 I2C pullup enable) intentionally open.

### Orders placed

1. **JLCPCB:** 15 bare PCBs (black, 1.6 mm, LeadFree HASL, tented vias, flying probe)
   + bottom-side solder paste stencil (frameless). No assembly: self-assembly with
   hot air / hotplate. Stencil clarification answered: jumper/connector/coil pads stay
   closed on the stencil.
2. **DigiKey:** full parts kit for ~3 builds. Key parts: TI LDC1612DNTT x5,
   Abracon ASE-24.000MHZ-L-C-T (3.3 V LVCMOS 3225) x5, Samsung CL10C471JB8NNNC
   470 pF C0G x25 (performance-critical dielectric), Samsung CL10A106KP8NNNC 10 uF 10 V,
   SPX3819M5-L-3-3/TR x5, JST B4B-XH-A x5, 1k/4.7k 0603, LED, 1N5819W.
   Cart audited by two independent reviews against the author BOM; verdict OK.
   (100 nF: author used Y5V; X7R optional upgrade. SMD mounting nuts skipped: plain
   M3 hardware instead.)
3. **AliExpress:** XKT-L111 5 mm 10 uH pancake coils, 5-pack (channel-1 sensing coil).
   Enamel wire leads: burn/scrape insulation before soldering, glue coil body down.
4. **BTT Eddy Coil V1.0** (~19 EUR, arrives first): same electrical architecture
   (LDC1612 + coil, plain I2C, no MCU). Software development mule until crab boards
   are built. Bigger coil = worse XY sharpness, same driver and code paths.

### Assembly plan

Hotplate reflow with Sn42Bi58 low-temperature paste + stencil; hot air for rework.
LDC1612 (WSON-12, exposed pad) is the critical joint: paste amount on belly pad,
inspect with magnification. Practice run on a spare board first. 15 PCBs + 5 chips
allow multiple attempts.

## Electrical integration (printer side)

4 wires from board to any free MCU I2C-capable pins (software I2C fine): 5V, GND,
SCL, SDA. No MCU on the sensor board, nothing to flash. Kalico's standard MCU
firmware already contains I2C + ldc1612 support (re-flash mainboard only if its
firmware predates the ldc1612 driver).

## Software (next phase)

Target: Kalico only. Upstream plugin `tool_eddy_calibration.py` (GPLv3, klippy extra)
does XY via directional scans + parabolic fitting; depends on stock `ldc1612.py`.
Kalico ships `ldc1612.py`, `probe_eddy_current.py`, and the improved `probe_eddy_ng`.
### Architecture decision (2026-07-30, fable-advisor verdict)

**Kalico plugin, no fork.** Kalico natively loads modules from the gitignored
`klippy/plugins/` directory (printer.py `_load_modules`, documented in
Kalico_Additions.md): full extras-equivalent API access, own git repo, symlink
install, moonraker update_manager for updates. Fork rejected: it worsens the
update-breakage risk (whole-tree rebases) and no MCU firmware changes are needed
(channel-1 big coil and >250 Hz sampling are speculative future work; ~60
samples/mm at 4 mm/s scans suffices for parabolic fitting). Revisit forking only
if firmware changes become real.

**Own unified plugin (owner decision 2026-07-30, supersedes the vendoring
layout):** one plugin covering XY and Z with a single config section
(`[eddy_tool_calibration]`) and one command set. We port the upstream
algorithm (directional scans, parabolic sub-sample fit, forward/reverse pair
cancellation) rather than vendoring his code; his file stays in-repo as
reference and future upstream improvements are ported as ideas, not diffs.
Consequences: plugin is GPLv3 (derivative of upstream), and first-run
validation burden is ours (covered by the BTT Eddy Coil dev period).
Sample-to-position sync uses motion_report.get_trapq_position instead of
upstream's constant-speed interpolation.

**Offset output: console only for now.** Owner runs StealthChanger today but
is moving to a cxchanger (hotend-swap) toolchanger with possibly its own
plugin later; persistence/toolchanger integration deliberately deferred.
Plugin prints computed per-tool XYZ offsets as labeled values; the user
transfers them manually. save_variables/SAVE_CONFIG hooks are future work.

**Kalico pieces to build on** (verified in source at Kalico 5fdf0dd):
- `ldc1612.py`: 250 Hz batch streaming of (print_time, freq) via `add_client`,
  configurable CLKIN (`frequency: 24000000` for our board), software I2C,
  `LDC_CALIBRATE_DRIVE_CURRENT`.
- `motion_report.get_trapq_position(print_time)`: exact commanded position at a
  sample timestamp; upgrade over upstream's constant-speed linear interpolation.
- `probe_eddy_current.py` `EddyCalibration`: freq<->Z piecewise mapping +
  step-dwell calibration move pattern for per-tool Z references.
- `tools_calibrate.py` (bundled viesturz contact calibration): persistence
  template (`configfile.set` + SAVE_CONFIG, or save_variables) and a cross-check
  oracle against the contact-pin method.
- `SET_GCODE_OFFSET` in gcode_move.py for applying offsets at runtime.

Maintenance rule: pin upstream SHA; after each Kalico update run a smoke
calibration (API breakage surfaces at runtime, not load time).

## Decisions log

- Eddy current sensing chosen over contact pin (dirty-nozzle immunity) and optical
  (sees plastic drool). 2026-07-30.
- Geometry-over-amplitude principle: mixed nozzle materials make absolute amplitude
  untrustworthy; only symmetry centers and calibrated references are used.
- Buy + adapt upstream Little Crab design instead of designing a custom board;
  BTT Eddy hardware rejected as primary (coil too large for XY sharpness) but used
  as an interim dev unit.
- 24 MHz oscillator variant ordered (community-validated) despite "40MHz" labels;
  scale-free math and calibration absorb the constant either way.
