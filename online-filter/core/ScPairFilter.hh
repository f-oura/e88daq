// SC two-ended coincidence filter: a time frame passes when, in one SC counter (detector SC, a module and
// layer with two sides, one segment), a leading edge of side 0 (up) and one of side 1 (down) are within
// `windowNs` of each other. SC1 and SC2 are separate modules of the detector definition; each segment of
// each module is its own counter, so side 0 of SC1 and side 1 of SC2 never make a pair.
//
// Hits are taken from all front-end modules of the time frame. Times are relative to the heartbeat frame
// of the time frame (frame distance x 524288 ns + TDC x LSB), as in the online analyzer. A pair split between
// two time frames is not seen (window / frame length, about 1e-4 of the pairs for 50 ns).
// This file depends on neither ROOT nor FairMQ.
#pragma once

#include <cstdint>
#include <string>
#include <unordered_map>
#include <vector>

#include "e88/Params.hh"
#include "format/Frames.hh"
#include "format/WordStream.hh"

namespace e88filter {

struct ScPairConfig {
  double windowNs = 0;       // max |t(side 0) - t(side 1)|; required (> 0)
  double tdcLsbNs = 0;       // HR-TDC LSB [ns]; required (> 0)
  int b2ttDataType = -1;     // 6-bit type of B2TT records (their payload words are not read as TDC words); -1: none
  std::vector<int> modules;  // SC modules to use; empty: every SC plane with two sides
};

struct ScPairResult {
  bool pass = false;
  int counters = 0;    // counters with at least one pair
  int module = -1;     // first counter with a pair (lowest module, then segment)
  int segment = -1;
  std::uint64_t leading = 0;  // SC leading edges seen in the time frame
};

class ScPairFilter {
 public:
  // Builds the channel table. Returns false with `error` when the configuration or the parameters do not allow
  // the filter (no SC plane with two sides, a requested module missing, window or LSB not positive).
  bool Configure(const nda::e88::DetectorDef& def, const nda::e88::ChannelMap& map, const ScPairConfig& config,
                 std::string& error);

  ScPairResult Judge(const nda::format::TimeFrameView& tf);

  // Counters used: (module, segment) of each, for reports.
  struct Counter {
    int module = 0;
    int layer = 0;
    int segment = 0;
  };
  const std::vector<Counter>& Counters() const { return fCounters; }
  std::size_t ChannelsUsed() const { return fChannels.size(); }

 private:
  struct Slot {
    int counter = 0;
    int side = 0;
  };
  ScPairConfig fConfig;
  nda::format::WordStream fWords;
  std::unordered_map<std::uint64_t, Slot> fChannels;  // (femId << 8 | channel) -> counter and side
  std::vector<Counter> fCounters;
  std::vector<std::vector<double>> fTimes[2];          // per side, per counter: leading-edge times [ns]
  std::vector<int> fTouched;                           // counters with hits in the current time frame
};

}  // namespace e88filter
