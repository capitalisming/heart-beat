# Methodology

How a breathing rate is obtained from a PPG trace, why each step is done the
way it is, and where the method stops being trustworthy. Numbered references
are listed in the [README](../README.md#references).

## 1. Physiological basis

A photoplethysmogram (PPG) measures light absorbed by tissue. The pulsatile
part follows the arterial blood volume under the sensor, the slow part
follows venous blood, tissue and sensor contact [5]. Breathing changes
intrathoracic pressure, which changes venous return, stroke volume and
autonomic tone, and it reaches the PPG by three separable routes [3, 4]:

| | Mechanism | What changes in the PPG | Series used here |
|-|-----------|------------------------|------------------|
| **AM** | Stroke volume and vessel filling vary with intrathoracic pressure | Height of each pulse | `A_i = P_i − T_i` |
| **BW** | Venous return and vasomotor tone shift the non-pulsatile blood volume | Baseline on which pulses ride | `B_i = (P_i + T_i)/2` |
| **FM** | Respiratory sinus arrhythmia: heart rate rises on inspiration | Time between pulses | `I_i = t_i − t_(i−1)` |

The strength of each route differs between people, body positions, breathing
rates and measurement sites, and none is reliably present in everyone
[2, 3]. That is why the research question is an empirical one.

**This project studies AM.** BW and FM are implemented only so the same
recordings can be analysed three ways for comparison. They are never mixed
into the primary estimate.

## 2. Signal model

With cardiac frequency `f_c`, breathing frequency `f_r` and unit pulse shape
`g`:

```
x(t) = [1 + m_AM·r(t)] · g(φ(t)) + m_BW·r(t) + d(t) + n(t)
φ(t) = ∫ f_c·[1 + m_FM·r(τ)] dτ ,   r(t) = sin(2π f_r t)
```

`d` is non-respiratory drift, `n` noise. The peak-to-trough height of beat `i`
is then

```
A_i ≈ A_0·[1 + m_AM·r(t_i)] + (baseline slope at t_i) × (rise time)
```

The first term is the amplitude modulation we want. The second term is real
and unavoidable: the trough is measured 0.1–0.3 s before the peak, so a moving
baseline leaks into the difference. **Plain peak-to-peak amplitude therefore
carries AM plus a contribution from BW.** Both oscillate at the breathing
frequency, so the leak does not corrupt the *rate*, but it means a good rate
estimate does not prove that true amplitude modulation was present. The code
also computes a baseline-corrected amplitude (`am_detrended`, peak height
above the line through the two neighbouring troughs) so the two can be
compared on the same data; in the unit tests the correction removes roughly
half of an extreme simulated leak, not all of it.

`src/ppg_resp/synthetic.py` implements exactly this model for testing.

## 3. Processing chain

### 3.1 Acquisition

Raw values and timestamps are stored untouched (`PPGRecording`). For the
firmware stream the time axis is `sample_index / fs`, and `fs` is **measured**
from the slope of the microcontroller clock against the sample counter,
because the MAX30102 runs from its own untrimmed oscillator. Any error in the
assumed sampling rate becomes the same relative error in the breathing rate.

### 3.2 Preprocessing

| Step | Rule | Reason |
|------|------|--------|
| Timestamp check | Must increase strictly; `fs` taken from the median step; declared `fs` differing by > 5 % is reported | A wrong time base silently scales every rate |
| Minimum rate | `fs` ≥ 20 Hz | Below that the systolic peak is sampled too coarsely to measure its height |
| Invalid values | NaN, and values outside the sensor's range (0 = no data, 262143 = 18-bit saturation for the MAX30102) | Saturated samples have no amplitude information |
| Gaps | Bridged by linear interpolation only up to 0.3 s; longer gaps are masked | A long straight line would look like "no pulse, no breathing" |
| Irregular timestamps | Resampled to a uniform grid (linear) | Filters need uniform sampling |
| Orientation | MAX30102 counts are negated | Reflected light falls when blood volume rises; after negation systole is a maximum |
| Spikes | 7-sample Hampel filter, threshold 6 robust SDs with a floor | Removes isolated glitches; at this width a real systolic peak is smooth and is not clipped (tested) |
| Low-pass 8 Hz | 3rd-order Butterworth, forward–backward | Noise suppression for the *measurement* signal; pulse energy lies below ~8 Hz |
| Band-pass 0.5–4 Hz | Same, used only to *locate* beats | 30–240 beats/min |
| Artifact mask | Local range of the pulse band over 2 s more than 3× or less than 0.2× its median | Motion and sensor lift-off |

Two design points matter:

- **Detection and measurement use different signals.** Measuring heights on
  the band-passed signal would let a 0.5 Hz high-pass reshape the slow
  amplitude modulation being studied. Peaks are found on the band-passed
  signal and their values read from the low-passed one.
- **All filters are zero-phase** (`sosfiltfilt`), so beat times are not
  delayed. Cut-offs are validated against the actual Nyquist frequency and
  lowered with a warning if `fs` is too low; an impossible filter raises an
  error instead of running.

### 3.3 Pulse detection

The band-passed signal is divided by a 4 s running estimate of its own SD
(with a floor at 20 % of the typical value so that dropouts are not amplified
into "pulses"). Peaks need a prominence of 1 local SD, roughly a third of a
pulse height, and a spacing of at least 0.3 s (200 beats/min). Because the
threshold is relative, the same settings work for any sensor gain, LED
current or perfusion; a test confirms identical beat positions when the
signal is scaled by 10⁻³ and 5×10⁴. The idea of adapting the threshold to the
local signal follows established PPG beat detectors [9].

Each inter-beat interval is then checked: it must correspond to
35–200 beats/min and lie within ±30 % of the running median of 11 intervals.
A failed check marks a probable missed or false beat. Flags are kept; nothing
is "repaired" by inserting beats.

### 3.4 Peak-to-peak amplitude

For every peak the trough is the minimum of the low-passed signal strictly
between the previous peak and this one (never further back than one typical
beat). This gives the required ordering

```
T_1 < P_1 < T_2 < P_2 < …
```

by construction: a trough cannot be shared by two beats or follow its peak,
and `verify_pairing()` raises if that is ever violated. Then

```
A_i = P_i − T_i ,   timestamp t_i = time of P_i
```

A beat is rejected if `A_i ≤ 0` or outside 0.33–3× the running median of its
neighbours. Respiratory modulation is typically a few percent to a few tens
of percent, so these limits remove artifacts without touching the effect.
Beats overlapping a masked region are dropped.

### 3.5 Respiratory modulation signal

`A(t_i)` has one sample per heartbeat, at irregular times. Consequences:

1. **Sampling limit.** A series sampled at the heart rate cannot represent
   breathing faster than half the heart rate. The search band is therefore
   capped at `HR/2` per window. Above that limit the rhythm *aliases*: breathing
   at 28 /min with a heart rate of 45 /min appears as a clean 17 /min rhythm,
   and nothing in the series can reveal it (kept as a test documenting the
   limitation). For resting adults (HR 60–90, RR 10–20) there is ample margin.
2. **Resampling.** The series is interpolated to a uniform 4 Hz grid. Linear
   interpolation is the default because it cannot overshoot and so cannot
   invent extrema, at the cost of mild attenuation near the cardiac Nyquist
   frequency; shape-preserving PCHIP is available. 4 Hz is far above the
   0.5 Hz band edge.
3. **No fabrication.** The grid runs from the first to the last beat only.
   Where trusted beats are more than 3 s apart the signal is marked invalid
   rather than bridged.

### 3.6 Respiratory band

Zero-phase 2nd-order Butterworth band-pass, default **0.1–0.5 Hz
(6–30 breaths/min)**. This suits resting or lightly paced adults, the
population of the planned experiment. It is not universal: children,
exercise and illness need a wider band, and a narrower band cannot detect
rates outside it (a peak at the band edge is flagged as unreliable). The band
is a setting, and the configuration is rejected if the band does not fit
below the Nyquist frequency of the grid or if the analysis window holds fewer
than 3 cycles of the slowest admissible rate.

### 3.7 Rate estimation

Windows of 32 s every 8 s, as in earlier work [1, 2]. Within a window:

| | Method | Rate | Strength | Weakness |
|-|--------|------|----------|----------|
| A | Peak counting | `60·(n−1)/(t_last − t_first)` over breath maxima with ≥ 25 % prominence | Follows changing rates; closest to "counting breaths" | One noise wiggle adds or removes a breath |
| **B** | **Periodogram (primary)** | Largest peak of the Hann-windowed, zero-padded spectrum in the band, refined by parabolic interpolation | Best frequency resolution for a given window; robust to isolated noise | Assumes a steady rate within the window |
| C | Welch PSD | Same on 16 s segments, 50 % overlap | Lower variance | Half the resolution |
| D | Autocorrelation | Lag of the first major ACF peak | Insensitive to waveform shape | Can lock to a multiple of the period (guarded: first peak within 90 % of the highest) |

```
RR = 60 · f_resp   [breaths/min]
```

**Resolution.** A 32 s window cannot separate two rates closer than about
`60/32 ≈ 1.9` breaths/min. Zero-padding and interpolation locate one clean
peak more finely than that; they do not add resolving power. A new,
independent estimate is available every 32 s; the 8 s step only smooths the
display, and overlapping windows are treated as dependent in the statistics.

**Why B is primary.** With a short fixed window the main risk is a spurious
extra or missing "breath" from noise; the spectral peak integrates over the
whole window and is the least sensitive to that. The other three methods are
reported for every window and their spread is a reliability indicator.

## 4. Signal quality and reliability

Three separate outputs:

1. **Estimate** – a rate, or none.
2. **Signal Quality Index** – 0–100.
3. **Reliability indicators** – method spread, modulation depth, heart rate,
   active band.

### SQI

| Component (0–1) | Meaning |
|-----------------|---------|
| `coverage` | Share of the window covered by trusted beats ≤ 3 s apart |
| `regularity` | Share of inter-beat intervals passing the plausibility check |
| `beat_validity` | Share of detected beats whose amplitude was accepted |
| `concentration` | Share of in-band power inside the main lobe of the dominant peak, rescaled so a flat spectrum scores 0 |
| `dominance` | 1 − (second-largest in-band peak ÷ largest) |

```
SQI = 100 · coverage · regularity · beat_validity · sqrt(concentration · dominance)
```

The pulse-level terms multiply because each is necessary. The two spectral
terms describe one property, "a single clear rhythm", so their geometric mean
enters once.

**The SQI is not a probability of being correct and not a medical measure.**
It orders windows from "one clear rhythm on clean pulses" to "noise". A clean
but wrong rhythm (the aliasing case above, or a non-respiratory oscillation
such as ~0.1 Hz vasomotor waves) scores high.

### Status

| Status | Condition | Output |
|--------|-----------|--------|
| `INSUFFICIENT_DATA` | < 16 usable beats, coverage < 80 %, a gap in the window, heart rate unknown, or the observable band too narrow | No estimate, reason, advice to repeat |
| `LOW_QUALITY` | SQI < threshold, regularity < 70 %, dominant peak at a band edge, or no peak | Estimate **withheld**, reason, advice to repeat |
| `OK` | Otherwise | Estimate reported |

The withheld value is kept in a separate `rr_candidate_bpm` column solely so
that the gate can be audited against ground truth.

### Threshold

The default 70 comes from a synthetic null experiment: on pulses with no
respiratory modulation at all, a threshold of 50 accepted about 21 % of
windows, 70 about 3–5 %, while 2 % amplitude modulation in moderate noise was
still accepted in about 80 % of windows. This is a calibration on a model.
The threshold must be re-derived from real recordings (accepted fraction
versus error against the reference) before it is relied on.

## 5. Ground truth

The reference must be independent of the PPG. Supported:

- **Breath events**: a timestamp per breath, from the marker button recorded
  in the same data stream (so no clock alignment is needed), from an
  observer's file, or from video labelling.
- **Reference waveform**: an independent sensor such as a respiratory belt,
  converted to events by peak detection.
- **Total count**: N breaths in T seconds.

```
RR_reference = N_breaths / T_minutes
```

Applied literally to a short window this is quantised (± 1 breath = ± 1.9 /min
in 32 s) and depends on where the window cuts a cycle. For windows the
default is therefore the interval rule, `60·(n−1)/(t_last − t_first)` over
the events inside the window, which counts whole cycles only. A window whose
longest breath interval exceeds 2.5× its median is given no reference: the
likeliest cause is a missed button press, and a silently biased reference is
worse than a missing one.

A reference labelled as PPG-derived is refused by the code.

**Limits of a button reference.** Human reaction adds a roughly constant
delay and some jitter to each press. A constant delay does not change a rate;
jitter of a few tenths of a second changes a 32 s rate by well under one
breath per minute. If the participant presses the button themselves the
reference is still independent of the *signal*, but attention to breathing
alters breathing, and hand movement may disturb the sensor on the other hand.
An observer pressing the button avoids both.

## 6. Statistics

`error = estimate − reference` (breaths/min).

```
MAE  = (1/N) Σ |ŷ_i − y_i|
RMSE = sqrt( (1/N) Σ (ŷ_i − y_i)² )
Bias = (1/N) Σ (ŷ_i − y_i)
```

plus SD of error, Pearson r, Spearman ρ and Bland–Altman analysis [7].

Rules the code enforces:

- **Unit of analysis.** Overlapping windows of one recording are not
  independent. Window-level numbers are descriptive and get no p-values. One
  pair per recording (mean of accepted windows vs mean reference over the
  same windows) is the unit of inference, clustered by participant.
- **Confidence intervals** come from a bootstrap that resamples whole
  participants, and are only produced with at least 6 participants. With
  fewer, the report says that the numbers describe the sample only.
- **Bland–Altman.** Recording level: bias, 95 % limits of agreement, their
  approximate CIs, and the slope of difference against mean to expose
  proportional bias. Window level: limits from a one-way ANOVA that adds
  between- and within-participant variance, the method for repeated
  measurements where the true value changes [8].
- **Correlation is not agreement.** A method can correlate perfectly with the
  reference and be wrong by a constant or a factor (a unit test demonstrates
  r = 1 with MAE > 5). Correlation also depends on the range of rates in the
  sample; with a narrow range it is weakly determined, and the report warns
  when the reference spans less than 4 breaths/min.
- **Coverage is reported with accuracy.** Only accepted windows have
  estimates, so a strict gate can make accuracy look good by discarding hard
  cases. The accepted fraction and the metrics of all candidates (gate
  ignored) are always shown alongside.
- Statistics that need more data than available are printed as `n/a`.

## 7. Verification versus validation

| | What it shows | State |
|-|---------------|-------|
| Unit tests on synthetic signals and a simulated sensor | The code implements the method correctly, including failure handling | Done: see `tests/`, `results/synthetic_verification/` |
| Public dataset of real PPG (BIDMC) | The code copes with real waveforms from a different sensor and population | Prepared (`scripts/prepare_bidmc.py`), not run |
| Own experiment with the MAX30102 build | The answer to the research question | Not yet performed |

Only the last row can answer how well peak-to-peak modulation agrees with
actual breath counts for this device. Nothing here supports clinical use.
