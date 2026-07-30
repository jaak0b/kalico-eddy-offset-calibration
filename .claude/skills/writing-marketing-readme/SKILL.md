---
name: writing-marketing-readme
description: Use when writing or revising the README, release announcements, or any outward-facing text meant to attract new users. Evidence-backed rules for converting a technical hobbyist audience.
---

# Writing marketing text for a technical hobbyist audience

These rules come from a research pass (2026-07). Sourcing, stated honestly: the correlational README/popularity study arXiv 2206.10772 backs the structural basics (a one-line purpose, usage and install sections, images, lists, license, and contribution/reference sections all correlate with repo popularity; correlation, not causation). PostHog's own developer-marketing writing backs the anti-hype and specificity rules. A third-party analysis of Tailscale's Hacker News launches (markepear.dev) backs the peer-voice tone claims. The READMEs of ripgrep and fzf are the concrete pattern examples (fzf for hero plus early screenshot, ripgrep for honest limitations). Rules not traceable to those sources are marked as heuristics. These rules apply to the README and any outward-facing text; in-app text is governed by CLAUDE.md rule 7 instead. The audience here is 3D-printing hobbyists running Klipper/Kalico toolchangers (Voron community and similar): assume fluency in printer and Klipper vocabulary (toolhead, nozzle offset, probe, macro, klippy).

## The register: a knowledgeable peer, not a manual and not an ad

The voice that converts technical hobbyists is a third thing between neutral manual prose and marketing copy: a practitioner talking directly to another practitioner. Here is the problem, here is what this does about it.

- First person is an asset when it carries a real technical story (a real toolchanger owner tired of cleaning nozzles before every offset calibration). It becomes a liability the moment it turns into persuasion or self-congratulation.
- Zero hype: no superlatives, no "revolutionary", "effortless", "magic". Developers and hobbyists detect marketing spin instantly and find it patronizing. Assume the reader is smart.
- Specifics carry the weight that adjectives cannot: exact sensor facts ("LDC1612 inductance-to-digital converter, 250 Hz sample stream"), exact command names, exact behavior ("fits the symmetry center, so nozzle material and dirt do not shift the result"). Naming the mechanism is credibility signaling.
- Section split: the hero line and the "why this exists" section may use peer voice and first person. Everything below (install steps, how it works, requirements, limitations) is plain technical prose.

## Structure (in order)

1. **Hero**: one factual spec-like sentence stating function, audience, and payoff mechanism. No slogan.
2. **Badges**: license, version, Kalico compatibility. A cheap credibility signal.
3. **Proof above the fold**: a photo of the board on the bed plus a console screenshot of a calibration run with real offset numbers and repeatability spread. Images correlating with popularity is study-backed.
4. **Friction-killing microcopy**: the genuine trust signals ("non-contact, works with dirty nozzles; no toolhead hardware, one 4-wire board at the bed edge; plain I2C, no extra MCU, nothing to flash").
5. **Why / how it works**: the mechanism is the selling point. The eddy sensor sees only metal; the symmetry-center fit makes the result independent of nozzle material and drool.
6. **What you get**: name the exact commands (EDDY_CALIBRATE_TOOL, EDDY_LOCATE, EDDY_QUERY, EDDY_SET_Z_REF) and what each prints.
7. **Honest limitations**: requirements and what it cannot do (Kalico only; per-tool Z needs a one-time contact reference; self-assembled sensor board; XY accuracy numbers stated as measured, not promised). Owning limits up front reads as expertise, not weakness.
8. **Install / configure / license, plus a contributing or reference section**: plain technical prose, symlink install path first.

## Proof beats claims

- Balanced factual comparison against the status quo (contact pins needing spotless nozzles, camera systems needing lighting and setup) is a strong trust device; FUD destroys trust (PostHog).
- Heuristics (general practice, not sourced studies): real user quotes beat zero testimonials, but never fabricate and add social proof only as it accrues; do not build the pitch on vanity metrics.

## Scannability

Nobody reads top to bottom. Short sections with headers, short paragraphs, bolded key terms, one visible screenshot or entity list early. No prose walls.

## Discipline carried over from the project rules

- Terminology: CLAUDE.md rule 7's terminology clause binds all outward-facing text (DCC and HA terms verbatim, one term per concept, no invented synonyms).
- Honesty: no setup-specific claim stated as a general truth, no capability overclaim.
- No em-dash (rule 6) and no AI attribution (rule 4) anywhere, including the README.
