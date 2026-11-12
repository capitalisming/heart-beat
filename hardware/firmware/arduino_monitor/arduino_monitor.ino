/*
 * arduino_monitor - PPG streamer with a live heart monitor on a 0.96" OLED
 *
 * Hardware : ESP32-S3 board + MAX30102 (I2C) + optional SSD1306 128x64 OLED (I2C)
 * Libraries (Arduino Library Manager):
 *            "SparkFun MAX3010x Pulse and Proximity Sensor Library"
 *            "Adafruit SSD1306"   and   "Adafruit GFX Library"
 *            (BeatDetector.h lies next to this file, nothing to install)
 * Board    : Arduino core "esp32 by Espressif Systems", e.g. "ESP32S3 Dev Module"
 *
 * What it does
 *   1. Reads the sensor at 100 samples/s (400 sps, 4x on-chip averaging, 18 bit).
 *   2. Shows on the OLED, like a hospital monitor: heart-rate number, a heart
 *      that flashes on every beat, and the scrolling pulse waveform.
 *   3. Sends the measurements over USB serial. SERIAL_MODE selects the format:
 *        0  CSV for recording (default): sample_index,device_us,ir,red,marker
 *           plus one "# HR ..." comment line per second
 *        1  readable text, 10 lines/s:   IR=52822 RED=58361 PULSE=-12 BPM=72 ...
 *        2  numbers for Tools > Serial Plotter:  Pulse:-12,BPM:72
 *
 *   Serial Monitor must be set to 115200 baud. Close it before running
 *   scripts/record_serial.py: only one program can use the port.
 *
 *   The OLED is optional: if it is not found at OLED_ADDRESS the sketch keeps
 *   running and streaming without it.
 *
 *   In mode 0, sample_index counts every sample including those lost in the
 *   sensor FIFO, device_us is the board clock when the line was sent, and
 *   marker is 1 on the first sample after the BOOT button was pressed (press
 *   it at every inhalation to record the breathing reference).
 *
 * The heart rate on the screen comes from a simple detector (BeatDetector.h)
 * meant for display only; the research analysis uses the raw stream.
 * This is not a medical device.
 *
 * STATUS: written against the MAX30102 datasheet and the public APIs of the
 * SparkFun and Adafruit libraries; the detector is tested on a PC. The sketch
 * itself has not been compiled or run on a board by the repository authors.
 */

#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include "MAX30105.h"
#include "BeatDetector.h"

// ------------------------------------------------------------ settings
#define SERIAL_MODE 0                  // 0 = CSV, 1 = readable text, 2 = Serial Plotter

static const int PIN_I2C_SDA = 8;      // shared by the sensor and the OLED
static const int PIN_I2C_SCL = 9;
static const int PIN_MARKER = 0;       // BOOT button (GPIO0, active low)

static const uint8_t OLED_ADDRESS = 0x3C;   // some modules use 0x3D
static const int OLED_W = 128;
static const int OLED_H = 64;
static const uint32_t DRAW_INTERVAL_MS = 40;  // 25 frames/s

static const uint32_t SERIAL_BAUD = 115200;

static const uint8_t MAX30102_ADDRESS = 0x57;
static const uint8_t REG_OVF_COUNTER = 0x05;
static const uint8_t LED_CURRENT = 0x1F;      // about 6 mA; raise for a weak pulse, lower if saturated
static const uint8_t SAMPLE_AVERAGE = 4;
static const uint8_t LED_MODE = 2;            // red + IR
static const int SAMPLE_RATE_SPS = 400;
static const int PULSE_WIDTH_US = 411;
static const int ADC_RANGE_NA = 4096;
static const int OUTPUT_RATE_HZ = SAMPLE_RATE_SPS / SAMPLE_AVERAGE;  // 100

// Finger detection on the IR level. Read the IR value with and without a
// finger in SERIAL_MODE 1 and move these two numbers between them.
static const uint32_t FINGER_ON = 20000;
static const uint32_t FINGER_OFF = 12000;
static const float WEAK_PULSE = 40.0f;        // pulse height (counts) below which "WEAK" is shown

static const uint32_t STALL_TIMEOUT_MS = 500;
static const uint32_t DEBOUNCE_MS = 60;
// ------------------------------------------------------------------------

MAX30105 sensor;
Adafruit_SSD1306 oled(OLED_W, OLED_H, &Wire, -1, 400000UL, 400000UL);
BeatDetector detector(OUTPUT_RATE_HZ, true);

static bool oledReady = false;
static bool sensorReady = false;
static bool fingerPresent = false;
static bool markerPending = false;
static bool buttonWasDown = false;
static uint32_t sampleIndex = 0;
static uint32_t lostSamples = 0;
static uint32_t lastSampleMs = 0;
static uint32_t lastButtonMs = 0;
static uint32_t lastDrawMs = 0;
static uint32_t lastStatusMs = 0;
static uint32_t beatFlashUntil = 0;

// Scrolling waveform: one column per two samples (about 2.6 s across the screen).
static const int GAP = 6;                     // blank columns ahead of the write position
static float wave[OLED_W];
static bool waveValid[OLED_W];
static int waveX = 0;
static float waveAcc = 0;
static int waveAccN = 0;

static void clearWave() {
  for (int i = 0; i < OLED_W; i++) waveValid[i] = false;
  waveX = 0;
  waveAcc = 0;
  waveAccN = 0;
}

static void pushWave(float v) {
  waveAcc += v;
  if (++waveAccN == 2) {
    wave[waveX] = waveAcc / 2.0f;
    waveValid[waveX] = true;
    waveX = (waveX + 1) % OLED_W;
    waveAcc = 0;
    waveAccN = 0;
  }
}

/* Two-line message in the middle of the screen. */
static void showMessage(const char *line1, const char *line2) {
  if (!oledReady) return;
  oled.clearDisplay();
  oled.setTextColor(SSD1306_WHITE);
  oled.setTextSize(1);
  oled.setCursor(4, 22);
  oled.print(line1);
  oled.setCursor(4, 36);
  oled.print(line2);
  oled.display();
}

static void drawHeart(int cx, int cy, int r) {
  oled.fillCircle(cx - r, cy, r, SSD1306_WHITE);
  oled.fillCircle(cx + r, cy, r, SSD1306_WHITE);
  oled.fillTriangle(cx - 2 * r, cy + 1, cx + 2 * r, cy + 1, cx, cy + 2 * r + 2, SSD1306_WHITE);
}

/* Top band: heart + BPM. Bottom area: scrolling pulse waveform. */
static void drawScreen() {
  oled.clearDisplay();
  oled.setTextColor(SSD1306_WHITE);

  const bool flash = millis() < beatFlashUntil;
  drawHeart(9, 8, (fingerPresent && flash) ? 4 : 3);

  oled.setTextSize(3);
  oled.setCursor(22, 1);
  if (fingerPresent && detector.bpm > 0) {
    oled.print((int)(detector.bpm + 0.5f));
  } else {
    oled.print("--");
  }
  oled.setTextSize(1);
  oled.setCursor(86, 3);
  oled.print("BPM");
  oled.setCursor(86, 15);
  if (!fingerPresent) {
    oled.print("---");
  } else if (detector.env < WEAK_PULSE) {
    oled.print("WEAK");
  } else {
    oled.print("OK");
  }
  oled.drawFastHLine(0, 26, OLED_W, SSD1306_WHITE);

  if (!fingerPresent) {
    oled.setCursor(4, 42);
    oled.print("Put finger on sensor");
  } else {
    float lo = 1e9f, hi = -1e9f;
    for (int i = 0; i < OLED_W; i++) {
      if (!waveValid[i]) continue;
      if (wave[i] < lo) lo = wave[i];
      if (wave[i] > hi) hi = wave[i];
    }
    if (hi >= lo) {
      float range = hi - lo;
      if (range < 40.0f) range = 40.0f;          // do not blow noise up to full height
      const float mid = 0.5f * (hi + lo);
      const int yTop = 29, yBottom = OLED_H - 1;
      const float yCenter = 0.5f * (yTop + yBottom);
      const float half = 0.5f * (yBottom - yTop) - 2.0f;
      int prevY = 0;
      for (int x = 0; x < OLED_W; x++) {
        int y = (int)(yCenter - (wave[x] - mid) / range * 2.0f * half);
        if (y < yTop) y = yTop;
        if (y > yBottom) y = yBottom;
        const int ahead = (x - waveX + OLED_W) % OLED_W;
        if (x > 0 && waveValid[x] && waveValid[x - 1] && ahead >= GAP) {
          oled.drawLine(x - 1, prevY, x, y, SSD1306_WHITE);
        }
        prevY = y;
      }
    }
  }
  oled.display();
}

static void pollMarkerButton() {
  const bool down = digitalRead(PIN_MARKER) == LOW;
  const uint32_t now = millis();
  if (down && !buttonWasDown && (now - lastButtonMs) > DEBOUNCE_MS) {
    markerPending = true;
    lastButtonMs = now;
  }
  buttonWasDown = down;
}

static bool startSensor() {
  if (!sensor.begin(Wire, I2C_SPEED_FAST, MAX30102_ADDRESS)) {
    return false;
  }
  sensor.setup(LED_CURRENT, SAMPLE_AVERAGE, LED_MODE, SAMPLE_RATE_SPS, PULSE_WIDTH_US, ADC_RANGE_NA);
  lastSampleMs = millis();
  return true;
}

static void printHeader() {
  Serial.println(F("# arduino_monitor 1.0 | ESP32-S3 + MAX30102"));
  Serial.print(F("# fs_nominal_hz="));
  Serial.print(OUTPUT_RATE_HZ);
  Serial.print(F(" led_current_reg=0x"));
  Serial.print(LED_CURRENT, HEX);
  Serial.print(F(" oled="));
  Serial.println(oledReady ? F("yes") : F("no"));
#if SERIAL_MODE == 0
  Serial.println(F("sample_index,device_us,ir,red,marker"));
#endif
}

/* One sample: update finger state, detector, waveform and the serial output. */
static void handleSample(uint32_t ir, uint32_t red) {
  const bool wasPresent = fingerPresent;
  if (ir > FINGER_ON) fingerPresent = true;
  if (ir < FINGER_OFF) fingerPresent = false;
  if (wasPresent && !fingerPresent) {
    detector.reset();
    clearWave();
  }
  if (fingerPresent) {
    if (detector.update((float)ir)) beatFlashUntil = millis() + 150;
    pushWave(detector.value);
  }

  const int marker = markerPending ? 1 : 0;
  markerPending = false;
#if SERIAL_MODE == 0
  Serial.print(sampleIndex);
  Serial.print(',');
  Serial.print((uint32_t)micros());
  Serial.print(',');
  Serial.print(ir);
  Serial.print(',');
  Serial.print(red);
  Serial.print(',');
  Serial.println(marker);
#elif SERIAL_MODE == 1
  if (sampleIndex % 10 == 0 || marker) {
    Serial.printf("IR=%lu RED=%lu PULSE=%d BPM=%d FINGER=%s MARK=%d\n", (unsigned long)ir, (unsigned long)red,
                  (int)detector.value, (int)(detector.bpm + 0.5f), fingerPresent ? "yes" : "no", marker);
  }
#else
  Serial.printf("Pulse:%d,BPM:%d\n", (int)detector.value, (int)(detector.bpm + 0.5f));
#endif
  sampleIndex++;
  lastSampleMs = millis();
}

static void drainFifo() {
  // Samples the chip had to drop are added to the index, so a loss shows up as
  // a gap instead of a silent time shift.
  const uint8_t overflow = sensor.readRegister8(MAX30102_ADDRESS, REG_OVF_COUNTER) & 0x1F;
  sensor.check();
  if (!sensor.available()) return;
  if (overflow > 0) {
    sampleIndex += overflow;
    lostSamples += overflow;
    Serial.print(F("# WARN fifo_overflow lost="));
    Serial.print(overflow);
    Serial.print(F(" total_lost="));
    Serial.println(lostSamples);
  }
  while (sensor.available()) {
    const uint32_t ir = sensor.getFIFOIR();
    const uint32_t red = sensor.getFIFORed();
    sensor.nextSample();
    handleSample(ir, red);
  }
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  pinMode(PIN_MARKER, INPUT_PULLUP);
  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL);
  delay(200);
  // periphBegin = false: keep the Wire pins chosen above.
  oledReady = oled.begin(SSD1306_SWITCHCAPVCC, OLED_ADDRESS, true, false);
  if (!oledReady) {
    Serial.println(F("# WARN OLED not found (check SDA, SCL, VCC, GND, address 0x3C/0x3D); continuing without display"));
  }
  clearWave();
  showMessage("Heart monitor", "Starting sensor...");
}

void loop() {
  if (!sensorReady) {
    sensorReady = startSensor();
    if (!sensorReady) {
      Serial.println(F("# ERROR MAX30102 not found on I2C (check 3V3, GND, SDA, SCL); retrying"));
      showMessage("MAX30102 not found", "check the wiring");
      delay(1000);
      return;
    }
    printHeader();
  }

  pollMarkerButton();
  drainFifo();

  const uint32_t now = millis();
  if (oledReady && now - lastDrawMs >= DRAW_INTERVAL_MS) {
    lastDrawMs = now;
    drawScreen();
  }
#if SERIAL_MODE == 0
  if (now - lastStatusMs >= 1000) {
    lastStatusMs = now;
    Serial.print(F("# HR bpm="));
    Serial.print((int)(detector.bpm + 0.5f));
    Serial.print(F(" finger="));
    Serial.print(fingerPresent ? 1 : 0);
    Serial.print(F(" pulse_height="));
    Serial.println((int)detector.env);
  }
#endif

  if (millis() - lastSampleMs > STALL_TIMEOUT_MS) {
    Serial.println(F("# ERROR no samples from MAX30102; reinitialising"));
    sensorReady = false;
  }
}
