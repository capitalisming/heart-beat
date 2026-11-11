"""Live pulse view for Thonny's Plotter (View -> Plotter), then Run.

Prints one number per sample: the IR signal with its slow level removed and
flipped so that each heartbeat points upwards. For looking only; recordings
for analysis are made with main.py, which sends the unmodified raw values.
"""

import time

from machine import I2C, Pin

from max30102 import MAX30102

sensor = MAX30102(I2C(0, sda=Pin(8), scl=Pin(9), freq=400000))
if not sensor.is_connected():
    print("MAX30102 not found - run check_sensor.py")
else:
    sensor.setup()
    level = None
    while True:
        n, _, data = sensor.read_fifo()
        for k in range(n):
            value = sensor.ir(data, k)
            level = value if level is None else level + (value - level) * 0.02
            print(int(level - value))
        time.sleep_ms(20)
