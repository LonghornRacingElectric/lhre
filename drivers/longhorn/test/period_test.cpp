#include "longhorn/period.hpp"
namespace {
struct Slow { static constexpr float kFrequencyHz = 0.5f; };
struct Normal { static constexpr float kFrequencyHz = 100.0f; };
struct Fast { static constexpr float kFrequencyHz = 2000.0f; };
static_assert(longhorn::PeriodMs<Slow>() == 2000);
static_assert(longhorn::PeriodMs<Normal>() == 10);
static_assert(longhorn::PeriodMs<Fast>() == 1);
}
