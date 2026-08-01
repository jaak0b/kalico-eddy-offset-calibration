# Hardware bring-up: BTT Eddy Coil V1.0 on Manta M8P

Scope: first live validation of the plugin, on a BTT Eddy Coil V1.0 wired to
the Manta M8P mainboard. This is step 1 of the validation ladder in
`docs/design.md` (bring-up), not the full ladder. A short section at the end
covers what changes for the "Little Crab" board later.

Follow the steps in order. Stop and check the named symptom before moving to
the next step.

## 1. Kalico version

The BTT Eddy Coil bring-up works on any Kalico that has a
`klippy/plugins/` directory (2023 onward), provided `frequency:` is left
out of the config (section 4). The Little Crab board needs Kalico from
March 2026 or newer, because the `frequency` option itself did not exist
before then (section 9).

Check the installed version on the Kalico host:

```
git -C ~/klipper describe --always --tags --long --dirty
```

Adjust the path to `~/kalico` if that is where your checkout lives.
Without SSH access, send `M115` in the console instead: the reply's
FIRMWARE_NAME and FIRMWARE_VERSION fields identify the build.

## 2. Wiring

**Voltage warning.** BTT's Eddy documentation lists the Eddy sensor supply as
5V. Verify this against the silkscreen or manual on your own Eddy Coil V1.0
before powering it: if your unit's connector is marked 3.3V instead, feeding
it 5V can damage it.

### 2.1 Eddy Coil V1.0 connector

- BTT ships Eddy Coil with a 4-pin ZH1.5 connector on the sensor end and
  loose DuPont pins on the mainboard end. The 4 wires carry power, ground,
  and the two I2C lines (SCL, SDA).
- BTT's own documentation does not give a confirmed pin-by-pin order for
  this connector (see Sources). **Verify the pin order against the label
  printed on the Eddy Coil PCB itself before connecting power**, do not
  wire from this doc alone.

### 2.2 Manta M8P V2.0 (STM32H723)

- The board has a labeled physical I2C connector wired to MCU pins PA8
  (SCL) and PC9 (SDA).
- Klipper's stock STM32H723 firmware does not compile in the `i2c3`
  hardware bus those pins belong to, so hardware `i2c_bus:` will fail to
  find the bus. Wire the sensor's SCL/SDA to those same physical pins but
  configure them as software I2C instead:
  ```
  i2c_software_scl_pin: PA8
  i2c_software_sda_pin: PC9
  ```
- Take 5V and GND for the sensor from the same connector or an adjacent
  fan/aux power connector. Verify against the silkscreen before connecting.

### 2.3 Manta M8P V1.x (STM32G0B1)

- No confirmed labeled I2C connector was found in the available V1.x
  documentation for this bring-up. Use software I2C on two spare digital
  pins instead:
  ```
  i2c_software_scl_pin: PC0     # spare Motor4/expansion endstop pin
  i2c_software_sda_pin: PC14    # spare probe pin, if unused
  ```
- **Verify these two pin names against your board's silkscreen and the
  V1.0/V1.1 schematic before wiring**: they are read from a Klipper generic
  config for this board revision, not from a confirmed I2C-specific
  connector, and a wrong pin here reads as "no samples" in section 5, step
  3, not as damage.
- Take 5V and GND from any spare fan or endstop connector on the board.

## 3. Install

The repository has no published git remote yet, so `git clone` does not
work. Copy the plugin file from the development machine to the Kalico host
instead.

1. Create the plugins directory on the Kalico host (the CM4) if it does not
   exist yet:
   ```
   ssh user@host mkdir -p ~/klipper/klippy/plugins
   ```
   Use `~/kalico/klippy/plugins` instead if your checkout lives at
   `~/kalico`.
2. Copy the file from Windows with `scp`:
   ```
   scp E:\Development\EddyNozzleProbe\eddy_tool_calibration.py user@host:~/klipper/klippy/plugins/
   ```
3. Add the config from section 4 to `printer.cfg`, then, with the sensor
   still disconnected, restart klippy and check the log:
   ```
   FIRMWARE_RESTART
   tail -n 50 ~/printer_data/logs/klippy.log
   ```
   **Do this restart with the sensor not yet connected.** A traceback
   naming `eddy_tool_calibration` at this point comes from the plugin file
   or config, not from wiring, because a Python-level load error happens
   whether or not the sensor is attached.

A copied file must be re-copied by hand after every change, whereas
`install.sh`'s symlink (below) tracks a git checkout on the host
automatically, so switch to the clone-plus-`install.sh` flow once the repo
is published.

### 3.1 Alternative: install.sh (requires a published repository)

Once the repository has a published git remote:

```
cd ~
git clone <repo url> eddy_tool_calibration
cd eddy_tool_calibration
./install.sh
```

`install.sh` symlinks `eddy_tool_calibration.py` into your Kalico checkout's
`klippy/plugins/` directory. Confirm the symlink exists:

```
ls -la ~/kalico/klippy/plugins/eddy_tool_calibration.py
```

Moonraker does not need a restart for this. Restart klippy so it loads the
new plugin:

```
FIRMWARE_RESTART
```

## 4. Config

Add this section to `printer.cfg`, replacing `coil_x` / `coil_y` with the
bed coordinates of your Eddy Coil once it is mounted:

```ini
[eddy_tool_calibration]
# --- sensor: wired per section 2.2 (M8P V2.0) above ---
i2c_mcu: mcu                    # the Manta M8P itself, not a toolboard
i2c_software_scl_pin: PA8       # M8P V2.0 physical I2C connector, SCL
i2c_software_sda_pin: PC9       # M8P V2.0 physical I2C connector, SDA
i2c_address: 42                 # LDC1612 default address (0x2A)
# frequency: left out on purpose. Kalico's ldc1612 driver defaults to
# 12 MHz, which is the BTT Eddy Coil's real CLKIN, so the correct value
# needs no config line. Setting frequency: on a Kalico older than March
# 2026 is a startup error: that Kalico has no such option (section 1).
reg_drive_current: 22           # placeholder; replace with the value from
                                 # LDC_CALIBRATE_DRIVE_CURRENT in section 5,
                                 # step 2
# --- geometry: measure with a ruler before EDDY_LOCATE refines it ---
coil_x: 350.0                   # approximate bed X of the coil center
coil_y: 5.0                     # approximate bed Y of the coil center
coil_z: 0.0                     # machine Z of the coil top face; measure
                                 # this against your Z endstop or gantry
coil_inner_diameter: 8.0        # mm; BTT Eddy Coil bore, not the crab coil
scan_height: 1.0                # nozzle height above the coil top for XY
scan_safe_z: 2.0                # extra mm of clearance for travel moves
z_start: 5.0                    # Z descent start, above the coil
z_stop: 0.5                     # closest approach; never touches the coil
z_step: 0.05                    # descent step size
# --- scan tuning: upstream defaults, unchanged for bring-up ---
scan_speed: 4.0
scan_length: 12.0               # comfortably exceeds the larger Eddy Coil bore
scan_angles: 45, 135
pair_scans: True
samples_min: 100
save_csv: True                  # keep raw scan data while validating
```

**Do not add `frequency:` unless your Kalico is from March 2026 or
newer.**

Use `coil_inner_diameter: 8.0` for the BTT Eddy Coil bore (larger than the
crab board's coil); the plugin derives `fit_window_radius` from it, so an
undersized value here narrows the fit window below the coil's real response.
Verify the actual bore against BTT's Eddy Coil manual for your unit.

## 5. First power-up checklist

1. Power on, then check the klippy log for load errors:
   ```
   tail -n 50 ~/printer_data/logs/klippy.log
   ```
   A traceback naming `eddy_tool_calibration` or `ldc1612` at startup means a
   config or wiring problem; fix it before continuing.
2. Calibrate the sensor's drive current, then persist it:
   ```
   LDC_CALIBRATE_DRIVE_CURRENT CHIP=eddy_tool_calibration
   SAVE_CONFIG
   ```
   This restarts Kalico. Wait for it to reconnect before the next step.
3. Run `EDDY_QUERY` and read the printed rows.
   - No samples at all: check the I2C wiring and the `i2c_software_scl_pin`
     / `i2c_software_sda_pin` values against section 2.
   - Frequency far from the expected LDC1612 range (well outside roughly
     1 to 10 MHz): likely a wrong sensor, a wiring problem, or, on a
     Kalico from March 2026 or newer, a wrong explicit `frequency:` value
     in section 4.
   - Nonzero `errors` or `overflows` rows: rerun step 2, the drive current
     is not calibrated for this coil.

### 5.1 MCU firmware

No menuconfig option controls LDC1612 support, and normally no reflash is
needed either. I2C defaults on for any GPIO-capable STM32, including the
Manta M8P V2.0's STM32H723, and firmware support for the LDC1612 driver has
shipped since April 2024.

If the MCU identifies itself as "Klipper" rather than "Kalico" when Kalico
connects, that is a warning, not a failure. It is not a reason to reflash.

## 6. Locate and calibrate

1. Home the printer.
2. Park the nozzle above the `coil_x` / `coil_y` position you configured,
   at a safe height, before running any scan command.
3. Run `EDDY_LOCATE` and confirm the printed coil center is close to your
   configured `coil_x` / `coil_y`. A result far from your estimate means the
   coarse scan missed the coil; recheck the geometry in section 4.
4. Run `EDDY_CALIBRATE_TOOL` with `save_csv: True` already set in config, so
   every scan pass and the Z descent are written to CSV for offline review.

## 7. Safety notes

- **Confirm `coil_z` and `scan_height` before the first Z descent.** The
  descent goes to `z_stop` (0.5 mm machine Z above the coil by default).
  Confirm the coil's physical top face sits below that machine Z, or the
  nozzle will crash into it.
- Run the first `EDDY_CALIBRATE_TOOL` with the printer's Z already at a
  safe height above the coil, and keep a finger near the emergency stop
  for the whole descent.
- If any scan pass reports a fit error or an extremum on the edge of the
  window, stop and rerun `EDDY_LOCATE` rather than repeating the same scan;
  see `docs/design.md` for what each error means.

## 8. Validation ladder

Bring-up (this document) is stage 1 of the validation plan in
`docs/design.md`. Do not re-derive the repeatability, cross-check, or
dirty-nozzle criteria here; follow that document for stages 2 through 5
once this bring-up succeeds.

## 9. Differences for the Little Crab board (later)

When moving from the BTT Eddy Coil to chengxg's Little Crab dual-coil
board, change only:

- `frequency: 24000000` (Little Crab's CLKIN oscillator, not the BTT
  Eddy's 12 MHz). **This requires Kalico from March 2026 or newer**
  (section 1); it is the only reason to update Kalico for this bring-up.
- The sensor connector is XH2.54-4P: 5V, GND, SCL, SDA. The crab board
  carries its own LDO, so 5V (not 3.3V) is correct on that pin.
- `coil_inner_diameter` back down to the crab board's own (smaller) bore.

Wiring pattern (which MCU pins carry SCL/SDA, hardware vs software I2C) is
otherwise the same as this bring-up.

## Sources

- https://github.com/bigtreetech/Eddy: Eddy Coil connects via I2C to a
  toolboard/mainboard rather than USB; full pinout and voltage are in the
  bundled PDF manual, not the repo README.
- https://global.bttwiki.com/Eddy.html: states Eddy connector is a 4-pin
  DuPont/ZH1.5 header and lists sensor supply voltage as 5V.
- https://github.com/krautech/btt-eddy-guide: confirms Eddy Coil is wired
  to an existing MCU's I2C port rather than carrying its own USB/CAN MCU.
- kalico/klippy/extras/ldc1612.py (local clone, current tree):
  `DEFAULT_LDC1612_FREQ = 12000000` with the comment "assume 12MHz is BTT
  Eddy"; confirms 12 MHz is the correct clock when `frequency:` is omitted
  from the config in section 4, on a Kalico recent enough to carry the
  option at all.
- Kalico PR #852 (merged 2026-03-04): added the `frequency` config option
  to `klippy/extras/ldc1612.py`. Verified locally by diffing that file at
  the `v2026.01.00` tag against the current tree: the older file hardcodes
  `LDC1612_FREQ = 12000000` and reads no `frequency` option at all, which
  is why that option is a hard startup error on the owner's December 2025
  Kalico build (section 1).
- src/Kconfig (local Kalico clone): `WANT_LDC1612` depends on `WANT_I2C`,
  which defaults on for any GPIO-capable STM32, including the Manta M8P
  V2.0's STM32H723; no menuconfig change is needed for LDC1612 firmware
  support (section 5.1).
- https://klipper.discourse.group/t/manta-m8p-i2c-bus-name/16062 and
  .../16062/2: Manta M8P V2.0's labeled physical I2C connector is wired to
  PA8 (SCL) and PC9 (SDA), matching Klipper's `i2c3` bus definition, but
  stock STM32H723 firmware does not compile `i2c3` in; a user reported
  wiring the same two physical pins as software I2C instead as the working
  fix.
- https://github.com/Klipper3d/klipper/blob/master/config/generic-bigtreetech-manta-m8p-v1.1.cfg:
  confirms the V1.1 board's MCU is STM32G0B1 and lists its endstop/probe
  pin names (PF3/PF4/PF5 for XYZ endstops, PC0 on the spare Motor4
  connector), used here as candidate software I2C pins in the absence of a
  confirmed dedicated I2C connector for V1.x.

Facts not verified, treat as unconfirmed until checked on the bench:
- The BTT Eddy Coil V1.0 connector's exact pin order (which of the 4 pins
  is 5V, GND, SCL, SDA and in what sequence).
- Whether Eddy Coil V1.0 is 3.3V-only or genuinely 5V-tolerant on its logic
  lines (the wiki states 5V supply but does not discuss logic-level
  tolerance).
- A confirmed, labeled I2C connector on the Manta M8P V1.x boards; the two
  pins recommended above are spare general-purpose pins, not a documented
  I2C connector.
