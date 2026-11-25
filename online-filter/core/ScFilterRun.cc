#include "ScFilterRun.hh"

#include <sys/stat.h>

#include <cstdio>
#include <sstream>

#include "format/NestdaqHeaders.hh"

namespace e88filter {

std::string JsonString(const std::string& s) {
  std::string o = "\"";
  for (char c : s) {
    if (c == '"' || c == '\\') o += '\\';
    if (static_cast<unsigned char>(c) < 0x20) {
      char b[8];
      std::snprintf(b, sizeof(b), "\\u%04x", c);
      o += b;
    } else {
      o += c;
    }
  }
  return o + "\"";
}

bool ScFilterRun::Begin(const ScFilterOptions& options, std::uint64_t run, const std::string& marksPath,
                        std::string& error) {
  fOptions = options;
  fRun = run;
  fCounts = ScFilterCounts{};
  fMarksPath = marksPath;
  if (!fPrescale.Configure(options.prescale, options.prescaleOffset, error)) return false;
  fParams = Parameters{};
  fConfig = ScPairConfig{};
  if (options.kind == FilterKind::kSc && !BeginSc(options, run, error)) return false;
  struct stat st;
  if (::stat(marksPath.c_str(), &st) == 0) {
    error = marksPath + " exists; outputs are never overwritten";
    return false;
  }
  fMarks.open(marksPath);
  if (!fMarks) {
    error = "cannot create " + marksPath;
    return false;
  }
  fMarks << "# input_index\ttime_frame_id\toutput_index\treasons\tsc_counters\tfirst_module\tfirst_segment\n";
  return true;
}

bool ScFilterRun::BeginSc(const ScFilterOptions& options, std::uint64_t run, std::string& error) {
  if (!LoadParameters(options.param, run, fParams, error)) return false;
  fConfig = ScPairConfig{};
  fConfig.windowNs = options.windowNs > 0 ? options.windowNs : fParams.settings.Number("sc_pair_window", 0, -1);
  fConfig.tdcLsbNs = options.tdcLsbNs > 0 ? options.tdcLsbNs : fParams.settings.Number("tdc_lsb_ns", 0, -1);
  fConfig.b2ttDataType = static_cast<int>(fParams.settings.Integer("b2tt_data_type", 0, -1));
  fConfig.modules = options.modules;
  if (!fFilter.Configure(fParams.detectors, fParams.channels, fConfig, error)) {
    error += " (window and LSB come from the options or from sc_pair_window and tdc_lsb_ns of the settings)";
    return false;
  }
  return true;
}

bool ScFilterRun::Process(const std::uint8_t* data, std::size_t size) {
  const std::uint64_t index = fCounts.received++;
  fCounts.bytesIn += size;
  std::string reasons;
  ScPairResult r;
  std::uint32_t tfId = 0;
  if (size >= sizeof(::TimeFrame::Header)) tfId = reinterpret_cast<const ::TimeFrame::Header*>(data)->timeFrameId;
  if (fOptions.kind == FilterKind::kPrescale) {
    // The content is not read: a plain prescale keeps whatever it selects.
  } else if (!nda::format::ParseTimeFrame(data, size, fView, fLastError)) {
    ++fCounts.malformed;
    reasons = "malformed";  // never drop what cannot be judged
  } else {
    r = fFilter.Judge(fView);
    fCounts.scLeading += r.leading;
    if (r.pass) {
      ++fCounts.scPass;
      ++fCounts.passByFirstModule[r.module];
      reasons = "sc";
    }
  }
  if (fPrescale.Select(index)) {
    ++fCounts.prescaled;
    if (reasons.empty()) ++fCounts.prescaleOnly;
    reasons += reasons.empty() ? "prescale" : ",prescale";
  }
  if (reasons.empty()) return false;
  fMarks << index << '\t' << tfId << '\t' << fCounts.kept << '\t' << reasons << '\t' << r.counters << '\t' << r.module
         << '\t' << r.segment << '\n';
  ++fCounts.kept;
  fCounts.bytesOut += size;
  return true;
}

std::string ScFilterRun::End(const std::vector<std::pair<std::string, std::string>>& extra) {
  fMarks.close();
  const auto& c = fCounts;
  std::ostringstream js;
  js << "{\n  \"filter\": \"" << KindName(fOptions.kind) << "\",\n  \"run_number\": " << fRun << ",\n  \"run_series\": "
     << JsonString(fParams.runSeries) << ",\n  \"marks\": " << JsonString(fMarksPath) << ",\n  \"parameters\": [";
  for (std::size_t i = 0; i < fParams.files.size(); ++i)
    js << (i ? ", " : "") << "{\"kind\": " << JsonString(fParams.files[i].first) << ", \"path\": "
       << JsonString(fParams.files[i].second) << ", \"sha256\": " << JsonString(fParams.sha256[i]) << "}";
  js << "]";
  if (fOptions.kind == FilterKind::kSc)
    js << ",\n  \"window_ns\": " << fConfig.windowNs << ",\n  \"tdc_lsb_ns\": " << fConfig.tdcLsbNs
     << ",\n  \"b2tt_data_type\": " << fConfig.b2ttDataType << ",\n  \"sc_counters\": " << fFilter.Counters().size()
     << ",\n  \"sc_channels\": " << fFilter.ChannelsUsed();
  js << ",\n  \"prescale_factor\": " << fPrescale.Factor()
     << ",\n  \"prescale_offset\": " << fPrescale.Offset();
  for (const auto& [k, v] : extra) js << ",\n  " << JsonString(k) << ": " << v;
  js << ",\n  \"received\": " << c.received << ",\n  \"kept\": " << c.kept << ",\n  \"sc_pass\": " << c.scPass
     << ",\n  \"sc_pass_by_first_module\": {";
  bool first = true;
  for (const auto& [m, n] : c.passByFirstModule) {
    js << (first ? "" : ", ") << "\"" << m << "\": " << n;
    first = false;
  }
  js << "},\n  \"prescaled\": " << c.prescaled << ",\n  \"prescale_only\": " << c.prescaleOnly
     << ",\n  \"malformed_kept\": " << c.malformed << ",\n  \"sc_leading_edges\": " << c.scLeading
     << ",\n  \"bytes_in\": " << c.bytesIn << ",\n  \"bytes_out\": " << c.bytesOut << "\n}\n";
  return js.str();
}

}  // namespace e88filter
