# Writing config option documentation

Use when writing or changing any option entry in the `[eddy_tool_calibration]`
config reference in `README.md`, and whenever a new config option is added to
the plugin. The house style is Klipper's own `Config_Reference.md`, because
that is the format every Klipper and Kalico user has already read a hundred
times. Matching it costs nothing and makes the reference feel native.

## The shape of an entry

**Sentence one names the value, with its unit in parentheses.** Start with the
noun, never with a condition, never with when you would need it.

```
#   Distance (in mm) that the axis travels with one full rotation of
#   the stepper motor.
#   The speed (in mm/s) of non-probing moves during the calibration.
#   A time value (in seconds) over which temperature measurements will
#   be smoothed to reduce the impact of measurement noise.
```

Units go in parentheses immediately after the noun: `(in mm)`, `(in mm/s)`,
`(in seconds)`, `(in Celsius)`, `(in Hz)`, `(in degrees)`. Not "in mm" trailing
at the end of a clause, not "mm" bare.

**Sentence two states the default, in Klipper's fixed phrasing.** The exact
form is `The default is X.` with no hedging and no explanation attached.

```
#   The default is 250000.
#   The default is 5mm/s.
#   The default is 0.100 seconds.
#   The default is to not implement Z hop.
```

That last form covers a behavioural default, where the value is an absence.
Use it rather than inventing a phrasing.

**A required option says so, in Klipper's fixed phrasing:**

```
#   This parameter must be provided.
```

Never "Required." on its own, never a bare adjective at the start of the
sentence.

## Length

Two to four sentences. If an entry needs more, the extra material is almost
always one of the three things listed under "What does not belong" and belongs
somewhere else.

## What does not belong in an entry

**Conditions before the definition.** "Needed only to run a command without
T=" is not what the option is. Say what it is first; put the condition in the
last sentence if it survives the cut at all.

**Arguments for the option's own correctness.** Do not explain what breaks if
the value is wrong, what happens if it is left out, or why the default was
chosen, unless a user is genuinely stuck without that sentence. A reference
documents settings; it does not defend them.

**Cross-option behaviour restated per option.** When a fact concerns how two
or more options interact, or how a command behaves, it goes once in the prose
above the group, not inside each option that touches it. An entry may name a
related option, in Klipper's style, without re-explaining it:

```
#   This parameter is only valid when the sensor is a thermistor.
#   If a gear_ratio is specified then rotation_distance specifies the
#   distance the axis travels for one full rotation of the final gear.
```

**Enumerated branches.** "When it is set, X. When it is not, Y." is a symptom
of documenting behaviour instead of the value. Pick the one a user needs and
cut the other.

## Checklist before committing an entry

- Does sentence one start with the noun the option holds?
- Is the unit in parentheses right after that noun?
- If it has a default, is it written exactly `The default is X.`?
- If it is required, does it say exactly `This parameter must be provided.`?
- Is the entry four sentences or fewer?
- Does anything in it argue, warn about misuse, or restate another option's
  behaviour? Cut it or move it to the group's prose.
