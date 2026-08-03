# Session handover (2026-08-03)

Read `CLAUDE.md` (conventions, durable gotchas), `README.md` (user-facing
reference) and `docs/design.md` (internal design) first. This file carries only
what those do not: where the project stands, what has been measured, and what is
open.

## Where we are

The plugin is written, reviewed, published and working on the owner's printer.
XY offsets and Z offsets both measure correctly. The motivating use case is
proven: the owner ran the dirty-nozzle test and a dirty nozzle measured the
same as a clean one (qualitative report; the numbers were not recorded).
Stock Klipper support is implemented: the plugin
resolves its firmware surfaces at startup per the firmware table in
`docs/design.md`, owns its contact-switch endstop, and `install.sh` detects
the checkout layout. No author-owned printer has ever run it on stock Klipper,
so the README labels that support untested by the author.

- Repository: https://github.com/jaak0b/kalico-eddy-offset-calibration
- 335 unit tests, green. CI runs them plus an integration suite over four
  firmware legs (Kalico main, Kalico 3b98cf51, Klipper master, Klipper
  v0.13.0); all four legs are green as of 2026-08-03. The first matrix run
  found one real plugin gap (sensor streaming hung a batch run; it now
  refuses) and two harness gaps, all fixed the same day.
- Hardware in use: BTT Eddy Coil wired to a Manta M8P V2.0, Raspberry Pi CM4,
  Voron with StealthChanger, Kalico from December 2025.

## Owner context

Hobbyist, no electronics background, capable maker. Anxious about being misled:
verify claims against source or datasheets, admit uncertainty, never bluff. He
catches real errors, several times this session, so take his objections
seriously rather than defending the first answer.

Working preferences he has stated: short sentences, no jargon, no restating a
question back at him. He wants the exact output written down rather than
described. Anthropic agents only. He approves each push explicitly; commits on
`main` need no approval.

## The owner's working config

```ini
[eddy_tool_calibration]
i2c_software_scl_pin: PA8
i2c_software_sda_pin: PC9
coil_x: 99.234
coil_y: -40.597
coil_z: 16.5
coil_inner_diameter: 8.0
calibrate_z: True
switch_pin: ^PF1
switch_x: 229.6
switch_y: -39.8
switch_probe_z_start: 32
tool_count: 2
toolchange_gcode:
    T{tool}
apply_offsets_gcode:
    SET_TOOL_OFFSET T={tool} X={offset_x} Y={offset_y} Z={offset_z}
```

Everything else runs on defaults. `frequency` must stay absent: that option
arrived in Kalico on 2026-03-04 and his build rejects unknown options. The
sexbolt switch shares its pin with `[tools_calibrate]`, which works because both
sides mark it multi-use.

## Measured so far

One machine, one session each, nozzles at 150 C, clean.

| what | measured |
|---|---|
| sensor noise floor | 58 Hz stddev on a 3.216 MHz base |
| XY repeatability, no toolchange | 4.6 um X, 2.5 um Y (six runs) |
| measurement spread, study | 5.7 um X, 4.6 um Y (15 runs, 3 cycles) |
| docking spread, study | 0.0 um X (below the measurement), 3.1 um Y |
| switch press spread | 0.0 to 2.5 um per anchor |
| agreement with the contact method | 14 to 66 um |
| microstep distance for comparison | 12.5 um per motor step |

The docking figures have two degrees of freedom, so treat them as an
indication. The contact cross-check is limited by the contact method's own
scatter rather than by ours.

## Open items, in priority order

1. **The owner's pending printer session.** Three checks queued for the next
   time the printer is on: verify the contact-switch endstop still triggers
   correctly after the plugin took ownership of it (commit 969bc56), run
   `EDDY_CALIBRATE_Z` once per tool because the sensor clock fix changed the
   anchor fingerprint, and run one calibration with the preheat wait forced to
   the `reactor_poll` strategy to prove that path on real hardware.
2. **Dirty-nozzle numbers.** The test was run and passed: a dirty nozzle
   measured the same as a clean one. The offset values were not recorded, so
   the README's claim stays qualitative until a run with numbers exists.
3. **`coil_inner_diameter: 8.0` is an estimate.** BTT publishes no bore figure
   anywhere. The measured response width supports it, but it sizes the fit
   window, so a real measurement would be better.
4. **Eight further duplications** found by review after three cleanup waves.
   None are defects today. The pattern that review named is worth carrying
   forward: the waves unified the leaf, the row or the predicate, and left the
   block that assembles the leaves.
5. **The crab board.** PCBs, stencil and coils are on hand; components were
   never ordered, about 100 EUR. The Eddy Coil already meets the accuracy target
   that board was meant to reach, so this is optional now.
6. **`EDDY_CALIBRATE_USED_TOOLS`**, the macro calling the plugin per used tool
   from `PRINT_START`, has never run in a real print.

## Durable gotchas, each one cost time this session

- **The `kalico/` clone is newer than the printer.** Finding an attribute there
  proves it exists in current Kalico, not in the December 2025 build. Reading
  `sensor.freq_conv` shut the printer down for exactly this reason. Reach any
  Kalico surface defensively unless you have established when it landed.
- **An unexpected exception in a gcode handler is a printer shutdown**, not an
  error message. Klipper's dispatcher catches `CommandError` and nothing else,
  so an `AttributeError` or a `KeyError` takes the machine down mid-command.
- **The unit tests never import klippy.** A green suite says nothing about any
  klippy-facing line. Several defects reached the printer with the suite green,
  including one that could have driven the nozzle into the switch. Much of that
  layer is pure and tested now; the rest is covered only by review, CI and
  hardware.
- **A setpoint is reproducible, a reading is not.** Anchors record the setpoint
  the tool was heated to, never the instantaneous temperature. Chasing a reading
  made the machine wait minutes for a fractional cooldown.
- **Positions must be resolved after the move completes.** The trapq holds only
  finished moves, so a mid-move query silently returns the previous move's end
  point. This made every early scan report its own start position.

## What was learned about working this way

The conventions in `CLAUDE.md` were rewritten twice after they failed to bind,
both times following the same lesson: a rule stated as a principle gets
rationalised away, and a rule carrying a checkable obligation does not. Rule 15
gained three categories and a per-comment justification. Rule 10 gained an
identifier search, unification declared in scope by default, and a report.

Reviews found what tests could not: the wrong determinant inherited from
upstream, the position mapping, the missing retreat, the offsets predicate
answered three ways, and the retreat that could drive down into the switch.
Hardware found what reviews could not: the version skew, the temperature stall,
the log rotation refusing a completed run. Neither substitutes for the other,
and the unit suite has never found a new defect on its own.

## Next steps

1. Run the owner's pending printer session (open item 1).
3. Repeat the dirty-nozzle test once with the offsets recorded, so the README
   claim can carry numbers.
