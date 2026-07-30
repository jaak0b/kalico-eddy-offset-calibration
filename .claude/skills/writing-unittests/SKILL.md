---
name: writing-unittests
description: Use when adding or changing a pytest unit test under tests/**, when reviewing unit tests, or when a code change needs a unit test that would actually catch a real bug, before committing the change.
---

# Writing unit tests

Unit tests (pytest, `tests/**`) are the internal correctness net: they keep the fit math and
scan geometry honest against ground truth fixed outside the implementation. Final acceptance
on the owner's real printer is a separate, owner-driven step and nothing here replaces it.

## The two questions every test must pass

A unit test earns its place by answering yes to both (Khorikov's four pillars, Beck's test
desiderata, the Google Testing Blog agree on the core):

1. **Would it fail on a real bug?** (regression protection)
2. **Would it stay green through a behavior-preserving refactor?** (refactoring resistance)

Plus fast feedback and maintainability. Test observable behavior through the public interface of
the module under test; never assert internal steps, private helpers, or call sequences. Test
state, not interactions: an interaction test checks how a result was reached, and only the result
matters. A test that fails both questions is a change detector, and a change detector is worse
than no test, because it trains people to update expectations on sight.

## HARD RULE: no math in tests

Owner-mandated and non-negotiable. An expected value is NEVER calculated inside a test: no
formulas, no unit conversions, no reuse of production helpers to derive the expectation. Every
expected result is a hardcoded literal with an independent provenance: a value hand-calculated
once outside the test (cite the derivation beside the literal), or the known ground truth a
synthetic fixture was generated from. A test
that recomputes the expected value with the same formula as production is tautological: it shares
any bug with the code under test and can never catch the bug it mirrors.

**Clarification, not an exception:** the seed-recovery pattern is the gold standard here: build a
synthetic response curve from known parameters (a bell curve centered at exactly x=2.375 with
known noise and latency shift) and assert the fit recovers those hardcoded parameters within
tolerance. The truth is the seed literal, not a computed expectation. Recorded real sensor
streams used as fixtures are raw input data the code never produced; their expected results are
literals verified once against an offline analysis.

Tolerances come from the method's actual noise floor (sample rate, sensor noise, fit residual) and
are justified where chosen, never a convenient round number, and never widened to make a failure
pass.

## Banned smells

Headline list (Meszaros's xUnit Test Patterns, Google Testing Blog):

- **Tautological test**: expected value derived by the production formula (see hard rule above).
- **Change detector**: fails on any implementation change; unread snapshot dumps.
- **Over-mocking**: asserting a mock was called with arguments mirroring the calling code tests
  "did I write this code". Mock only true boundaries (the klippy printer object, sensor stream,
  toolhead motion). Feed the fit and geometry logic real sample arrays and real timestamps (the
  classicist style).
- **Obscure test / mystery guest**: the expected behavior is unreadable without opening the
  implementation or an unseen shared fixture.
- **Assertion roulette**: many unlabeled asserts in one test.
- **Conditional logic in tests**: no if/loops branching on outcomes; parametrized case tables
  (`pytest.mark.parametrize`) are fine.
- **Shared mutable fixtures**: prefer local builders per test.
- **DRY over DAMP**: duplication is acceptable when it keeps a test verifiable by inspection.

## Proving a test works

- **Watch it fail first.** When writing a test for a bug, run it against the broken code (or
  temporarily re-break it) and see red. A test never seen red is unverified.
- **Property-based tests** (hypothesis) fit the pure math stages: assert invariants over
  generated inputs (fitted center is amplitude-invariant; forward/reverse averaging cancels any
  constant latency by construction).
- **Metamorphic tests** when no oracle exists: transform the input (shift the curve by dx, scale
  amplitude, mirror the scan direction) and assert the recovered center transforms consistently.

## Structure rules

- One behavior per test; the name states the behavior ("rejects a scan whose extremum lies
  outside the window"), not the method name.
- Arrange-act-assert visibly separated.
- Expected literals visible in the test body, not hidden behind helpers or constants files.
