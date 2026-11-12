"""Streaming heart-beat detector for the on-device display (MicroPython / CPython).

One call per raw sample at about 100 Hz. Pure Python, no imports, so the same
file runs on the board and in the PC tests. The C++ twin for the Arduino
sketch is BeatDetector.h; keep the two in step (tests compare them).

This is a display aid: it shows a pulse waveform and an approximate heart
rate. The research analysis never uses it; it works on the raw stream.

Algorithm
---------
1. Remove the slow level (running mean, ALPHA_DC) and flip the sign: the
   MAX30102 counts fall when blood volume rises, so a heartbeat points up.
2. Smooth with a one-pole low-pass (ALPHA_LP, about 5 Hz).
3. A beat is a local maximum that exceeds max(MIN_PEAK, THR_FRAC * envelope),
   where the envelope is the recent peak height (half-life about 3 s), and
   that is at least REFRACT_S after the previous beat and, once a rhythm is
   known, at least 0.55 of the typical interval after it (rejects the small
   second wave of the pulse).
4. Heart rate = 60 / mean of the last 3-5 plausible intervals (largest and
   smallest dropped when there are five).
"""

ALPHA_DC = 0.02
ALPHA_LP = 0.3
DECAY = 0.9977          # envelope decay per sample (half-life ~3 s at 100 Hz)
THR_FRAC = 0.5
MIN_PEAK = 15.0         # raw counts; smaller pulses are treated as "no pulse"
REFRACT_S = 0.30        # 200 beats/min upper limit
MAX_INTERVAL_S = 1.5    # 40 beats/min lower limit
MIN_INTERVAL_FRAC = 0.55
WARMUP_S = 2.0
LOST_S = 4.0            # no beat for this long: heart rate is dropped
MAX_INTERVALS = 5


class BeatDetector:
    """Feed raw IR samples to update(); read bpm, beat and value afterwards.

    Attributes:
        value   smoothed pulse signal (heartbeat points up), raw-count units
        beat    True on the sample at which a beat was just confirmed
        bpm     heart rate in beats/min, or 0 while unknown
        env     recent peak height (for judging signal strength)
    """

    def __init__(self, fs=100, invert=True):
        self.fs = fs
        self.invert = invert
        self.reset()

    def reset(self):
        """Forget everything (call when the finger is lifted)."""
        self.n = 0
        self.dc = None
        self.lp = 0.0
        self.p1 = 0.0
        self.p2 = 0.0
        self.env = 0.0
        self.last_beat = None
        self.intervals = []
        self.value = 0.0
        self.beat = False
        self.bpm = 0.0

    def _typical(self):
        """Typical interval in samples from the stored intervals (0 if unknown)."""
        k = len(self.intervals)
        if k < 3:
            return 0.0
        s = sorted(self.intervals)
        if k >= 5:
            s = s[1:-1]
        return sum(s) / len(s)

    def update(self, raw):
        """Process one raw sample; returns True if a beat was just detected."""
        self.beat = False
        if self.dc is None:
            self.dc = float(raw)
        self.dc += (raw - self.dc) * ALPHA_DC
        x = raw - self.dc
        if self.invert:
            x = -x
        self.lp += (x - self.lp) * ALPHA_LP
        s = self.lp
        self.value = s

        self.env *= DECAY
        if s > self.env:
            self.env = s

        fs = self.fs
        # The candidate peak is the previous sample: it must exceed both neighbours.
        if self.n > WARMUP_S * fs and self.p1 > self.p2 and self.p1 >= s:
            thr = THR_FRAC * self.env
            if thr < MIN_PEAK:
                thr = MIN_PEAK
            if self.p1 > thr:
                peak_at = self.n - 1
                if self.last_beat is None:
                    self.last_beat = peak_at
                else:
                    gap = peak_at - self.last_beat
                    typical = self._typical()
                    floor = REFRACT_S * fs
                    if typical and MIN_INTERVAL_FRAC * typical > floor:
                        floor = MIN_INTERVAL_FRAC * typical
                    if gap >= floor:
                        if gap <= MAX_INTERVAL_S * fs:
                            self.intervals.append(gap)
                            if len(self.intervals) > MAX_INTERVALS:
                                self.intervals.pop(0)
                        else:
                            self.intervals = []     # long pause: restart the rhythm
                        self.last_beat = peak_at
                        self.beat = True
                        typical = self._typical()
                        self.bpm = 60.0 * fs / typical if typical else 0.0

        if self.last_beat is not None and self.n - self.last_beat > LOST_S * fs:
            self.last_beat = None
            self.intervals = []
            self.bpm = 0.0

        self.p2 = self.p1
        self.p1 = s
        self.n += 1
        return self.beat
