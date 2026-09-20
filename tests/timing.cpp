#include <stdint.h>
#include <cassert>

int main() {
    const auto duration = cycleDuration(1000, 1000, true);
    // Reported blink: on/off halves at 0/500, 1000/1500, 2000/2500.
    int flashes = 0;
    bool previous = false;
    for (uint32_t ms = 0; ms <= 3500; ms += 10) {
        bool on = !animationExpired(ms, duration, 3, 0) && ms % duration < duration / 2;
        if (on && !previous) ++flashes;
        previous = on;
    }
    assert(flashes == 3);
    assert(!animationExpired(2999, duration, 3, 0));
    assert(animationExpired(3000, duration, 3, 0));
    assert(animationExpired(1000, duration, 3, 1000));
    assert(!animationExpired(999, duration, 3, 1000));
    assert(!animationExpired(4000000, duration, 0, 0));
    assert(cycleDuration(1000, 250, false) == 250);  // legacy speed
    assert(cycleDuration(500, 250, true) == 500);    // duration wins
    assert(cycleDuration(0, 250, true) == 1);
    assert(cycleDuration(1000, 0, false) == 1);
    const uint32_t start = UINT32_MAX - 499;
    const uint32_t now = 500;
    assert(animationExpired(now - start, 1000, 1, 0)); // millis rollover
}
