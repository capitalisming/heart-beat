"""PPG streamer with heart monitor for ESP32-S3 + MAX30102 (MicroPython).

Upload this file as main.py together with max30102.py, beat.py, display.py and
ssd1306.py. It starts on power-up.

Serial output (SERIAL_MODE below):
  0  CSV for recording (default), 100 lines per second:
         sample_index,device_us,ir,red,marker
     plus one "# HR ..." comment line per second
  1  readable text, 10 lines per second:
         IR=52822 RED=58361 PULSE=-12 BPM=72 FINGER=yes MARK=0
  2  numbers for Thonny's Plotter (View -> Plotter):  PULSE, BPM

sample_index  running counter; samples dropped by the sensor are added to it,
              so index / rate is always the true time and a loss shows as a gap
device_us     board clock in microseconds when the line was sent; the PC uses
              its long-term slope to measure the real sampling rate
ir, red       raw 18-bit readings of the two LEDs
marker        1 on the first sample after the BOOT button was pressed
              (press it at every inhalation to record the breathing reference)

Lines starting with '#' are status and error messages.

If an SSD1306 OLED (128x64) is connected to the same SDA/SCL pins it shows the
heart rate and the live pulse waveform. Without it everything else still works.
The heart rate shown comes from a simple display-only detector (beat.py); the
research analysis works on the raw stream. Not a medical device.

Stop with Ctrl+C (Thonny: the red Stop button).
"""

import sys
import time

from machine import I2C, Pin

from beat import BeatDetector
from max30102 import MAX30102, OUTPUT_RATE_HZ

# ---------------- settings you may need to change ----------------
SERIAL_MODE = 0         # 0 = CSV, 1 = readable text, 2 = Plotter
PIN_SDA = 8             # MAX30102 and OLED SDA
PIN_SCL = 9             # MAX30102 and OLED SCL
PIN_BUTTON = 0          # BOOT button (connects GPIO0 to GND when pressed)
OLED_ADDRESS = 0x3C     # some modules use 0x3D
LED_CURRENT = 0x1F      # 0x00-0xFF, ~0.2 mA per step; raise if the pulse is weak
POLL_MS = 10            # how often the FIFO is emptied (FIFO holds 320 ms)
DRAW_MS = 40            # screen refresh period (25 frames/s)
DEBOUNCE_MS = 60
STALL_MS = 1000         # no samples for this long = sensor fault
FINGER_ON = 20000       # IR level above which a finger is assumed
FINGER_OFF = 12000      # ... and below which it is assumed gone
FLASH_MS = 150          # how long the heart stays big after a beat
# -----------------------------------------------------------------


def start_display(i2c):
    """Return a Monitor if an OLED answers on the bus, else None."""
    if OLED_ADDRESS not in i2c.scan():
        sys.stdout.write("# WARN OLED not found (check SDA, SCL, VCC, GND, address 0x3C/0x3D); continuing without display\n")
        return None
    try:
        from display import Monitor
        from ssd1306 import SSD1306_I2C

        return Monitor(SSD1306_I2C(128, 64, i2c, OLED_ADDRESS))
    except Exception as exc:  # a display problem must never stop the recording
        sys.stdout.write("# WARN OLED could not be started: %r\n" % (exc,))
        return None


def run():
    i2c = I2C(0, sda=Pin(PIN_SDA), scl=Pin(PIN_SCL), freq=400000)
    button = Pin(PIN_BUTTON, Pin.IN, Pin.PULL_UP)
    sensor = MAX30102(i2c)
    monitor = start_display(i2c)
    detector = BeatDetector(OUTPUT_RATE_HZ, True)
    write = sys.stdout.write

    index = 0            # sample counter, never reset while running
    clock_us = 0         # 32-bit microsecond clock built from tick differences
    last_ticks = time.ticks_us()
    ready = False
    marker = 0
    finger = False
    button_was_down = False
    last_press = time.ticks_ms()
    last_sample = time.ticks_ms()
    last_draw = time.ticks_ms()
    last_status = time.ticks_ms()
    flash_until = 0

    if monitor:
        monitor.message("Heart monitor", "Starting sensor")

    while True:
        if not ready:
            if not sensor.is_connected():
                write("# ERROR MAX30102 not found on I2C (check 3V3, GND, SDA, SCL); retrying\n")
                if monitor:
                    monitor.message("MAX30102 not found", "check the wiring")
                time.sleep(1)
                continue
            sensor.setup(LED_CURRENT)
            write("# micropython_monitor 1.1 | ESP32-S3 + MAX30102\n")
            write("# fs_nominal_hz=%d led_current_reg=0x%02X oled=%s\n"
                  % (OUTPUT_RATE_HZ, LED_CURRENT, "yes" if monitor else "no"))
            if SERIAL_MODE == 0:
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
                ir = sensor.ir(data, k)
                red = sensor.red(data, k)

                was_present = finger
                if ir > FINGER_ON:
                    finger = True
                if ir < FINGER_OFF:
                    finger = False
                if was_present and not finger:
                    detector.reset()
                    if monitor:
                        monitor.clear_wave()
                if finger:
                    if detector.update(ir):
                        flash_until = time.ticks_add(now_ms, FLASH_MS)
                    if monitor:
                        monitor.push(detector.value)

                if SERIAL_MODE == 0:
                    write("%d,%d,%d,%d,%d\n" % (index, clock_us, ir, red, marker))
                elif SERIAL_MODE == 1:
                    if index % 10 == 0 or marker:
                        write("IR=%d RED=%d PULSE=%d BPM=%d FINGER=%s MARK=%d\n"
                              % (ir, red, int(detector.value), int(detector.bpm + 0.5),
                                 "yes" if finger else "no", marker))
                else:
                    write("%d %d\n" % (int(detector.value), int(detector.bpm + 0.5)))
                index += 1
                marker = 0
            last_sample = now_ms
        elif time.ticks_diff(now_ms, last_sample) > STALL_MS:
            write("# ERROR no samples from MAX30102; reinitialising\n")
            ready = False
            continue

        if monitor and time.ticks_diff(now_ms, last_draw) >= DRAW_MS:
            last_draw = now_ms
            flash = time.ticks_diff(flash_until, now_ms) > 0
            monitor.draw(detector.bpm, finger, detector.env, flash)

        if SERIAL_MODE == 0 and time.ticks_diff(now_ms, last_status) >= 1000:
            last_status = now_ms
            write("# HR bpm=%d finger=%d pulse_height=%d\n"
                  % (int(detector.bpm + 0.5), 1 if finger else 0, int(detector.env)))

        time.sleep_ms(POLL_MS)


try:
    run()
except KeyboardInterrupt:
    print("# stopped")
