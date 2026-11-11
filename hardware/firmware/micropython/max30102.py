"""Minimal MAX30102 driver for MicroPython (ESP32-S3).

No external libraries: only an I2C object with ``readfrom_mem``,
``readfrom_mem_into`` and ``writeto_mem`` is needed, so the same file also
runs under ordinary Python against a simulated bus (see tests/test_firmware.py).

Configuration written by ``setup()`` (register values from the MAX30102
datasheet):

* SpO2 mode: red and IR LEDs both active
* 400 samples/s internally, 4x on-chip averaging -> 100 samples/s out
* 411 us LED pulses -> 18-bit ADC resolution
* ADC full scale 4096 nA
* FIFO roll-over disabled: when the 32-sample FIFO is full, new samples are
  dropped and counted in OVF_COUNTER, so losses are visible to the caller
"""

import time

ADDRESS = 0x57          # fixed 7-bit I2C address
PART_ID = 0x15          # value of REG_PART_ID for a MAX30102

REG_INT_STATUS_1 = 0x00
REG_FIFO_WR_PTR = 0x04
REG_OVF_COUNTER = 0x05
REG_FIFO_RD_PTR = 0x06
REG_FIFO_DATA = 0x07
REG_FIFO_CONFIG = 0x08
REG_MODE_CONFIG = 0x09
REG_SPO2_CONFIG = 0x0A
REG_LED1_PA = 0x0C      # red LED current
REG_LED2_PA = 0x0D      # IR LED current
REG_PART_ID = 0xFF

MODE_RESET = 0x40
MODE_SPO2 = 0x03        # red + IR
FIFO_AVG_4 = 0x40       # SMP_AVE = 010 (4 samples), roll-over off
SPO2_CFG = 0x2F         # ADC range 4096 nA | 400 sps | 411 us (18 bit)

FIFO_DEPTH = 32
BYTES_PER_SAMPLE = 6    # 3 bytes red + 3 bytes IR
SAMPLE_MASK = 0x03FFFF  # 18 valid bits
OUTPUT_RATE_HZ = 100    # 400 sps / 4


class MAX30102:
    """Reads raw red/IR samples from the sensor FIFO."""

    def __init__(self, i2c, address=ADDRESS):
        self.i2c = i2c
        self.address = address
        self._buf = bytearray(FIFO_DEPTH * BYTES_PER_SAMPLE)
        self._view = memoryview(self._buf)

    def _read(self, reg):
        return self.i2c.readfrom_mem(self.address, reg, 1)[0]

    def _write(self, reg, value):
        self.i2c.writeto_mem(self.address, reg, bytes([value]))

    def is_connected(self):
        """True if a MAX30102 answers at the address."""
        try:
            return self._read(REG_PART_ID) == PART_ID
        except OSError:
            return False

    def setup(self, led_current=0x1F):
        """Reset and configure the sensor for 100 Hz, 18-bit, red + IR.

        led_current: 0x00-0xFF, about 0.2 mA per step (0x1F is roughly 6 mA).
        Raise it if the pulse is weak, lower it if readings sit at 262143.
        """
        self._write(REG_MODE_CONFIG, MODE_RESET)
        for _ in range(50):                      # reset bit clears itself
            if not self._read(REG_MODE_CONFIG) & MODE_RESET:
                break
            time.sleep(0.01)
        self._write(REG_FIFO_CONFIG, FIFO_AVG_4)
        self._write(REG_SPO2_CONFIG, SPO2_CFG)
        self._write(REG_LED1_PA, led_current)
        self._write(REG_LED2_PA, led_current)
        self._write(REG_MODE_CONFIG, MODE_SPO2)
        self.clear_fifo()

    def clear_fifo(self):
        self._write(REG_FIFO_WR_PTR, 0)
        self._write(REG_OVF_COUNTER, 0)
        self._write(REG_FIFO_RD_PTR, 0)

    def read_fifo(self):
        """Fetch every sample waiting in the FIFO.

        Returns (n, lost, data):
          n     number of samples fetched (0-32)
          lost  samples the chip dropped since the previous read (0-31)
          data  memoryview of n*6 bytes; decode with red()/ir()
        """
        lost = self._read(REG_OVF_COUNTER) & 0x1F
        write_ptr = self._read(REG_FIFO_WR_PTR) & 0x1F
        read_ptr = self._read(REG_FIFO_RD_PTR) & 0x1F
        n = (write_ptr - read_ptr) & 0x1F
        if n == 0 and lost:
            n = FIFO_DEPTH                       # pointers equal because the FIFO is full
        if n == 0:
            return 0, lost, self._view[0:0]
        data = self._view[0 : n * BYTES_PER_SAMPLE]
        self.i2c.readfrom_mem_into(self.address, REG_FIFO_DATA, data)
        return n, lost, data

    @staticmethod
    def red(data, k):
        """Red value of sample k in a buffer returned by read_fifo()."""
        o = k * BYTES_PER_SAMPLE
        return ((data[o] << 16) | (data[o + 1] << 8) | data[o + 2]) & SAMPLE_MASK

    @staticmethod
    def ir(data, k):
        """IR value of sample k in a buffer returned by read_fifo()."""
        o = k * BYTES_PER_SAMPLE + 3
        return ((data[o] << 16) | (data[o + 1] << 8) | data[o + 2]) & SAMPLE_MASK
