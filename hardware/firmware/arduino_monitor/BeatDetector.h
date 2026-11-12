/*
 * BeatDetector.h - streaming heart-beat detector for the on-device display.
 *
 * C++ twin of hardware/firmware/micropython/beat.py (same constants, same
 * steps; tests/test_beat_detector.py runs both on the same signals).
 * No Arduino dependencies, so it also compiles on a PC.
 *
 * Call update() once per raw sample at about 100 Hz, then read bpm, beat, value.
 * This is a display aid only. The research analysis works on the raw stream.
 *
 *  1. Remove the slow level and flip the sign (MAX30102 counts fall when
 *     blood volume rises, so a heartbeat should point up).
 *  2. Smooth with a one-pole low-pass (about 5 Hz).
 *  3. A beat is a local maximum above max(MIN_PEAK, THR_FRAC * envelope), at
 *     least 0.3 s after the previous beat and, once a rhythm is known, at
 *     least 0.55 of the typical interval after it.
 *  4. bpm = 60 / mean of the last 3-5 plausible intervals.
 */
#ifndef BEAT_DETECTOR_H
#define BEAT_DETECTOR_H

#include <stdint.h>

class BeatDetector {
 public:
  static constexpr float ALPHA_DC = 0.02f;
  static constexpr float ALPHA_LP = 0.3f;
  static constexpr float DECAY = 0.9977f;
  static constexpr float THR_FRAC = 0.5f;
  static constexpr float MIN_PEAK = 15.0f;
  static constexpr float REFRACT_S = 0.30f;
  static constexpr float MAX_INTERVAL_S = 1.5f;
  static constexpr float MIN_INTERVAL_FRAC = 0.55f;
  static constexpr float WARMUP_S = 2.0f;
  static constexpr float LOST_S = 4.0f;
  static constexpr int MAX_INTERVALS = 5;

  float value;   // smoothed pulse signal, heartbeat points up (raw-count units)
  float env;     // recent peak height, a measure of signal strength
  float bpm;     // heart rate in beats/min, 0 while unknown
  bool beat;     // true on the sample at which a beat was just confirmed

  explicit BeatDetector(int sampleRateHz = 100, bool invertSignal = true)
      : value(0), env(0), bpm(0), beat(false), fs_(sampleRateHz), invert_(invertSignal) {
    reset();
  }

  /* Forget everything (call when the finger is lifted). */
  void reset() {
    n_ = 0;
    haveDc_ = false;
    dc_ = 0;
    lp_ = 0;
    p1_ = 0;
    p2_ = 0;
    env = 0;
    value = 0;
    bpm = 0;
    beat = false;
    haveBeat_ = false;
    lastBeat_ = 0;
    count_ = 0;
  }

  /* Process one raw sample. Returns true if a beat was just detected. */
  bool update(float raw) {
    beat = false;
    if (!haveDc_) {
      dc_ = raw;
      haveDc_ = true;
    }
    dc_ += (raw - dc_) * ALPHA_DC;
    float x = raw - dc_;
    if (invert_) x = -x;
    lp_ += (x - lp_) * ALPHA_LP;
    const float s = lp_;
    value = s;

    env *= DECAY;
    if (s > env) env = s;

    const float fs = (float)fs_;
    if (n_ > (uint32_t)(WARMUP_S * fs) && p1_ > p2_ && p1_ >= s) {
      float thr = THR_FRAC * env;
      if (thr < MIN_PEAK) thr = MIN_PEAK;
      if (p1_ > thr) {
        const uint32_t peakAt = n_ - 1;
        if (!haveBeat_) {
          lastBeat_ = peakAt;
          haveBeat_ = true;
        } else {
          const float gap = (float)(peakAt - lastBeat_);
          const float typical = typicalInterval();
          float floorGap = REFRACT_S * fs;
          if (typical > 0 && MIN_INTERVAL_FRAC * typical > floorGap) floorGap = MIN_INTERVAL_FRAC * typical;
          if (gap >= floorGap) {
            if (gap <= MAX_INTERVAL_S * fs) {
              push(gap);
            } else {
              count_ = 0;  // long pause: restart the rhythm
            }
            lastBeat_ = peakAt;
            beat = true;
            const float t = typicalInterval();
            bpm = t > 0 ? 60.0f * fs / t : 0.0f;
          }
        }
      }
    }

    if (haveBeat_ && (float)(n_ - lastBeat_) > LOST_S * fs) {
      haveBeat_ = false;
      count_ = 0;
      bpm = 0;
    }

    p2_ = p1_;
    p1_ = s;
    n_++;
    return beat;
  }

 private:
  int fs_;
  bool invert_;
  uint32_t n_;
  bool haveDc_;
  float dc_, lp_, p1_, p2_;
  bool haveBeat_;
  uint32_t lastBeat_;
  float intervals_[MAX_INTERVALS];
  int count_;

  void push(float gap) {
    if (count_ == MAX_INTERVALS) {
      for (int i = 1; i < MAX_INTERVALS; i++) intervals_[i - 1] = intervals_[i];
      count_--;
    }
    intervals_[count_++] = gap;
  }

  /* Mean of the stored intervals, dropping the largest and smallest when there are five. */
  float typicalInterval() const {
    if (count_ < 3) return 0;
    float v[MAX_INTERVALS];
    for (int i = 0; i < count_; i++) v[i] = intervals_[i];
    for (int i = 1; i < count_; i++) {  // insertion sort
      const float key = v[i];
      int j = i - 1;
      while (j >= 0 && v[j] > key) {
        v[j + 1] = v[j];
        j--;
      }
      v[j + 1] = key;
    }
    int from = 0, to = count_;
    if (count_ >= 5) {
      from = 1;
      to = count_ - 1;
    }
    float sum = 0;
    for (int i = from; i < to; i++) sum += v[i];
    return sum / (float)(to - from);
  }
};

#endif  // BEAT_DETECTOR_H
