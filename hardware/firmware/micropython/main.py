"""PPG streamer for ESP32-S3 + MAX30102 (MicroPython).

Save this file and max30102.py to the board. It starts on power-up and prints
one CSV line per sample (100 per second) to the USB serial port:

    sample_index,device_us,ir,red,marker

sample_index  running counter; samples dropped by the sensor are added to it,
              so index / rate is always the true time and a loss shows as a gap
device_us     board clock in microseconds (32-bit) when the line was sent; the
              PC uses its long-term slope to measure the real sampling rate
ir, red       raw 18-bit readings of the two LEDs
marker        1 on the first sample after the BOOT button was pressed
              (press it at every inhalation to record the breathing reference)

Lines starting with '#' are status and error messages.
Stop with Ctrl+C (Thonny: the red Stop button).
"""

import sys
import time

from machine import I2C, Pin

from max30102 import MAX30102, OUTPUT_RATE_HZ

# ---------------- settings you may need to change ----------------
PIN_SDA = 8             # MAX30102 SDA
PIN_SCL = 9             # MAX30102 SCL
PIN_BUTTON = 0          # BOOT button (connects GPIO0 to GND when pressed)
LED_CURRENT = 0x1F      # 0x00-0xFF, ~0.2 mA per step; raise if the pulse is weak
POLL_MS = 20            # how often the FIFO is emptied (FIFO holds 320 ms)
DEBOUNCE_MS = 60
STALL_MS = 1000         # no samples for this long = sensor fault
# -----------------------------------------------------------------


def run():
    i2c = I2C(0, sda=Pin(PIN_SDA), scl=Pin(PIN_SCL), freq=400000)
    button = Pin(PIN_BUTTON, Pin.IN, Pin.PULL_UP)
    sensor = MAX30102(i2c)
    write = sys.stdout.write

    index = 0            # sample counter, never reset while running
    clock_us = 0         # 32-bit microsecond clock built from ticks differences
    last_ticks = time.ticks_us()
    ready = False
    marker = 0
    button_was_down = False
    last_press = time.ticks_ms()
    last_sample = time.ticks_ms()

    while True:
        if not ready:
            if not sensor.is_connected():
                write("# ERROR MAX30102 not found on I2C (check 3V3, GND, SDA, SCL); retrying\n")
                time.sleep(1)
                continue
            sensor.setup(LED_CURRENT)
            write("# ppg_streamer-micropython 1.0 | ESP32-S3 + MAX30102\n")
            write("# fs_nominal_hz=%d led_current_reg=0x%02X\n" % (OUTPUT_RATE_HZ, LED_CURRENT))
            write("sample_index,device_us,ir,red,marker\n")
            ready = True
            last_sample = time.ticks_ms()

        # Breath-marker button: one marker per press.
        now_ms = time.ticks_ms()
        down = button.value() == 0
        if down and not button_was_down and time.ticks_diff(now_ms, last_press) > DEBOUNCE_MS:
            marker = 1
            last_press = now_ms
        button_was_down = down

        try:
            n, lost, data = sensor.read_fifo()
        except OSError:
            write("# ERROR I2C read failed; reinitialising\n")
            ready = False
            continue

        if lost:
            index += lost
            write("# WARN fifo_overflow lost=%d\n" % lost)

        if n:
            ticks = time.ticks_us()
            clock_us = (clock_us + time.ticks_diff(ticks, last_ticks)) & 0xFFFFFFFF
            last_ticks = ticks
            for k in range(n):
                write("%d,%d,%d,%d,%d\n" % (index, clock_us, sensor.ir(data, k), sensor.red(data, k), marker))
                index += 1
                marker = 0
            last_sample = now_ms
        elif time.ticks_diff(now_ms, last_sample) > STALL_MS:
            write("# ERROR no samples from MAX30102; reinitialising\n")
            ready = False
            continue

        time.sleep_ms(POLL_MS)


try:
    run()
except KeyboardInterrupt:
    print("# stopped")
