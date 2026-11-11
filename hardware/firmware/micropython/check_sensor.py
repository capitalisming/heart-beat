"""Step-by-step hardware check. Run it from Thonny with the green Run button.

1. Scans the I2C bus (a MAX30102 answers at address 0x57 = 87).
2. Reads 5 seconds of data and reports the real sample rate and signal level.
"""

import time

from machine import I2C, Pin

from max30102 import MAX30102

PIN_SDA = 8
PIN_SCL = 9

i2c = I2C(0, sda=Pin(PIN_SDA), scl=Pin(PIN_SCL), freq=400000)
found = i2c.scan()
print("I2C devices found:", [hex(a) for a in found])
if 0x57 not in found:
    print("FAIL: no device at 0x57. Check the four wires and that VIN is on 3V3.")
else:
    sensor = MAX30102(i2c)
    print("Part ID correct (MAX30102):", sensor.is_connected())
    sensor.setup()
    print("Put a fingertip on the sensor. Measuring for 5 seconds...")
    count = lost_total = 0
    ir_min, ir_max = 1 << 18, 0
    start = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), start) < 5000:
        n, lost, data = sensor.read_fifo()
        lost_total += lost
        for k in range(n):
            value = sensor.ir(data, k)
            ir_min = min(ir_min, value)
            ir_max = max(ir_max, value)
        count += n
        time.sleep_ms(20)
    print("Samples: %d  -> %.1f per second (expected about 100)" % (count, count / 5))
    print("Samples lost in FIFO:", lost_total)
    print("IR level: min %d, max %d" % (ir_min, ir_max))
    if ir_max >= 262143:
        print("Signal is saturated: lower LED_CURRENT in main.py (e.g. 0x10).")
    elif ir_max < 10000:
        print("Very low level: no finger on the sensor, or raise LED_CURRENT (e.g. 0x3F).")
    else:
        print("OK: level looks usable.")
