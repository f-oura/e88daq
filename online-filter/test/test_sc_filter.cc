// Unit test of the SC coincidence filter and the prescale on time frames built in memory.
// Detector definition: SC module 0 (SC1) and module 1 (SC2), 4 segments x 2 sides, and an AC plane.
#include <cstdio>
#include <fstream>
#include <string>

#include "Builder.hh"
#include "Prescale.hh"
#include "ScPairFilter.hh"

using namespace ndatest;

namespace {

constexpr std::uint32_t kFemA = 0xc0000210;  // 192.0.2.16
constexpr std::uint32_t kFemB = 0xc0000211;  // 192.0.2.17
constexpr double kLsb = 1.0 / 1024;          // ns
constexpr std::uint32_t kTicksPerNs = 1024;

// SC1 (module 0): segment s, up on A ch 2s, down on A ch 2s+1. SC2 (module 1): segment s, up on B ch 2s,
// down on B ch 2s+1. AC on A ch 20.
void WriteParams(nda::e88::DetectorDef& def, nda::e88::ChannelMap& map) {
  std::ofstream("t_det.txt") << "#! format detector_def v1\nSC 0 0 4 2 LT\nSC 1 0 4 2 LT\nAC 0 0 1 1 LT\n";
  std::ofstream m("t_map.txt");
  m << "#! format channel_map v1\n#! valid_runs 0 999999\n";
  for (int mod = 0; mod < 2; ++mod)
    for (int s = 0; s < 4; ++s)
      for (int side = 0; side < 2; ++side)
        m << (mod == 0 ? "192.0.2.16 " : "192.0.2.17 ") << 2 * s + side << " SC " << mod << " 0 " << s << " " << side
          << " SC" << mod + 1 << "-" << s << (side ? "D" : "U") << "\n";
  m << "192.0.2.16 20 AC 0 0 0 0 AC0\n";
  m.close();
  std::string error;
  CHECK(nda::e88::ReadDetectorDef("t_det.txt", def, error));
  CHECK(nda::e88::ReadChannelMap("t_map.txt", def, map, error));
}

// One time frame (frame 100) with the given leading edges (fem, channel, time in ns).
struct Edge {
  std::uint32_t fem;
  std::uint32_t ch;
  double tNs;
};
std::vector<std::uint64_t> Frame(const std::vector<Edge>& edges, std::uint32_t frameId = 100,
                                 std::vector<std::uint64_t> extraA = {}) {
  std::vector<std::uint64_t> a = extraA, b;
  for (const auto& e : edges)
    (e.fem == kFemA ? a : b).push_back(Leading(e.ch, 100, static_cast<std::uint32_t>(e.tNs * kTicksPerNs)));
  for (auto* v : {&a, &b}) {
    v->push_back(Delimiter1(frameId));
    v->push_back(Delimiter2(0, 0));
  }
  return BuildTimeFrame(frameId, {Stf{kFemA, ::SubTimeFrame::TDC64H, 0, -1, {a}}, Stf{kFemB, ::SubTimeFrame::TDC64H, 0, -1, {b}}});
}

e88filter::ScPairResult Judge(e88filter::ScPairFilter& f, const std::vector<std::uint64_t>& tf) {
  nda::format::TimeFrameView view;
  std::string error;
  CHECK(nda::format::ParseTimeFrame(reinterpret_cast<const std::uint8_t*>(tf.data()), Bytes(tf), view, error));
  return f.Judge(view);
}

}  // namespace

int main() {
  nda::e88::DetectorDef def;
  nda::e88::ChannelMap map;
  WriteParams(def, map);
  e88filter::ScPairFilter f;
  std::string error;
  e88filter::ScPairConfig c;
  c.windowNs = 50;
  c.tdcLsbNs = kLsb;
  c.b2ttDataType = 0x2b;
  CHECK(f.Configure(def, map, c, error));
  CHECK_EQ(f.Counters().size(), std::size_t{8});
  CHECK_EQ(f.ChannelsUsed(), std::size_t{16});

  // Up and down of SC1 segment 1 within the window: pass.
  auto r = Judge(f, Frame({{kFemA, 2, 1000}, {kFemA, 3, 1030}}));
  CHECK(r.pass);
  CHECK_EQ(r.counters, 1);
  CHECK_EQ(r.module, 0);
  CHECK_EQ(r.segment, 1);
  // Same, outside the window (60 ns): no pass.
  CHECK(!Judge(f, Frame({{kFemA, 2, 1000}, {kFemA, 3, 1060}})).pass);
  // Exactly at the window: pass.
  CHECK(Judge(f, Frame({{kFemA, 2, 1000}, {kFemA, 3, 1050}})).pass);
  // Only one side, many hits: no pass.
  CHECK(!Judge(f, Frame({{kFemA, 2, 1000}, {kFemA, 2, 1010}, {kFemA, 2, 1020}})).pass);
  // Up of SC1 segment 2 and down of SC2 segment 2 at the same time: different counters, no pass.
  CHECK(!Judge(f, Frame({{kFemA, 4, 1000}, {kFemB, 5, 1000}})).pass);
  // Up and down of different segments of SC1: no pass.
  CHECK(!Judge(f, Frame({{kFemA, 2, 1000}, {kFemA, 5, 1000}})).pass);
  // SC2 segment 3: pass, reported as module 1.
  r = Judge(f, Frame({{kFemB, 6, 5000}, {kFemB, 7, 4990}}));
  CHECK(r.pass);
  CHECK_EQ(r.module, 1);
  CHECK_EQ(r.segment, 3);
  // Two counters at once: SC1 segment 0 and SC2 segment 0.
  CHECK_EQ(Judge(f, Frame({{kFemA, 0, 100}, {kFemA, 1, 101}, {kFemB, 0, 200}, {kFemB, 1, 201}})).counters, 2);
  // The state of the previous time frame does not leak into the next one.
  CHECK(!Judge(f, Frame({{kFemA, 1, 101}})).pass);
  // AC hits alone: no pass.
  CHECK(!Judge(f, Frame({{kFemA, 20, 1000}})).pass);
  // A B2TT record (type 0x2b, 3 words) whose payload looks like leading edges of ch 2 and 3: not read as hits.
  {
    const std::uint64_t header = (std::uint64_t{0x2b} << 58) | (std::uint64_t{3} << 44);
    const std::vector<std::uint64_t> record = {header, Leading(2, 1, 1000 * kTicksPerNs), Leading(3, 1, 1000 * kTicksPerNs)};
    CHECK(!Judge(f, Frame({}, 100, record)).pass);
  }
  // Only SC2 used: an SC1 pair does not pass, an SC2 pair does.
  {
    e88filter::ScPairFilter only2;
    e88filter::ScPairConfig c2 = c;
    c2.modules = {1};
    CHECK(only2.Configure(def, map, c2, error));
    CHECK(!Judge(only2, Frame({{kFemA, 2, 1000}, {kFemA, 3, 1000}})).pass);
    CHECK(Judge(only2, Frame({{kFemB, 2, 1000}, {kFemB, 3, 1000}})).pass);
    c2.modules = {2};
    CHECK(!only2.Configure(def, map, c2, error));
  }
  // Configuration errors.
  {
    e88filter::ScPairFilter g;
    e88filter::ScPairConfig bad = c;
    bad.windowNs = 0;
    CHECK(!g.Configure(def, map, bad, error));
    bad = c;
    bad.tdcLsbNs = 0;
    CHECK(!g.Configure(def, map, bad, error));
  }

  // Prescale: there is no "off"; every N-th time frame by position.
  {
    e88filter::Prescale p;
    CHECK(!p.Configure(0, 0, error));
    CHECK(!p.Configure(10, 10, error));
    CHECK(p.Configure(10, 3, error));
    int n = 0;
    for (std::uint64_t i = 0; i < 1000; ++i) n += p.Select(i) ? 1 : 0;
    CHECK_EQ(n, 100);
    CHECK(p.Select(3) && p.Select(13) && !p.Select(0));
    CHECK(p.Configure(1, 0, error) && p.Select(7));
  }
  std::remove("t_det.txt");
  std::remove("t_map.txt");
  std::cout << (gFailures ? "test_sc_filter: FAIL\n" : "test_sc_filter: PASS\n");
  return gFailures ? 1 : 0;
}
