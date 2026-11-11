# Hardware

**Status: designed, not yet built or tested by the repository authors.**
The firmware was checked line by line against the public API of the SparkFun
library and the MAX30102 register map, but it has not been compiled or run on
a board. Nothing in this folder is a photograph of a working device. Work
through the bring-up checklist below and record what you observe.

## Bill of materials

| # | Part | Role | Notes |
|---|------|------|-------|
| 1 | ESP32-S3-WROOM-1 development board | Controller, USB serial link | Exact board model not yet recorded (e.g. DevKitC-1 or a clone); pin labels differ between boards |
| 2 | MAX30102 breakout module | PPG sensor (red 660 nm + IR 880 nm LEDs, photodiode, 18-bit ADC, I2C) | Exact breakout variant not yet recorded |
| 3 | Solderless breadboard | Mechanical support | |
| 4 | DuPont jumper wires ×4 | Power and I2C | Keep them short (≤ 20 cm) for 400 kHz I2C |
| 5 | USB data cable | Power and data to the computer | Charge-only cables do not work |

No extra respiratory sensor is used: that is the point of the project. The
independent breathing reference comes from a person pressing the board's BOOT
button once per breath (see `docs/experiment_protocol.md`).

## Wiring

![Wiring diagram](wiring_diagram/wiring.svg)

| MAX30102 pin | ESP32-S3 pin | Wire |
|--------------|--------------|------|
| VIN          | 3V3          | red |
| GND          | GND          | black |
| SDA          | GPIO 8       | blue |
| SCL          | GPIO 9       | yellow |
| INT          | not connected | — |

GPIO 8/9 are the default I2C pins of the Arduino core for "ESP32S3 Dev
Module". The ESP32-S3 can route I2C to almost any GPIO: if you use other
pins, change `PIN_I2C_SDA` / `PIN_I2C_SCL` at the top of the sketch.

Things to check on your particular parts (they vary between sellers):

- **Supply voltage.** The MAX30102 chip itself needs 1.8 V logic and 3.3 V LED
  supplies; breakouts carry regulators so that one VIN pin is enough. Use the
  ESP32's **3V3** pin. Do not use 5 V: the ESP32-S3's GPIOs are not 5 V tolerant
  and on some breakouts the I2C pull-ups are tied to VIN.
- **Pull-ups.** Most breakouts include I2C pull-up resistors. If the sensor is
  not found, measure SDA/SCL at idle: they should sit near 3.3 V. Some
  low-cost boards pull up to 1.8 V only, which is marginal for a 3.3 V host.
- **Red/IR order.** On some MAX30102 modules the two LED channels come out
  swapped relative to the library's naming. It does not matter for this
  project as long as you always analyse the same column; pick the one with the
  larger pulsatile component (`--channel ir` or `--channel red`).

## Firmware

`firmware/ppg_streamer/ppg_streamer.ino`

| Item | Value |
|------|-------|
| Toolchain | Arduino IDE 2.x or `arduino-cli`, core "esp32 by Espressif Systems" |
| Board setting | "ESP32S3 Dev Module" |
| Library | "SparkFun MAX3010x Pulse and Proximity Sensor Library" (Library Manager) |
| Serial | 115200 baud, 8N1 |
| Output rate | 100 samples/s (400 sps internal ÷ 4 on-chip averaging) |
| Resolution | 18 bit (411 µs LED pulse), ADC full scale 4096 nA |
| LED current | register 0x1F ≈ 6 mA; adjustable in the sketch |

USB note: boards with two USB-C sockets expose a USB-UART bridge ("UART"/"COM")
and the chip's native USB. Use the UART socket, or, when using the native USB
socket, enable *Tools → USB CDC On Boot* so that `Serial` goes to that port.

### Main functions

| Function | Purpose |
|----------|---------|
| `startSensor()` | Finds the sensor on I2C, resets and configures it; returns `false` if it does not answer |
| `printHeader()` | Prints the configuration and the CSV header so every log is self-describing |
| `pollMarkerButton()` | Debounced detection of a button press → breath marker |
| `drainFifo()` | Reads the FIFO overflow counter, pulls all waiting samples and prints one line each |
| `loop()` | Retries a missing sensor every second; re-initialises it if no samples arrive for 0.5 s |

### Data stream

```
# ppg_streamer 1.0 | ESP32-S3 + MAX30102
# fs_nominal_hz=100.0 sample_rate_sps=400 sample_average=4 pulse_width_us=411 adc_range_na=4096 led_current_reg=0x1F
sample_index,device_us,ir,red,marker
0,2011934,118211,95310,0
1,2012470,118205,95301,0
```

- `sample_index` is the time base: `t = sample_index / fs`. Samples lost in the
  sensor FIFO are added to the index, so a loss appears as a gap, never as a
  hidden time shift.
- `device_us` is the microcontroller's clock at transmission. The host uses
  only its overall slope against `sample_index` to **measure the actual
  sampling rate**: the sensor runs from its own untrimmed oscillator, and a
  rate error of x % would become an x % error in every breathing rate.
- `marker` is 1 on the first sample after a button press.
- Lines beginning with `#` are status, warnings (`# WARN fifo_overflow …`) and
  errors (`# ERROR MAX30102 not found …`). The recorder stores them in the
  recording's `.meta.json`.

All signal processing is done in Python on the host, so every intermediate
step can be inspected and re-run on stored raw data. The microcontroller only
has to deliver unmodified samples with a trustworthy time base.

## Bring-up checklist

1. Wire as above; connect USB. The board's power LED should light.
2. Flash the sketch. Open a serial monitor at 115200 baud.
3. Expected: the two `#` header lines, then about 100 data lines per second.
   If you see `# ERROR MAX30102 not found`, recheck wiring and supply.
4. Without a finger, `ir` is small; with a fingertip resting lightly on the
   sensor it rises to tens of thousands of counts or more and shows a ripple
   at the heart rate. If it sits at 262143 the ADC is saturated: lower
   `LED_CURRENT`. If the ripple is only a few counts, raise it.
5. Press BOOT: one line should show `marker` = 1.
6. Run `python scripts/record_serial.py --port <port> --duration 60 --analyze`
   and confirm that the reported sampling rate is close to 100 Hz and that the
   heart rate matches a manual pulse count.
7. Photograph the assembled device and add the pictures to `photos/`.

## Photos

`photos/` is intentionally empty until the device exists. See
`photos/README.md` for the shots to take.

## 3D models

Not applicable. The prototype is a bare sensor module on a breadboard and no
custom mechanical part was designed, so there are no CAD or STL files and
none are fabricated for show. If motion artifacts turn out to dominate the
error (see `docs/limitations.md`), a printed fingertip cradle that fixes the
contact pressure would be the first mechanical addition, and its CAD sources
would then belong in `hardware/3d_models/`.
