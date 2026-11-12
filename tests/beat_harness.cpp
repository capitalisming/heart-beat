// Test harness: feeds raw samples from stdin (one token per line) to the
// firmware's BeatDetector.h. A line "R" calls reset(), as the sketch does when
// the finger is lifted. For every detected beat it prints
// "<sample index> <bpm> <pulse value>"; at the end "end <bpm>".
#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "BeatDetector.h"

int main() {
  BeatDetector detector(100, true);
  char token[64];
  int index = 0;
  while (std::scanf("%63s", token) == 1) {
    if (std::strcmp(token, "R") == 0) {
      detector.reset();
      continue;
    }
    if (detector.update((float)std::atof(token))) {
      std::printf("%d %.3f %.3f\n", index, detector.bpm, detector.value);
    }
    index++;
  }
  std::printf("end %.3f\n", detector.bpm);
  return 0;
}
