# Engine and display review — 22 October 2026

Reviewed against the supplied `ProcessLab-Engine-Study-Guide.pdf`, the implementation,
and BPI 2017 model `0a750cda862abda411057668`.

The model agrees with the guide: 24,041 training cases, 6,300 held-out cases,
1,159 excluded boundary cases, and 137 resource profiles. The worked case `d1_c4`
reproduces the guide's activity/resource sequence, durations and timestamps at seed 42.
The model's training-event overlap rate is 15.89% (15.9% rounded); the README's
15.4% was stale.

## Corrections and interpretation

The three-way time breakdown in the guide is incomplete for tasks spanning shifts.
`finish_work()` already pauses these tasks overnight and over weekends, so cycle
duration was correct. However, those pauses were neither processing time nor the
reported pre-start queue/calendar wait. The engine now exports
`calendar_pause_seconds` per event and reports `mean_processing_hours` and
`mean_calendar_pause_hours` alongside the existing metrics. The Results tab shows
all four components and their shares. Old saved results remain readable and ask
for a rerun to obtain the new breakdown.

For a 30-day baseline, seed 42, one repetition:

| Component | Mean hours per case | Share |
|---|---:|---:|
| Processing | 1.5434 | 0.468% |
| Queue/off-shift wait before starting | 66.0314 | 20.039% |
| Off-shift pauses during tasks | 1.2298 | 0.373% |
| Residual delay | 260.7087 | 79.119% |
| Total case duration | 329.5132 | 100% |

The guide's roughly 78/21/1 split and fidelity percentages are results for particular
experiment settings, not constants. Residual delay bundles unobserved causes and
model error; describing all of it as proven external/customer waiting overstates
what the log establishes. Staffing conclusions are scenario findings under the
model assumptions, not causal evidence that hiring cannot help the real process.

The score button previously inherited a 20-second HTTP timeout although its UI
describes a roughly one-minute calculation. It now allows 15 minutes and uses the
model ID attached to the displayed experiment, rather than a subsequently selected
model. The learned model and simulation scheduling rules are unchanged by this review.

## Verification

- Python regression suite: 97 passed, including shift-pause accounting and unchanged-scenario pairing.
- JavaScript syntax checks passed for both browser scripts.
- Edge browser checks using the local scripted provider: desktop/mobile layouts,
  analyst charts, assistant confirmation/recovery, scenario submission, saved results,
  CSV export and the new time breakdown.
- Scoring regression: a response delayed beyond 20 seconds and a selected model that
  differs from the displayed experiment.

The Edge run passed with no browser exceptions; desktop and mobile screenshots
were inspected. The real scoring run (two repetitions, seed 42, 78-day window)
reproduced the guide's rounded values: NGD 0.167, AEDD 147.766, CEDD 2.171,
REDD 30.341 and CTDD 53.178.

The raw-XES reproduction test is excluded from the default suite and requires the
original XES file. The review does not establish causal validity or remove the
documented calendar, sequential-case and duration-outlier limitations.
