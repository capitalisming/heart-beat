# Part B — Final Analysis

**Status of this document: interim.** It reports what has actually been done.
The experiment on people has not been run, so sections 4 and 5 state that
plainly instead of giving results. Update them from
`results/study/report.md` once recordings exist; do not fill them in from
expectations.

## 1. Which objectives were achieved?

| Objective | Status | Evidence |
|-----------|--------|----------|
| Pulse detection and peak-to-peak extraction | Achieved (software) | `pulse_detection.py`, `amplitude_extraction.py`; tests for heart rates 45–180 /min, signal scale 10⁻³–5×10⁴, one-to-one peak–trough pairing |
| Respiratory signal and rate estimation | Achieved (software) | Four estimators; simulated rates 7–28 /min recovered within 0.5 /min in tests |
| Signal-quality rejection | Achieved (software) | Statuses with reasons; flat, noise-only, too-short, low-rate and gapped inputs handled without a false number in tests |
| Agreement statistics | Achieved (software) | MAE, RMSE, bias, SD, Pearson, Spearman, Bland–Altman incl. repeated measures; checked against hand calculations |
| Acquisition device and firmware | Partly achieved | Assembled by the owner; an Arduino sketch streamed live raw data in the Arduino IDE (a screenshot seen in the session showed continuous sample indices at about 100 Hz). The current sketch adds an OLED heart monitor; its beat detector is tested on simulated signals in C++; not characterised on people |
| Reproducible repository | Achieved | 173 passing tests, central configuration, documented protocol |

## 2. Which objectives were not achieved?

| Objective | Status | What is missing |
|-----------|--------|-----------------|
| Recordings with ground truth | **Not achieved** | No participant has been measured |
| Quantitative answer to the research question | **Not achieved** | Depends on the recordings above and on confirming a clear pulse signal on real fingers |
| Photographs and demonstration video | **Not achieved** | Require the built device |

## 3. Technical difficulties

1. **Baseline wander leaks into peak-to-peak amplitude.** A test with a
   simulated moving baseline and *no* amplitude modulation still produced a
   variation of about 15 % in `P − T`, because the trough is sampled a
   fraction of a second before the peak. A baseline-corrected amplitude was
   added for comparison; it roughly halves the leak in that test. Consequence:
   the primary feature cannot by itself attribute the rhythm to amplitude
   modulation.
2. **False rhythms in signals without breathing information.** With the first
   quality threshold (50), 21 % of simulated windows with no respiratory
   modulation were accepted. Raising it to 70 reduced this to about 5 %, at
   the cost of rejecting some weakly modulated windows. The threshold remains
   a simulator-based choice.
3. **Aliasing above half the heart rate.** The amplitude series has one
   sample per beat. Simulated breathing at 28 /min with a heart rate of
   45 /min was reported as 17 /min with a perfect quality score. It cannot be
   detected from this signal and is documented, with a test, as a limit.
4. **The sensor's sampling clock.** The MAX30102 is not guaranteed to run at
   exactly its nominal rate, and a rate error scales every breathing rate.
   The firmware therefore sends the microcontroller's clock with each sample
   and the host measures the true rate from it.
5. **Dependence between windows.** Overlapping windows would inflate the
   apparent sample size, so the evaluation uses one pair per recording,
   clusters by participant, and withholds intervals for small samples.
6. **No board during development of the software.** The sketch could not be
   compiled or run in the development environment. It was checked against the
   public headers of its libraries, and the beat detector was compiled and
   tested on a PC; wiring, timing and the display were checked only by the
   owner running the project on the real board.
7. **No pulse in the first live data.** The owner's first screenshot showed the
   infrared channel moving by about 10 counts out of 52 000 over 13 samples,
   consistent with no finger or poor contact, not with a clear pulse wave.
   Whether a good wave is obtained is the first thing to confirm.

## 4. Results obtained

**On people: none.**

**On simulated signals** (software verification only; see
`results/synthetic_verification/report.md`):

- 40 simulated recordings (8 simulated participants × 5 rates from 8 to
  24 /min): recording-level MAE 0.003 breaths/min, bias 0.000; all 440
  windows accepted.
- No-modulation control: 4.9 % of 264 windows wrongly accepted.
- Accepted fraction falls from 100 % to 72 % to 11 % as noise rises from
  0.10 to 0.20 to 0.40 of the pulse height; error of accepted windows stays
  at or below 0.5 breaths/min on average.

These figures show that the implementation matches the mathematics. The
simulator has a perfectly steady breathing rate and white noise, so they say
nothing about accuracy on real signals.

## 5. How well does PPG peak-to-peak modulation correlate with actual breathing?

**Not yet known.** No measurement of real breathing has been compared with
the estimator, so neither hypothesis H1 (MAE ≤ 2 breaths/min) nor H2
(r ≥ 0.8) has been tested. Reporting a correlation here would be invention.

What the work so far does establish is how the answer has to be read once
data exist:

- A correlation coefficient alone will not answer the question; error, bias
  and limits of agreement are needed, together with the fraction of windows
  the quality gate accepted.
- A good result under paced breathing may not carry over to spontaneous
  breathing.
- A good rate estimate from `P − T` may partly be owed to baseline wander.

## 6. Limitations identified

See `docs/limitations.md`. The most consequential: no human data; hardware
unverified; amplitude modulation absent in some people; aliasing above half
the heart rate; sensitivity to motion and contact pressure; quality threshold
calibrated on simulations; a button reference with human timing error.

## 7. What would be improved with more time and resources

1. Verify the pulse signal on real fingers, then run the protocol with at
   least 6 participants: this is the missing core of the project.
2. Test the algorithm on the public BIDMC dataset to see its behaviour on
   real PPG with an independent reference before the own experiment.
3. Calibrate the quality threshold on real recordings, using separate
   participants for tuning and for reporting.
4. Add a respiratory belt as an automatic reference to remove human timing
   error.
5. Stabilise the finger with a cradle or clip.
6. Compare amplitude, baseline and frequency modulation on the same
   recordings and examine whether combining them is justified.

---

## Checklist for completing this analysis

- [ ] Replace section 4 "On people" with the numbers from `results/study/report.md`
- [ ] Answer section 5 with MAE, bias, limits of agreement, r and ρ, accepted fraction, n
- [ ] State whether H1 and H2 were supported or rejected
- [ ] Add the scatter, error-distribution and Bland–Altman figures
- [ ] Move rows from section 2 to section 1 as they are completed
- [ ] Record every deviation from the protocol
