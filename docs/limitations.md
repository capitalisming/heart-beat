# Limitations

## State of the evidence

1. **No human data has been collected with this device.** Every numerical
   result in the repository comes from simulated signals. The research
   question is unanswered.
2. **The device is assembled but not yet characterised.** The project owner
   ran the sketch on the board and saw live samples at about 100 per second,
   but no measured sampling-rate accuracy, signal quality across people, or
   OLED behaviour is documented here. The beat detector shown on the display is
   tested only on simulated signals and its heart rate is an approximation.
3. **Synthetic tests are optimistic by construction.** The simulator has a
   perfectly steady breathing rate, a fixed pulse shape and white noise. Real
   breathing wanders, pulse shape changes with posture and temperature, and
   real artifacts are correlated with movement and can mimic slow rhythms.
4. **The quality threshold was tuned on the simulator.** Its false-acceptance
   rate on real signals is unknown.

## Limits of the method

5. **Amplitude modulation is not present in everyone.** Its strength varies
   with the person, posture, breathing depth and rate, and measurement site
   [2, 3]. Shallow breathing may leave no detectable modulation; the correct
   output then is "no estimate".
6. **Peak-to-peak amplitude is not pure AM.** Baseline wander leaks into
   `P − T` in proportion to baseline slope × pulse rise time
   (methodology §2). A correct rate does not show which mechanism produced it.
7. **Cardiac sampling limit.** Breathing faster than half the heart rate
   aliases to a wrong but clean-looking rate that the quality index cannot
   detect. Relevant for slow hearts with fast breathing; not for typical
   resting adults.
8. **Fixed band, 6–30 breaths/min.** Rates outside it cannot be reported.
   Slow non-respiratory oscillations near 0.1 Hz (vasomotor waves) fall
   inside the band and can be mistaken for slow breathing.
9. **Steady-rate assumption.** The periodogram gives the dominant rate of a
   32 s window. Irregular breathing, sighs, speech or apnoea are averaged or
   misrepresented. Resolution is about 1.9 breaths/min; a new independent
   value arrives every 32 s.
10. **Motion and contact pressure.** A bare breakout on a breadboard has no
    mechanical fixation. Finger movement and changing pressure alter pulse
    amplitude directly, in the very quantity being analysed.
11. **Arrhythmia.** Irregular rhythms fail the interval check and are
    rejected as low quality. The method is not applicable to them as built.
12. **Signal polarity and channel are assumed.** The MAX30102 profile inverts
    the signal and analyses the IR column. Some modules swap the LED
    channels; a wrong polarity changes which extrema are taken as peaks.

## Limits of the planned validation

13. **Button reference.** Reaction-time jitter limits it to rates, not breath
    timing; missed presses happen (guarded by an interval check and a tally
    count). If participants mark their own breaths, attention changes their
    breathing and hand movement may disturb the sensor.
14. **Paced breathing is not spontaneous breathing.** Pacing tends to be
    deeper and more regular, which strengthens modulation and flatters the
    method.
15. **Small, healthy, seated sample.** Results describe young healthy adults
    at rest with this sensor on a fingertip. They do not transfer to
    patients, children, exercise, other sites or other sensors.
16. **Selection by the quality gate.** Accuracy is computed on accepted
    windows only. It must be read together with the accepted fraction.
17. **Sampling-rate accuracy.** The rate is measured against the ESP32's
    crystal. That is adequate for this purpose but was not checked against an
    external time standard.

## Not a medical device

This is a teaching and research project. It has no clinical validation, no
regulatory status and no safety analysis. Its output must not be used for
diagnosis, monitoring or any decision about a person's health.
