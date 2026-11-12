"""Heart-monitor screen for a 128x64 OLED, in the style of a hospital monitor.

Top band : heart that flashes on every beat, heart rate in big digits, status.
Bottom   : the pulse waveform scrolling across the screen (auto-scaled).

Needs only a framebuf-style object (fill, fill_rect, line, text, show), so it
also runs against a fake display in the PC tests.
"""

WIDTH = 128
HEIGHT = 64
GAP = 6                      # blank columns ahead of the write position (sweep effect)
WAVE_TOP = 29
WAVE_BOTTOM = HEIGHT - 1
MIN_RANGE = 40.0             # counts; keeps noise from being blown up to full height
WEAK_PULSE = 40.0            # pulse height (counts) below which "WEAK" is shown

# 3x5 pixel digits, each row a 3-bit number (MSB = left pixel)
_DIGITS = {
    "0": (7, 5, 5, 5, 7), "1": (2, 6, 2, 2, 7), "2": (7, 1, 7, 4, 7), "3": (7, 1, 7, 1, 7),
    "4": (5, 5, 7, 1, 1), "5": (7, 4, 7, 1, 7), "6": (7, 4, 7, 5, 7), "7": (7, 1, 1, 1, 1),
    "8": (7, 5, 7, 5, 7), "9": (7, 5, 7, 1, 7), "-": (0, 0, 7, 0, 0),
}
# 7x6 heart
_HEART = (0b0110110, 0b1111111, 0b1111111, 0b0111110, 0b0011100, 0b0001000)


class Monitor:
    def __init__(self, oled):
        self.oled = oled
        self.wave = [0.0] * WIDTH
        self.valid = [False] * WIDTH
        self.x = 0               # next column to write
        self._acc = 0.0
        self._n = 0

    def clear_wave(self):
        self.valid = [False] * WIDTH
        self.x = 0
        self._acc = 0.0
        self._n = 0

    def push(self, value):
        """Add one filtered sample; two samples make one column (about 2.6 s across)."""
        self._acc += value
        self._n += 1
        if self._n == 2:
            self.wave[self.x] = self._acc / 2.0
            self.valid[self.x] = True
            self.x = (self.x + 1) % WIDTH
            self._acc = 0.0
            self._n = 0

    def message(self, line1, line2=""):
        o = self.oled
        o.fill(0)
        o.text(line1, 0, 20, 1)
        o.text(line2, 0, 34, 1)
        o.show()

    def _big_text(self, text, x, y, scale):
        o = self.oled
        for ch in text:
            rows = _DIGITS.get(ch)
            if rows is None:
                continue
            for r in range(5):
                bits = rows[r]
                for c in range(3):
                    if bits & (4 >> c):
                        o.fill_rect(x + c * scale, y + r * scale, scale, scale, 1)
            x += 4 * scale

    def _heart(self, x, y, scale):
        o = self.oled
        for r in range(6):
            bits = _HEART[r]
            for c in range(7):
                if bits & (64 >> c):
                    o.fill_rect(x + c * scale, y + r * scale, scale, scale, 1)

    def draw(self, bpm, finger, pulse_height, flash):
        """Render one frame.

        bpm          heart rate (0 = unknown)
        finger       True if a finger is on the sensor
        pulse_height recent peak height of the pulse signal, in counts
        flash        True for a short moment after each beat
        """
        o = self.oled
        o.fill(0)
        if finger and flash:
            self._heart(0, 3, 3)
        else:
            self._heart(2, 6, 2)

        text = "%d" % int(bpm + 0.5) if (finger and bpm > 0) else "--"
        self._big_text(text, 24, 2, 4)
        o.text("BPM", 80, 4, 1)
        if not finger:
            o.text("---", 80, 16, 1)
        elif pulse_height < WEAK_PULSE:
            o.text("WEAK", 80, 16, 1)
        else:
            o.text("OK", 80, 16, 1)
        o.fill_rect(0, 26, WIDTH, 1, 1)

        if not finger:
            o.text("Put finger", 24, 38, 1)
            o.text("on sensor", 28, 50, 1)
        else:
            self._draw_wave()
        o.show()

    def _draw_wave(self):
        o = self.oled
        values = [self.wave[i] for i in range(WIDTH) if self.valid[i]]
        if not values:
            return
        lo, hi = min(values), max(values)
        rng = hi - lo
        if rng < MIN_RANGE:
            rng = MIN_RANGE
        mid = 0.5 * (hi + lo)
        center = 0.5 * (WAVE_TOP + WAVE_BOTTOM)
        half = 0.5 * (WAVE_BOTTOM - WAVE_TOP) - 2
        prev_y = 0
        for x in range(WIDTH):
            y = int(center - (self.wave[x] - mid) / rng * 2.0 * half)
            if y < WAVE_TOP:
                y = WAVE_TOP
            elif y > WAVE_BOTTOM:
                y = WAVE_BOTTOM
            ahead = (x - self.x) % WIDTH
            if x > 0 and self.valid[x] and self.valid[x - 1] and ahead >= GAP:
                o.line(x - 1, prev_y, x, y, 1)
            prev_y = y
