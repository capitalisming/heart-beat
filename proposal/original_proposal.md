# Part A — Original Proposal

> **Note.** The proposal as originally submitted was not available when this
> repository was prepared. The text below was reconstructed from the project
> brief (title, idea and research question). Replace it with the submitted
> version, or check it against that version, before handing in.

## Project Title

PPG Respiratory Estimator: Extract Breathing Rate Without Extra Sensors

## Background

Respiratory rate is a basic vital sign, but measuring it usually needs a
dedicated sensor: a chest belt, a nasal thermistor or a capnograph. Optical
pulse sensors (photoplethysmography, PPG) are cheap and already present in
watches and fingertip oximeters. Breathing is known to modulate the PPG in
three ways: the height of the pulses (amplitude modulation), the baseline
they ride on, and the interval between them. If one of these can be read
reliably, breathing rate comes for free from a sensor that is already there.

## Problem Statement

Can a low-cost reflective PPG sensor (MAX30102) on a fingertip, with no other
sensor, provide a breathing-rate estimate that agrees with breaths counted by
an independent observer?

## Research Question

How well does optical peak-to-peak modulation correlate with actual breath
counts?

## Hypothesis

In healthy adults seated at rest and breathing at 10–20 breaths per minute,
the respiratory rate estimated from the peak-to-peak amplitude of the
fingertip PPG agrees with observer-counted breaths with:

- **H1 (agreement):** a mean absolute error of at most 2 breaths per minute
  at recording level, and
- **H2 (association):** a Pearson correlation of at least 0.8 across
  recordings spanning 10–20 breaths per minute.

The hypothesis is rejected if the MAE exceeds 2 breaths per minute or the
correlation is below 0.8. The 2 breaths/min margin is roughly the resolution
of a 32 s analysis window and of a manual count over the same time; it is a
study criterion, not a clinical standard. Results are to be reported with the
share of windows that passed the signal-quality check, since accuracy on a
small accepted subset would not support the hypothesis.

## Project Objectives

1. Build a MAX30102 + ESP32-S3 acquisition device that streams raw PPG.
2. Implement pulse detection and peak-to-peak amplitude extraction.
3. Derive the respiratory modulation signal and estimate the breathing rate.
4. Detect and reject recordings of insufficient quality.
5. Collect recordings with an independent breath count under several
   breathing conditions.
6. Quantify agreement with MAE, RMSE, bias, correlation and Bland–Altman
   analysis, and state the limits of the method.

## Proposed Methodology

- **Acquisition:** 100 Hz infrared PPG from the fingertip, 180 s per
  recording, streamed over USB.
- **Processing (Python):** zero-phase filtering; pulse detection;
  `A_i = P_i − T_i` per beat; interpolation to a uniform grid; band-pass
  0.1–0.5 Hz; spectral estimate of the dominant rate in 32 s windows;
  alternative estimators for comparison.
- **Ground truth:** an observer marks every inhalation with a button recorded
  alongside the PPG, plus a tally count.
- **Conditions:** spontaneous breathing and paced breathing at 10, 15 and
  20 breaths per minute; optional supine and repositioned-sensor recordings.
- **Analysis:** recording-level agreement statistics, with participants as
  clusters; pre-specified in `docs/experiment_protocol.md`.

## Hardware and Software

| Hardware | Software |
|----------|----------|
| ESP32-S3-WROOM-1 development board | Arduino IDE, SparkFun MAX3010x library, Adafruit SSD1306 and GFX libraries |
| MAX30102 pulse-oximetry breakout, optional 0.96" OLED | Python 3, NumPy, SciPy, pandas, Matplotlib |
| Breadboard, DuPont wires, USB cable | pytest, Git/GitHub |

## Expected Outcomes

- A working acquisition device and an open, tested analysis package.
- A quantitative answer to the research question for the tested conditions,
  whichever way it falls: either the stated agreement is reached, or the
  study documents by how much and under which conditions it is missed.
- A list of the factors that limit the method (weak modulation, motion,
  breathing-to-heart-rate ratio).

## Project Timeline

| Week | Work |
|------|------|
| 1 | Literature review, requirements, repository setup |
| 2 | Hardware assembly, firmware, first raw recordings |
| 3 | Preprocessing, pulse detection, amplitude extraction |
| 4 | Respiratory signal, rate estimation, quality index, tests |
| 5 | Data collection with ground truth |
| 6 | Statistical evaluation, figures |
| 7 | Documentation, video, final analysis |
