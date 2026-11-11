/*
 * ppg_streamer - raw PPG acquisition for the PPG Respiratory Estimator
 *
 * Hardware : ESP32-S3-WROOM-1 development board + MAX30102 breakout (I2C)
 * Library  : "SparkFun MAX3010x Pulse and Proximity Sensor Library"
 *            (Arduino Library Manager; header MAX30105.h also drives the MAX30102)
 * Board    : Arduino core for ESP32 (esp32 by Espressif), board "ESP32S3 Dev Module"
 *
 * What it does
 *   Configures the MAX30102 for 100 samples/s (400 sps internal, 4x on-chip
 *   averaging, 18-bit, 411 us pulses), empties the sensor FIFO continuously and
 *   prints one CSV line per sample over USB serial:
 *
 *       sample_index,device_us,ir,red,marker
 *
 *   sample_index  running sample counter (gaps = samples lost in the sensor FIFO)
 *   device_us     micros() when the line was sent. It is NOT the exact sample
 *                 instant (samples arrive in FIFO batches); the host uses only
 *                 its long-term slope against sample_index to measure the true
 *                 sampling rate, because the sensor's own oscillator is not
 *                 trimmed to exactly 100 Hz.
 *   ir, red       raw 18-bit ADC counts of the two LEDs
 *   marker        1 on the first sample after the breath-marker button was
 *                 pressed, else 0 (ground truth input, not used by the estimator)
 *
 *   Lines starting with '#' are status or error messages for the host log.
 *
 * All signal processing happens on the host in Python: the microcontroller
 * only has to deliver unmodified samples with a reliable time base, which
 * keeps the algorithm inspectable and the firmware trivial to verify.
 *
 * STATUS: written against the SparkFun library API and the MAX30102 datasheet
 * register map; it has not yet been compiled or run on hardware by the
 * authors of this repository. See hardware/README.md for the bring-up checklist.
 */

#include <Arduino.h>
#include <Wire.h>
#include "MAX30105.h"

// ----------------------------------------------------------------- wiring
// Any free GPIOs work on the ESP32-S3; these are the Arduino-core defaults
// for "ESP32S3 Dev Module". Change them here if your board differs.
static const int PIN_I2C_SDA = 8;
static const int PIN_I2C_SCL = 9;
// BOOT button of most ESP32-S3 dev boards (GPIO0, active low). Safe to use as
// an input once the program runs; do not hold it while resetting the board.
static const int PIN_MARKER = 0;

// ----------------------------------------------------------------- serial
static const uint32_t SERIAL_BAUD = 115200;   // ~35 bytes x 100 lines/s = 30 % of capacity

// ----------------------------------------------------------------- sensor
static const uint8_t  MAX30102_ADDRESS = 0x57;   // fixed 7-bit I2C address
static const uint8_t  REG_OVF_COUNTER  = 0x05;   // FIFO overflow counter (0..31)
static const uint8_t  LED_CURRENT      = 0x1F;   // ~6 mA (0.2 mA per step); raise if signal is weak
static const uint8_t  SAMPLE_AVERAGE   = 4;      // on-chip averaging
static const uint8_t  LED_MODE         = 2;      // 2 = red + IR
static const int      SAMPLE_RATE_SPS  = 400;    // internal rate
static const int      PULSE_WIDTH_US   = 411;    // 18-bit resolution
static const int      ADC_RANGE_NA     = 4096;   // full scale 4096 nA
static const float    OUTPUT_RATE_HZ   = (float)SAMPLE_RATE_SPS / SAMPLE_AVERAGE;  // 100 Hz

static const uint32_t STALL_TIMEOUT_MS = 500;    // no samples for this long = sensor fault
static const uint32_t DEBOUNCE_MS      = 60;

MAX30105 sensor;

static uint32_t sampleIndex   = 0;
static uint32_t lastSampleMs  = 0;
static uint32_t lostSamples   = 0;
static bool     sensorReady   = false;
static bool     markerPending = false;
static bool     buttonWasDown = false;
static uint32_t lastButtonMs  = 0;

/* Start (or restart) the sensor. Returns false if it does not answer on I2C. */
static bool startSensor() {
  if (!sensor.begin(Wire, I2C_SPEED_FAST, MAX30102_ADDRESS)) {
    return false;
  }
  // setup() soft-resets the chip, then writes LED current, averaging, mode,
  // sample rate, pulse width and ADC range, and clears the FIFO.
  sensor.setup(LED_CURRENT, SAMPLE_AVERAGE, LED_MODE, SAMPLE_RATE_SPS, PULSE_WIDTH_US, ADC_RANGE_NA);
  lastSampleMs = millis();
  return true;
}

/* Print the stream description so every log is self-describing. */
static void printHeader() {
  Serial.println(F("# ppg_streamer 1.0 | ESP32-S3 + MAX30102"));
  Serial.print(F("# fs_nominal_hz="));
  Serial.print(OUTPUT_RATE_HZ, 1);
  Serial.print(F(" sample_rate_sps="));
  Serial.print(SAMPLE_RATE_SPS);
  Serial.print(F(" sample_average="));
  Serial.print(SAMPLE_AVERAGE);
  Serial.print(F(" pulse_width_us="));
  Serial.print(PULSE_WIDTH_US);
  Serial.print(F(" adc_range_na="));
  Serial.print(ADC_RANGE_NA);
  Serial.print(F(" led_current_reg=0x"));
  Serial.println(LED_CURRENT, HEX);
  Serial.println(F("sample_index,device_us,ir,red,marker"));
}

/* Debounced falling-edge detection on the marker button. */
static void pollMarkerButton() {
  const bool down = digitalRead(PIN_MARKER) == LOW;
  const uint32_t now = millis();
  if (down && !buttonWasDown && (now - lastButtonMs) > DEBOUNCE_MS) {
    markerPending = true;
    lastButtonMs = now;
  }
  buttonWasDown = down;
}

/* Send every sample waiting in the sensor FIFO. */
static void drainFifo() {
  // The chip counts samples it had to drop because the FIFO was full. Adding
  // them to the index keeps (index / fs) a true time axis and makes the loss
  // visible to the host as a gap instead of a silent time shift.
  const uint8_t overflow = sensor.readRegister8(MAX30102_ADDRESS, REG_OVF_COUNTER) & 0x1F;

  sensor.check();                         // pull new samples into the library buffer
  if (!sensor.available()) {
    return;
  }
  if (overflow > 0) {
    sampleIndex += overflow;
    lostSamples += overflow;
    Serial.print(F("# WARN fifo_overflow lost="));
    Serial.print(overflow);
    Serial.print(F(" total_lost="));
    Serial.println(lostSamples);
  }
  while (sensor.available()) {
    const uint32_t ir  = sensor.getFIFOIR();
    const uint32_t red = sensor.getFIFORed();
    sensor.nextSample();

    Serial.print(sampleIndex++);
    Serial.print(',');
    Serial.print((uint32_t)micros());
    Serial.print(',');
    Serial.print(ir);
    Serial.print(',');
    Serial.print(red);
    Serial.print(',');
    Serial.println(markerPending ? 1 : 0);
    markerPending = false;
    lastSampleMs = millis();
  }
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  pinMode(PIN_MARKER, INPUT_PULLUP);
  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL);
  delay(200);
}

void loop() {
  if (!sensorReady) {
    sensorReady = startSensor();
    if (!sensorReady) {
      Serial.println(F("# ERROR MAX30102 not found on I2C (check 3V3, GND, SDA, SCL); retrying"));
      delay(1000);
      return;
    }
    printHeader();
  }

  pollMarkerButton();
  drainFifo();

  if (millis() - lastSampleMs > STALL_TIMEOUT_MS) {
    Serial.println(F("# ERROR no samples from MAX30102; reinitialising"));
    sensorReady = false;     // sample_index keeps counting, so the host sees the gap
  }
}
