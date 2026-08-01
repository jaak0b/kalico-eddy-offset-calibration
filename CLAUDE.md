# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A Kalico (Klipper fork) plugin that calibrates per-tool XYZ nozzle offsets on toolchanger 3D
printers using a bed-mounted LDC1612 eddy-current sensor board. Non-contact and immune to dirty
nozzles: the sensor sees only metal. XY comes from directional scans over the coil with parabolic
sub-sample fitting of the symmetric response (material-independent); Z comes from a
frequency-vs-height descent curve anchored by a one-time per-tool contact reference. Offsets are
printed to the console in v1; no toolchanger integration yet. Hardware is chengxg's open-source
"Little Crab" dual-coil board (self-assembled; see `project.md`). Design: `docs/design.md`;
session state: `HANDOVER.md`.

## The plugin: Python, Kalico klippy plugin

Single module `eddy_tool_calibration.py`, installed by symlink into Kalico's gitignored
`klippy/plugins/` directory (Kalico loads it like an extra; config section name = module name).
GPLv3 (algorithm ported from the GPLv3 upstream, kept in `reference/`). Kalico-only target; no
stock-Klipper compatibility requirement.

Commands (repo root):

```bash
pip install -r requirements_test.txt
python -m pytest tests/            # unit tests for fit math and scan geometry
```

Structure:

- **`eddy_tool_calibration.py`**: the plugin. Pure-math parts (parabolic fit, pair averaging,
  center reconstruction, curve evaluation) live in functions with no klippy dependency so they
  are unit-testable standalone.
- **`reference/tool_eddy_calibration.py`**: upstream file, unmodified, algorithm provenance.
- **`kalico/`**: shallow read-only clone of Kalico for API reference (not shipped, gitignored).
  Files we build against: `klippy/extras/ldc1612.py`, `probe_eddy_current.py`,
  `motion_report.py`, `tools_calibrate.py`.
- **`tests/`**: pytest; synthetic response curves with known centers as oracles.
- **`docs/`**, **`project.md`**, **`HANDOVER.md`**: design, hardware state, session handover.

Durable gotchas:
- Inside `klippy/plugins/`, cross-module imports must use the full namespace
  (`from klippy.extras import ldc1612`), never relative imports.
- Our crab board's LDC1612 CLKIN is 24 MHz (`frequency: 24000000`); the BTT Eddy Coil dev unit
  differs (driver default 12 MHz). Wrong constant scales all frequencies; geometry math is
  scale-free but Z references are not transferable across clock settings.
- `ldc1612.add_client` delivers 0.1 s batches of `(print_time, freq_hz, dummy_z)` at a fixed
  250 Hz; watch the `errors`/`overflows` fields at scan edges.
- Map sample timestamps to positions with `motion_report.get_trapq_position(print_time)`,
  never by assuming constant scan velocity.
- **The `kalico/` clone is newer than the printer this runs on.** Reading an attribute,
  option or method there proves it exists in current Kalico, not in the owner's build. Before
  depending on any Kalico surface, establish when it was added and reach it defensively if it
  postdates the target build (`ldc1612`'s `frequency` option and its `freq_conv` attribute both
  arrived 2026-03-04; the printer runs December 2025). The tests cannot catch this: they never
  import klippy, so every Kalico-facing line is proven only on hardware.
- An unexpected exception in a gcode handler is not an error message, it is a printer shutdown.
  Klipper's dispatcher only catches `CommandError`, so an `AttributeError` or a `KeyError`
  reaches the bare handler and shuts the machine down mid-command.

## Conventions

Numbered for unambiguous reference; do not cite rule numbers in shipped source or UI text.

1. **Measurement integrity: established methods only, never a fudge.** Every algorithm
   (parabolic sub-sample fitting, forward/reverse pair cancellation, least-squares center
   reconstruction, frequency-curve evaluation) is an established method or ported from the
   validated upstream, named with provenance in comments. NEVER introduce a hand-tuned constant,
   empirical offset, or bias correction fitted to make one particular setup's numbers look
   right: that overfits the sample and lies on the next one. Tunable tolerances are config
   parameters, exposed and documented, not buried magic numbers.

2. **No silently swallowed errors.** An `except` must surface the error, rethrow, or return a
   value the caller can act on. Every failure path a user can hit (no samples, too few samples,
   bad fit residual, no extremum in window, sensor amplitude errors) raises a gcode error with
   an actionable message naming the likely cause.

3. **Keep the math framework-agnostic and modular.** Fit and geometry functions take plain
   numbers/arrays and import nothing from klippy; the plugin class is the only klippy-facing
   layer. New calibration modes or sensor variants are their own modules behind clear
   interfaces.

4. **NO AI attribution in git/GitHub; no AI process residue in any output.** A
   `Co-Authored-By: Claude <...>` trailer NEVER allowed on commits. no AI
   attribution anywhere. Commit messages: a single short sentence. Shipped output of every kind
   (source code, comments, docstrings, docs, UI text, error messages, commit messages) must
   never reference the AI-assisted process behind it: no mention of these rules or their
   numbers ("per rule 2"), CLAUDE.md, skills, agents, subagents, prompts, reviews by agents,
   or session context. Rationale is expressed in plain domain terms instead ("failed passes
   always print their full diagnostics so the cause stays visible"). The reader of any shipped
   file must find no evidence of how it was produced.

5. **Commit approval.** The owner granted standing approval to commit at will on `main`
   (2026-07-30). Pushes still require explicit approval.

6. **Never use the em-dash character**, and never a hyphen as a substitute for it. Rewrite with
   a colon, parentheses, a comma, or two sentences. Hyphens only where grammar requires
   (compound modifiers).

7. **UI text is plain technical prose; terminology is the Klipper/Voron community's.** Complete
   grammatical sentences in docs and error messages, neutral register. Terms as the ecosystem
   names them (toolhead, nozzle offset, probe, macro, config section, print_time); one term per
   concept. Gcode command names follow Klipper convention (UPPER_SNAKE, parameters `T=`, `Z=`).

8. **Diagnostic readouts show raw values** as labeled rows, not prose sentences (frequency,
   fitted center, residual, sample count, per-axis offset).

9. **Never corrupt reported offsets.** The printed offsets reflect the fitted measurement
   exactly; no rounding beyond documented display precision, no silent clamping, no "helpful"
   adjustments. A measurement that fails validation is reported as failed, never as a plausible
   number.

10. **Extend the concept's existing home; never bolt a duplicate beside a symptom.** Before
    adding or fixing logic, find the module that already owns the concept (search for the
    concept, not just the symptom site) and extend it. Never compute a value the codebase
    already derives elsewhere: if a figure is produced in two places, unify on the single
    source. Reuse Kalico's own machinery (ldc1612 driver, motion_report, EddyCalibration
    patterns) instead of reimplementing it. Any non-trivial or cross-cutting change gets a short
    written design first (its canonical home, what it extends, what it must not duplicate) for
    owner approval before implementation. Interim ("quick fix now, proper fix later") solutions
    are forbidden in all cases: the correct structure is built immediately, even when it costs a
    schema change or a larger diff.

11. **Subagent discipline.** Give every subagent a correct, specific title; never run more than
    1 Fable agent at a time (hard budget limit). Sonnet is fine for parallel design/research
    work. Only Anthropic/Claude agents: never route work to cross-vendor lanes (grok/codex).
    The main (user-facing) agent edits repository files itself only for tiny changes (a single
    line); anything larger is performed by a subagent. The main agent also delegates other
    context-heavy work and consumes only conclusions: codebase exploration and broad searches,
    reading large files or external references, and reviews/audits. The main agent keeps for
    itself only what needs conversation context or judgment: talking to the owner, design
    decisions, writing the subagent prompts, running tests to verify outcomes, and git commits.
    Exceptions where the main agent may edit directly: CLAUDE.md and the memory directory
    (meta-configuration the owner asks for), and reverting a file with git. When delegating
    implementation, the prompt must be self-contained (files, constraints, conventions,
    definition of done, exact interfaces or design decisions already made); iterative design
    loops with the owner are still driven by the main agent, which re-delegates each round with
    the updated instructions rather than editing directly because the round feels small.

12. **Exhaustive dispatch over closed sets.** Any branch on a closed set (command parameters,
    calibration phases, sensor status codes the plugin claims to handle) must handle every
    member explicitly and end in an explicit "unhandled" path (raise, or log-and-skip per
    rule 2). Never write an `else` that assumes whatever is left: it silently absorbs members
    added later.

13. **Test oracles must be independent of the logic they judge.** A test's expected value comes
    from a source the implementation cannot contaminate: a synthetic response curve constructed
    with a known center asserts that literal center within tolerance; latency-bias tests build
    the bias in by construction. Never compute the expectation by calling, copying, or
    paraphrasing the code under test: a test that mirrors the implementation passes when both
    are wrong and verifies nothing. This is the deliberate exception to rule 10: oracle math is
    duplicated on purpose. Self-consistency checks (repeatability spread) are legitimate but
    must never be presented as accuracy evidence; accuracy claims require the independent
    contact-method cross-check.

14. **No invented defaults for setup-specific values.** A config option whose correct value
    depends on the user's machine (coordinates, pins, drive current, tool count) gets NO
    default in code or docs: it is a required option the user must provide, and the config
    reference shows it blank. Defaults exist only for values we can genuinely know (protocol
    constants, algorithm tunables with provenance, driver defaults that are correct for the
    documented hardware). A wrong-looking example number presented as a default teaches users
    to keep it.

15. **A comment states what the code cannot; three categories only, and a budget.** A new
    comment or docstring line is allowed ONLY if it is one of: (a) provenance, citing the source
    file and line of a ported algorithm or a depended-on Kalico behaviour; (b) a physical or API
    constraint unreadable from the code (hardware behaviour, a measurement assumption, a
    deliberate deviation and why); (c) the reason an "unhandled" branch exists. Everything else
    is forbidden, specifically: restating what the next line or block does, arguing that a
    change is an improvement, narrating what was deleted or added, describing the shape of a
    fix, and docstrings that paraphrase the function name or signature. Code needing a paragraph
    to follow gets rewritten, not annotated.

    **Enforcement, because the principle alone does not hold.** Every comment must be able to
    name which of the three categories justifies it, and a provenance comment must cite the file
    and line it rests on. A comment whose category cannot be named is deleted, no judgement call
    and no argument from usefulness. That requirement is what limits volume: a change contains
    only so many genuine citations, constraints and unhandled branches, so no quota is needed and
    none is set, since a quota would only invite filling it. Deleting comments that fail the
    categories is always free. The rationale in a task's instructions is for whoever does the
    work, not for the file: never copy the reasoning behind an assignment into the code it
    produces. When reviewing, a comment that cannot name its category is a finding like any
    other.

**Verification bar.** `python -m pytest tests/` green before any feature is declared finished.
Final acceptance for measurement-facing changes is a run on the owner's real printer (Eddy Coil
dev unit or crab board), verified by the owner, per the validation ladder in `docs/design.md`
(repeatability spread, contact-method cross-check, dirty-nozzle test).
