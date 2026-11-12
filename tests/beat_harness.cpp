// Test harness: feeds raw samples from stdin (one per line) to the firmware's
// BeatDetector.h and prints "<sample index> <bpm>" for every detected beat.
#include <cstdio>

#include "BeatDetector.h"

int main() {
  BeatDetector detector(100, true);
  double raw;
  int index = 0;
  while (std::scanf("%lf", &raw) == 1) {
    if (detector.update((float)raw)) std::printf("%d %.3f\n", index, detector.bpm);
    index++;
  }
  std::printf("end %.3f\n", detector.bpm);
  return 0;
}
