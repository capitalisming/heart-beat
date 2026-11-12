# Experiment protocol

A reproducible procedure for answering: *how well does optical peak-to-peak
modulation agree with actual breath counts?*

This is a student engineering study on healthy adult volunteers. It is not a
clinical trial and its results cannot establish medical suitability. Check
whether your institution requires ethics approval or a supervisor's sign-off
for measurements on people, and obtain it before recording anyone but
yourself.

## 1. Participants and safety

- Healthy adult volunteers who consent after reading what will be measured
  and how the data will be stored. Anyone may stop at any time.
- Target at least 6 participants: below that the analysis reports no
  confidence intervals. More is better; state the number you achieved.
- **No breath-holding, no forced hyperventilation, no rates that feel
  uncomfortable.** Paced conditions stay within ordinary breathing
  (10–20 breaths/min). If a participant feels light-headed or uncomfortable,
  stop the condition and let them breathe normally. People with respiratory
  or cardiac conditions should not take part in paced conditions.
- The sensor's LEDs are low power and the device is powered from a computer's
  USB port. Use a laptop on battery if you want the participant galvanically
  isolated from mains equipment.

## 2. Anonymisation and storage

- Assign codes `P01`, `P02`, … Keep the code–name list on paper or in a
  separate private file, never in the repository.
- File names and metadata contain only the code, session and condition.
- `data/raw/` and `data/reference/` are git-ignored. Publish recordings only
  if participants agreed to that explicitly.
- Raw files are never edited or overwritten (the recorder refuses to).

## 3. Setup

- Participant seated, back supported, forearm resting on the table, hand at
  about heart level, relaxed.
- Index fingertip resting on the sensor with light, steady pressure. Too much
  pressure flattens the pulse; too little lets ambient light and motion in.
  Covering the sensor and finger with a dark cloth reduces ambient light.
- Room at a comfortable temperature; cold fingers have weak pulses. Allow
  2 minutes of rest before the first recording.
- The **observer** sits where they can see the participant's chest or abdomen
  and presses the board's BOOT button at the start of every inhalation. The
  observer does not see the live PPG.

## 4. Conditions

Each recording lasts **180 s**. With 32 s windows every 8 s this gives 19
windows, of which about 5 are non-overlapping.

| Code | Condition | Breathing | Purpose |
|------|-----------|-----------|---------|
| `rest` | Quiet sitting | Spontaneous | Natural rate and depth |
| `paced_10` | Paced, slow | 10 /min (3 s in, 3 s out) | Low end of the range |
| `paced_15` | Paced, normal | 15 /min (2 s in, 2 s out) | Typical resting rate |
| `paced_20` | Paced, faster | 20 /min (1.5 s in, 1.5 s out) | Upper end, still comfortable |
| `rest_supine` | Lying down (optional) | Spontaneous | Posture changes venous return, hence modulation strength |
| `reposition` | Quiet sitting after lifting and replacing the finger (optional) | Spontaneous | Sensitivity to sensor placement |

Pacing uses a visual or audible metronome that the participant follows at a
comfortable depth. Paced conditions widen the range of rates, which is needed
for correlation to be informative; spontaneous conditions show performance
when the rate is not imposed. Report them separately as well as together.

Order: `rest` first, then the paced conditions in an order randomised per
participant, with 1 minute of normal breathing in between. Two repetitions of
each condition per participant if time allows.

Low-quality data needs no deliberate provocation: note honestly when a
recording had movement, talking or coughing, and keep it. Such recordings
test the quality gate.

## 5. Recording procedure

For each condition:

1. Start the metronome if the condition is paced; let the participant settle
   for 20 s.
2. Start the recording (on Windows write `ppg scripts\record_serial.py ...` and a port like `COM5`):
   ```bash
   python scripts/record_serial.py --port <port> --duration 180 --participant P01 --session S1 --condition paced_15 --analyze
   ```
3. The observer presses the button at each inhalation for the whole 180 s.
4. Independently, the observer (or a second person) counts breaths over the
   full recording with a tally counter or on paper and writes down the total.
   This second, coarser reference guards against missed button presses.
5. Note anything unusual (cough, movement, sensor slipped) in `--notes` or a
   lab notebook.

**Synchronisation.** Button markers travel in the same data stream as the PPG
samples and share its sample counter, so no clock alignment between devices
is needed. Human reaction time adds a few tenths of a second of delay and
jitter per press, which is negligible for a rate over 32 s but rules out
breath-by-breath phase analysis. If a separate reference device is used
instead (belt, video), it must be aligned to the PPG by a shared event, for
example a tap that both record, and the measured offset entered as
`time_offset_s` in the manifest. Without such an event, state the
synchronisation as a limitation and use whole-recording counts only.

## 6. Manifest

List every recording, including failed ones, in `data/manifest.csv`:

```csv
recording_id,participant_id,session_id,condition,ppg_file,fs_hz,channel,reference_type,reference_file,n_breaths,count_duration_s,time_offset_s,reference_source
P01_S1_rest,P01,S1,rest,raw/P01_S1_rest.csv,100,ir,markers,,,,,observer button
P01_S1_paced_15,P01,S1,paced_15,raw/P01_S1_paced_15.csv,100,ir,markers,,,,,observer button
```

Paths are relative to the manifest. `reference_type` is `markers`, `events`,
`waveform` or `count`. To analyse the tally counts instead of the markers,
copy the manifest, set `reference_type` to `count` and fill `n_breaths` and
`count_duration_s`.

## 7. Analysis

```bash
python scripts/run_evaluation.py data/manifest.csv --outdir results/study --recording-figures
```

Decide **before** looking at results:

- Primary outcome: recording-level MAE and Bland–Altman limits of agreement
  for the AM feature with the periodogram method, accepted windows only,
  reported together with the accepted fraction.
- Hypothesis test: as stated in `proposal/original_proposal.md`.
- Settings are frozen at the committed `config/settings.yaml`. If the SQI
  threshold or any other setting is tuned on the collected data, say so, and
  report results for the original settings too. Tuning and testing on the
  same recordings overstates accuracy; with enough participants, tune on some
  and report on the others.

Secondary analyses: by condition; AM versus `am_detrended`, BW and FM on the
same recordings (`--feature`); markers versus tally counts as reference;
agreement of the four estimation methods.

## 8. What to report

- Number of participants, recordings and windows; how many recordings were
  excluded and why.
- Accepted fraction of windows.
- MAE, RMSE, bias, SD of error, limits of agreement, with intervals where the
  sample allows.
- Pearson and Spearman coefficients together with the range of reference
  rates, and never as the sole evidence of accuracy.
- The Bland–Altman plot and whether error grows with rate.
- Every deviation from this protocol.
