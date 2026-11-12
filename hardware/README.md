# Hardware

**Status: designed; the Arduino sketch has been run by the project owner, but
no recording with ground truth exists yet.** The owner confirmed that the
project ran in the Arduino IDE on the ESP32-S3 + MAX30102 and streamed data.
The current sketch, with the OLED heart monitor, has been checked here only
indirectly: library calls against the public headers, the beat detector on a
PC, the stream format against the host parser. Nothing in this folder is a
photograph of a working device. Work through the bring-up checklist below and
record what you observe.

Step-by-step instructions in Russian: [docs/arduino_guide_ru.md](../docs/arduino_guide_ru.md).

## Bill of materials

| # | Part | Role | Notes |
|---|------|------|-------|
| 1 | ESP32-S3 development board (ESP32-S3-WROOM-1 module) | Controller, USB serial link | The owner's board is selected as "Fri3d Badge 2024" in the Arduino IDE; pin labels differ between boards |
| 2 | MAX30102 breakout module | PPG sensor (red 660 nm + IR 880 nm LEDs, photodiode, 18-bit ADC, I2C) | Exact breakout variant not yet recorded |
| 3 | Solderless breadboard | Mechanical support | |
| 4 | DuPont jumper wires | Power and I2C | Keep them short (≤ 20 cm) for 400 kHz I2C |
| 5 | USB data cable | Power and data to the computer | Charge-only cables do not work |
| 6 | 0.96" OLED, SSD1306, 128×64, I2C (optional) | Live heart monitor: heart rate and pulse waveform | 4-pin module (GND, VCC, SCL, SDA), usually address 0x3C; 7-pin SPI variants do not fit this wiring |

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

The optional OLED is wired **in parallel** with the sensor on the same two I2C
lines (the sensor answers at address 0x57, the display at 0x3C):

| OLED pin | ESP32-S3 pin |
|----------|--------------|
| VCC      | 3V3 |
| GND      | GND |
| SDA      | GPIO 8 (same as the sensor's SDA) |
| SCL      | GPIO 9 (same as the sensor's SCL) |

On the breadboard use the power rails for 3V3 and GND and one row each for SDA
and SCL, so both modules plug into the same rows. Pin order on OLED modules
differs between sellers (GND-VCC-SCL-SDA or VCC-GND-SCL-SDA): read the labels.

The ESP32-S3 can route I2C to almost any GPIO: if your board uses other pins,
change `PIN_I2C_SDA` / `PIN_I2C_SCL` at the top of the sketch.

Things to check on your particular parts (they vary between sellers):

- **Supply voltage.** The MAX30102 chip needs 1.8 V logic and 3.3 V LED
  supplies; breakouts carry regulators so that one VIN pin is enough. Use the
  ESP32's **3V3** pin. Do not use 5 V: the ESP32-S3's GPIOs are not 5 V tolerant
  and on some breakouts the I2C pull-ups are tied to VIN.
- **Pull-ups.** Most breakouts include I2C pull-up resistors. If the sensor is
  not found, measure SDA/SCL at idle: they should sit near 3.3 V. With the
  OLED on the same lines the pull-ups of both modules are in parallel, which is
  normally fine.
- **Red/IR order.** On some MAX30102 modules the two LED channels come out
  swapped relative to the library's naming. It does not matter for this
  project as long as you always analyse the same column; pick the one with the
  larger pulsatile component (`--channel ir` or `--channel red`).

## Firmware: `firmware/arduino_monitor/`

| File | Purpose |
|------|---------|
| `arduino_monitor.ino` | Reads the sensor, draws the OLED monitor, streams data over USB serial |
| `BeatDetector.h` | Heart-beat detector for the display; no Arduino dependencies, tested on a PC |

| Item | Value |
|------|-------|
| Toolchain | Arduino IDE 2.x, core "esp32 by Espressif Systems" |
| Libraries | SparkFun MAX3010x Pulse and Proximity Sensor Library; Adafruit SSD1306; Adafruit GFX Library |
| Serial | 115200 baud, 8N1 |
| Output rate | 100 samples/s (400 sps internal ÷ 4 on-chip averaging) |
| Resolution | 18 bit (411 µs LED pulse), ADC full scale 4096 nA |
| LED current | register 0x1F ≈ 6 mA; `LED_CURRENT` in the sketch |

The sketch calls were checked against the public headers of the three
libraries. The OLED is optional: if it is not found the sketch warns and keeps
streaming.

### Main functions

| Function | Purpose |
|----------|---------|
| `startSensor()` | Finds the sensor on I2C, resets and configures it; returns `false` if it does not answer |
| `printHeader()` | Prints the configuration and the CSV header so every log is self-describing |
| `pollMarkerButton()` | Debounced detection of a button press → breath marker |
| `drainFifo()` | Reads the FIFO overflow counter, pulls all waiting samples and passes each to `handleSample()` |
| `handleSample()` | Finger detection, beat detector, waveform buffer and serial output for one sample |
| `drawScreen()` | Renders one OLED frame: heart, BPM digits, status, scrolling waveform |
| `loop()` | Retries a missing sensor every second; re-initialises it if no samples arrive for 0.5 s |

### Heart monitor on the OLED

A heart that grows on every beat, the heart rate in large digits, a status
(`OK`, `WEAK`, `---`) and the pulse waveform scrolling across the screen,
auto-scaled so that weak and strong pulses both fill the plot. Without a
finger it says "Put finger on sensor".

The heart rate comes from a small streaming detector (`BeatDetector.h`): the
slow level is removed, the signal is flipped so that beats point up and
smoothed at about 5 Hz, and a beat is a local maximum above half the recent
peak height and at least 0.3 s after the previous one. The rate is the mean of
the last 3–5 intervals. It is meant for display only and is separate from the
research analysis, which uses the raw stream. Pulses smaller than 15 counts
are treated as "no pulse". It was tested on simulated signals (45–180
beats/min, amplitudes from 30 to 7500 counts); not yet on real fingers. The
screen layout itself is not covered by any automated test.

### Serial output

`SERIAL_MODE` at the top of the sketch:

| Mode | Output | Use |
|------|--------|-----|
| 0 | CSV, 100 lines/s, plus a `# HR bpm=… finger=… pulse_height=…` line per second | Recording (default) |
| 1 | `IR=52822 RED=58361 PULSE=-12 BPM=72 FINGER=yes MARK=0`, 10 lines/s | Reading values by eye |
| 2 | `Pulse:-12,BPM:72` | Arduino Serial Plotter |

Only mode 0 can be recorded by `scripts/record_serial.py`.

### Data stream (mode 0)

```
# arduino_monitor 1.0 | ESP32-S3 + MAX30102
# fs_nominal_hz=100 led_current_reg=0x1F oled=yes
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

All research signal processing is done in Python on the host, so every
intermediate step can be inspected and re-run on stored raw data. The
microcontroller only has to deliver unmodified samples with a trustworthy time
base.

## Bring-up checklist

1. Wire as above; connect USB. The board's power LED should light.
2. Install the libraries, open `arduino_monitor.ino`, select the board and
   port, upload.
3. Open the Serial Monitor at 115200 baud. Expected: the two `#` header lines,
   then about 100 data lines per second. `# ERROR MAX30102 not found` means
   wiring or supply; `# WARN OLED not found` means the display only.
4. Set `SERIAL_MODE 1`, upload, and read `IR` without and with a finger. Move
   `FINGER_ON` / `FINGER_OFF` between the two readings. Without a finger `IR`
   is low; with a fingertip resting lightly it rises to tens of thousands of
   counts and ripples with the heart rate. If it sits at 262143 the ADC is
   saturated: lower `LED_CURRENT`. If the ripple is only a few counts, raise it.
5. Set `SERIAL_MODE 0`, upload, press BOOT: one line should show `marker` = 1.
6. Close the Serial Monitor, then run
   `python scripts/record_serial.py --port <port> --duration 60 --analyze`
   (on Windows: `ppg scripts\record_serial.py --port COM5 --duration 60 --analyze`)
   and confirm that the reported sampling rate is close to 100 Hz and that the
   heart rate matches a manual pulse count.
7. Photograph the assembled device and add the pictures to `photos/`.

## Photos

`photos/` is intentionally empty until real photographs exist. See
`photos/README.md` for the shots to take.

## 3D models

Not applicable. The prototype is a bare sensor module on a breadboard and no
custom mechanical part was designed, so there are no CAD or STL files and
none are fabricated for show. If motion artifacts turn out to dominate the
error (see `docs/limitations.md`), a printed fingertip cradle that fixes the
contact pressure would be the first mechanical addition, and its CAD sources
would then belong in `hardware/3d_models/`.
