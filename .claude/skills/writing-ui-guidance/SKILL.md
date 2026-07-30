---
name: writing-ui-guidance
description: Use when writing or changing any user-facing guidance text (gcode error messages, console output, config reference, setup instructions in docs), before committing the change. Ensures critical constraints are actually seen and instructions survive skimming.
---

# Writing UI guidance users actually read

Users scan, they do not read. Eye tracking shows attention concentrates on the first line of a block and collapses after it (NN/g F-pattern; only ~20-28% of words on a page are read). A constraint buried mid-paragraph is functionally undocumented. These rules are evidence-backed (NN/g studies, plainlanguage.gov, ANSI Z535.6 warning anatomy, poka-yoke control hierarchy); follow them in order. In this plugin, "UI text" means gcode error messages, console command output, the config reference, and setup instructions in the README or docs.

## 1. Code beats text (poka-yoke hierarchy)

Before writing a sentence, ask: can the plugin enforce or check this itself? Config validation at load, a sensor sanity check before a scan starts, a sensible default, or an inline gcode error at the moment of the mistake ranks above ANY wording. Text is the weakest control; use it only for what software cannot know (e.g. "keep metal objects away from the coil area"). If a constraint is checkable (from data the plugin has), add the check and delete the prose.

## 2. One constraint, one sentence, one place

Never restate the same rule at different strengths. Multiple versions of one rule read as several rules and the user cannot tell which binds. Collapse to the single true rule and delete the rest. If two genuinely different rules exist (a hard requirement and an optional tip), they never share a sentence or a severity register.

## 3. Mandatory constraints get isolation, not prose

A must-do never lives inside a descriptive paragraph. It gets its own line, visually distinct, placed at the point of action (the config flow step or field it governs), not in an upstream preamble. Anatomy when consequences matter: what to do, then what goes wrong otherwise, in that order, each one short sentence.

## 4. Instruction first, reason second or cut

Verb-first imperative ("Run EDDY_LOCATE before the first calibration."), rationale as a trailing clause or second short sentence only when the rule would otherwise look arbitrary. Reasons never precede or bury the instruction. Optional tips can drop the reason entirely.

## 5. Length ceilings

Helper text: at most two to three short sentences (per the repo's rule 7, and NN/g caps microcopy below three sentences). Average sentence under ~20 words. One action per sentence. If a block needs more, it has more than one job: split it across the points of action, or cut.

## 6. Retention budget: about four items

Users retain roughly four chunks from text read once and acted on later. One core mandatory fact per step. Everything beyond the budget must be enforced in code (rule 1), moved to the point of action (rule 3), or cut (rule 2).

## 7. Optional depth is one click, never for essentials

Progressive disclosure is only for depth some users want; the essential path must work with zero disclosure interactions. One level of disclosure at most, and label the trigger with what it answers, not a bare icon.

## Checklist before committing guidance text

- Could code enforce this instead? If yes, do that and delete the text.
- Is any rule stated more than once at different strengths? Collapse.
- Is every mandatory constraint on its own line at the point of action?
- Does every sentence start with the action, one action per sentence?
- Is any block over three sentences? Split or cut.
- Would a reader who skims ONLY the first sentence of each block still succeed?
