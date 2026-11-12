"""Minimal SSD1306 OLED driver (128x64, I2C) for MicroPython.

Same idea as the driver in the MicroPython documentation, kept in this
repository so nothing has to be downloaded. The class is a framebuf.FrameBuffer,
so fill(), pixel(), line(), rect(), fill_rect(), hline(), text() ... all work;
call show() to send the picture to the display.

Display memory is shared with the transmit buffer, so show() sends the frame
without making a copy.
"""

import framebuf

ADDRESS = 0x3C   # some modules use 0x3D


class SSD1306_I2C(framebuf.FrameBuffer):
    def __init__(self, width, height, i2c, addr=ADDRESS):
        if height not in (32, 64) or width != 128:
            raise ValueError("only 128x64 and 128x32 displays are supported")
        self.i2c = i2c
        self.addr = addr
        self.width = width
        self.height = height
        self.pages = height // 8
        # One I2C transfer: control byte 0x40 ("data follows"), then the frame.
        self._tx = bytearray(1 + self.pages * width)
        self._tx[0] = 0x40
        super().__init__(memoryview(self._tx)[1:], width, height, framebuf.MONO_VLSB)
        self._init_display()

    def _cmd(self, *commands):
        for c in commands:
            self.i2c.writeto(self.addr, bytes((0x80, c)))   # 0x80: single command byte follows

    def _init_display(self):
        self._cmd(
            0xAE,                       # display off
            0x20, 0x00,                 # horizontal addressing mode
            0x40,                       # start line 0
            0xA1,                       # column 127 is segment 0 (not mirrored)
            0xA8, self.height - 1,      # multiplex ratio
            0xC8,                       # scan from COM[N-1] to COM0 (not upside down)
            0xD3, 0x00,                 # no vertical offset
            0xDA, 0x12 if self.height == 64 else 0x02,   # COM pin layout
            0xD5, 0x80,                 # clock divide / oscillator
            0xD9, 0xF1,                 # pre-charge
            0xDB, 0x30,                 # VCOMH level
            0x81, 0xFF,                 # contrast
            0xA4,                       # follow RAM contents
            0xA6,                       # normal (not inverted)
            0x8D, 0x14,                 # internal charge pump on
            0xAF,                       # display on
        )
        self.fill(0)
        self.show()

    def show(self):
        """Send the frame buffer to the display."""
        self._cmd(0x21, 0, self.width - 1, 0x22, 0, self.pages - 1)
        self.i2c.writeto(self.addr, self._tx)

    def poweroff(self):
        self._cmd(0xAE)

    def poweron(self):
        self._cmd(0xAF)

    def contrast(self, value):
        self._cmd(0x81, value)
