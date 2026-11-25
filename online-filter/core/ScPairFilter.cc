#include "ScPairFilter.hh"

#include <algorithm>
#include <cmath>
#include <set>

#include "format/TdcWord.hh"

namespace e88filter {

namespace {

constexpr double kFrameNs = 65536 * 8.0;  // one heartbeat frame

long SignedFrameDiff(std::uint32_t frame, std::uint32_t reference) {
  long d = static_cast<long>(nda::format::FrameDistance(reference, frame));
  if (d >= (1L << 23)) d -= (1L << 24);
  return d;
}

// True when some a[i] and b[j] differ by at most `window`; both sorted.
bool AnyPair(const std::vector<double>& a, const std::vector<double>& b, double window) {
  std::size_t j = 0;
  for (double t : a) {
    while (j < b.size() && b[j] < t - window) ++j;
    if (j < b.size() && b[j] <= t + window) return true;
  }
  return false;
}

}  // namespace

bool ScPairFilter::Configure(const nda::e88::DetectorDef& def, const nda::e88::ChannelMap& map,
                             const ScPairConfig& config, std::string& error) {
  if (!(config.windowNs > 0)) {
    error = "the SC pair window must be positive";
    return false;
  }
  if (!(config.tdcLsbNs > 0)) {
    error = "the TDC LSB must be positive";
    return false;
  }
  fConfig = config;
  fWords = nda::format::WordStream{};
  if (config.b2ttDataType >= 0) fWords.AddRecordType(static_cast<std::uint8_t>(config.b2ttDataType));
  const std::set<int> wanted(config.modules.begin(), config.modules.end());
  std::set<int> found;
  // Planes used: SC, two sides, and (if modules are given) one of those modules.
  std::vector<int> firstCounter(def.planes.size(), -1);
  fCounters.clear();
  for (std::size_t pi = 0; pi < def.planes.size(); ++pi) {
    const auto& p = def.planes[pi];
    if (p.detector != "SC" || p.nSides != 2) continue;
    if (!wanted.empty() && !wanted.count(p.module)) continue;
    found.insert(p.module);
    firstCounter[pi] = static_cast<int>(fCounters.size());
    for (int s = 0; s < p.nSegments; ++s) fCounters.push_back(Counter{p.module, p.layer, s});
  }
  for (int m : wanted) {
    if (!found.count(m)) {
      error = "SC module " + std::to_string(m) + " with two sides is not in the detector definition";
      return false;
    }
  }
  if (fCounters.empty()) {
    error = "the detector definition has no SC plane with two sides";
    return false;
  }
  fChannels.clear();
  for (const auto& c : map.channels) {
    const int first = firstCounter[c.plane];
    if (first < 0 || c.side > 1) continue;
    fChannels[nda::e88::ChannelMap::Address(c.femId, c.channel)] = Slot{first + c.segment, c.side};
  }
  if (fChannels.empty()) {
    error = "the channel map has no channel of the SC planes used";
    return false;
  }
  for (auto& side : fTimes) side.assign(fCounters.size(), {});
  fTouched.clear();
  return true;
}

ScPairResult ScPairFilter::Judge(const nda::format::TimeFrameView& tf) {
  ScPairResult result;
  for (int c : fTouched) {
    fTimes[0][c].clear();
    fTimes[1][c].clear();
  }
  fTouched.clear();
  const std::uint32_t tfFrame = tf.header->timeFrameId & nda::format::kFrameNumberMask;
  for (const auto& stf : tf.subFrames) {
    const auto kind = nda::format::KindOfFemType(stf.header->femType);
    if (kind != nda::format::TdcKind::kHighResolution) continue;  // SC is read by HR-TDC
    const std::uint32_t fem = stf.header->femId;
    for (const auto& hbf : stf.frames) {
      bool haveFrame = false;
      std::uint32_t frame = 0;
      for (std::size_t i = hbf.nWords; i-- > 0;) {
        if (nda::format::HeadOf(hbf.words[i]) == nda::format::kHeartbeat1) {
          frame = nda::format::DecodeHeartbeat1(hbf.words[i]).frameNumber;
          haveFrame = true;
          break;
        }
      }
      if (!haveFrame) continue;
      const double frameStart = static_cast<double>(SignedFrameDiff(frame, tfFrame)) * kFrameNs;
      fWords.ForEach(hbf, [&](std::uint64_t w, nda::format::WordRole role, std::size_t) {
        if (role != nda::format::WordRole::kWord || nda::format::HeadOf(w) != nda::format::kLeading) return;
        const auto hit = nda::format::DecodeTdc(w, kind);
        const auto it = fChannels.find(nda::e88::ChannelMap::Address(fem, static_cast<int>(hit.channel)));
        if (it == fChannels.end()) return;
        const Slot& s = it->second;
        auto& other = fTimes[1 - s.side][s.counter];
        auto& mine = fTimes[s.side][s.counter];
        if (mine.empty() && other.empty()) fTouched.push_back(s.counter);
        mine.push_back(frameStart + hit.tdc * fConfig.tdcLsbNs);
        ++result.leading;
      });
    }
  }
  std::sort(fTouched.begin(), fTouched.end());
  for (int c : fTouched) {
    auto& a = fTimes[0][c];
    auto& b = fTimes[1][c];
    if (a.empty() || b.empty()) continue;
    std::sort(a.begin(), a.end());
    std::sort(b.begin(), b.end());
    if (!AnyPair(a, b, fConfig.windowNs)) continue;
    if (result.counters++ == 0) {
      result.module = fCounters[c].module;
      result.segment = fCounters[c].segment;
    }
  }
  result.pass = result.counters > 0;
  return result;
}

}  // namespace e88filter
