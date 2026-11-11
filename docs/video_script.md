# Video demonstration script (target 4 minutes)

**The video does not exist yet.** This is the plan for recording it. Record
real screens and the real device; do not substitute the simulated figures
from `results/synthetic_verification/` for device results. If a step fails on
camera (for example a low-quality status), keep it: it demonstrates the
quality gate.

## Before recording

- [ ] Device assembled and passing the bring-up checklist (`hardware/README.md`)
- [ ] One real 180 s recording made with button markers, plus the tally count
- [ ] If available, the study evaluation in `results/study/`
- [ ] Terminal with a large font; image viewer for the figures
- [ ] Phone or camera on a stand for the hardware shots; a quiet room
- [ ] A second person to act as observer and press the button

## Shot list

| # | Time | On screen | Narration |
|---|------|-----------|-----------|
| 1 | 0:00–0:20 | Title card, then you with the device | "This is the PPG Respiratory Estimator. It measures breathing rate with only an optical pulse sensor, no chest belt or airflow sensor. The question I set out to answer: how well does the peak-to-peak modulation of the pulse signal agree with actual breath counts?" |
| 2 | 0:20–0:50 | Close-up of the breadboard; point at each part; the wiring diagram | "The sensor is a MAX30102, which shines red and infrared light into the fingertip and measures what comes back. An ESP32-S3 reads it over I2C on four wires and streams one hundred samples a second to the computer over USB. The button on the board marks each breath for the ground truth." |
| 3 | 0:50–1:20 | Finger on sensor; terminal running `record_serial.py`; sample counter climbing; observer pressing the button | "I start a three-minute recording. The observer watches my breathing and presses the button at every inhalation. Those presses are stored in the same data stream, so they are synchronised with the signal automatically." |
| 4 | 1:20–1:40 | Figure 1 (raw) then figure 2 (filtered) | "This is the raw signal. Each ripple is a heartbeat. The software checks the timing, removes glitches and filters noise without shifting the signal in time." |
| 5 | 1:40–2:05 | Figure 3 (peaks and troughs), zoomed | "It finds every pulse: the red triangles are systolic peaks, the green ones the troughs before them. The height from trough to peak is the peak-to-peak amplitude, one value per heartbeat." |
| 6 | 2:05–2:30 | Figure 4 (amplitude per beat) then figure 6 (respiratory band with button markers) | "Plotted over time, that amplitude goes up and down. After keeping only the frequencies where breathing can be, this is the respiratory signal. The orange lines are the observer's button presses." *Say what the plot shows: whether the waves line up with the markers or not.* |
| 7 | 2:30–2:55 | Spectrum figure; terminal output with the rate, SQI and reference | "The strongest frequency in this signal gives the breathing rate. For this recording the estimate is … breaths per minute and the counted reference is …. The quality index says how clearly a single rhythm stands out; when it is too low the program refuses to give a number." *Read the actual values.* |
| 8 | 2:55–3:35 | `report.md`; figure 8 (scatter) and figure 10 (Bland–Altman) | "Across … participants and … recordings, the mean absolute error was …, the bias …, and ninety-five percent of differences fell between … and …. The quality gate accepted … percent of windows." *Use the numbers in your report. If the study is small, say that these describe your sample only.* |
| 9 | 3:35–4:00 | You, or a closing slide with limitations | "So, how well does it work? …" *Give the honest answer from your data, including where it failed: weak modulation in some people, motion, the reference's own uncertainty.* "It is a research prototype, not a medical device. Code, protocol and data formats are in the repository." |

## Materials to show

1. The assembled device and its parts.
2. Live terminal: recording, then `run_estimation.py` output.
3. Figures 1–7 of **your** recording (`results/runs/<name>/figures/`).
4. Figures 8–10 and `report.md` of **your** study, if completed.
5. The repository page (README, commit history).

## If the study is not finished when you record

Show one real recording end to end (shots 1–7) and say plainly that the
multi-participant evaluation is pending. Do not present the simulated
agreement figures as experimental results; if you show them at all, say on
camera that they are simulations used to test the software.
