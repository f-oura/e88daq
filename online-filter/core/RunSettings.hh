// Per-run settings of the filter device, read from a small text file at every RUN so that the filter can be changed
// between runs without restarting the session. One "key value" pair per line; '#' starts a comment:
//   kind sc          sc (SC coincidence + prescale), prescale (prescale only) or off (nothing kept in the run)
//   prescale 100     N >= 1, keep one time frame out of N regardless of its content
// A missing key keeps the value given when the device started. This file depends on neither ROOT nor FairMQ.
#pragma once

#include <cstdint>
#include <string>

namespace e88filter {

struct RunSettings {
  std::string kind;           // "sc", "prescale" or "off"
  std::uint64_t prescale = 0;  // >= 1
};

// Reads `path` over `settings` (which holds the defaults). Returns false with `error` for an unreadable file, an unknown
// key, a bad kind or a prescale that is not an integer >= 1; `settings` is then unchanged.
bool ReadRunSettings(const std::string& path, RunSettings& settings, std::string& error);

}  // namespace e88filter
