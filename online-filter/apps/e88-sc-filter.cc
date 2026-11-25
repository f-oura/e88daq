// File version of the SC coincidence filter.
//
// Reads a file written by the NestDAQ FileSink, keeps the time frames in which one SC counter (SC1 or SC2, one
// segment) has a leading edge on the up and on the down side within the pair window, and always keeps the time
// frames chosen by an unbiased prescale. The output is a FileSink file of the same form (header, kept time
// frames unchanged, trailer), readable by every program that reads the input.
//
// Next to the output, a marks file lists every kept time frame with the reason(s) it was kept, so that the
// prescaled sample can be selected without bias (reason "prescale", whatever the SC decision was).
// A time frame that cannot be parsed is kept (reason "malformed"): the filter never drops what it cannot judge.
#include <sys/stat.h>

#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <map>
#include <sstream>
#include <string>
#include <vector>

#include "Params.hh"
#include "Prescale.hh"
#include "ScPairFilter.hh"
#include "format/FileReader.hh"
#include "format/Frames.hh"
#include "format/NestdaqHeaders.hh"

namespace {

void Usage() {
  std::cerr <<
      "usage: e88-sc-filter --input RUN.dat --output OUT.dat --prescale N [options]\n"
      "  --input FILE            FileSink file to read\n"
      "  --output FILE           filtered FileSink file to write (must not exist)\n"
      "  --prescale N            keep every N-th time frame regardless of its content (required, N >= 1)\n"
      "  --prescale-offset K     which of the N (0 <= K < N, default 0)\n"
      "  --param INDEX           e88param index; selects the files by run number\n"
      "  --detector-def FILE     detector definition (overrides the index)\n"
      "  --channel-map FILE      channel map (overrides the index)\n"
      "  --settings FILE         settings (overrides the index)\n"
      "  --allow-unassigned      skip channel-map rows with '-' in fem_ip and ch\n"
      "  --run N                 run number for the index (default: from the FileSink header)\n"
      "  --window NS             max |t(up) - t(down)| (default: sc_pair_window of the settings)\n"
      "  --tdc-lsb-ns X          HR-TDC LSB (default: tdc_lsb_ns of the settings)\n"
      "  --sc-modules LIST       SC modules to use, e.g. 0,1 (default: every SC plane with two sides)\n"
      "  --marks FILE            marks of the kept time frames (default: OUTPUT.marks.tsv)\n"
      "  --summary FILE          summary as JSON (default: OUTPUT.summary.json)\n"
      "  --max-frames N          stop after N time frames (the output then has no trailer)\n";
}

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

bool Exists(const std::string& path) {
  struct stat st;
  return ::stat(path.c_str(), &st) == 0;
}

bool ReadBlock(const std::string& path, std::uint64_t offset, std::size_t n, std::vector<char>& out) {
  std::ifstream in(path, std::ios::binary);
  out.assign(n, 0);
  return static_cast<bool>(in.seekg(static_cast<std::streamoff>(offset)).read(out.data(), static_cast<std::streamsize>(n)));
}

}  // namespace

int main(int argc, char** argv) {
  std::string input, output, marksPath, summaryPath, modulesText;
  e88filter::ParamOptions popt;
  long long run = -1, maxFrames = -1;
  unsigned long long factor = 0, offset = 0;
  double window = -1, lsb = -1;
  bool havePrescale = false;
  for (int i = 1; i < argc; ++i) {
    const std::string a = argv[i];
    auto value = [&]() -> std::string {
      if (i + 1 >= argc) {
        std::cerr << "e88-sc-filter: " << a << " needs a value\n";
        std::exit(2);
      }
      return argv[++i];
    };
    if (a == "--input") input = value();
    else if (a == "--output") output = value();
    else if (a == "--prescale") { factor = std::stoull(value()); havePrescale = true; }
    else if (a == "--prescale-offset") offset = std::stoull(value());
    else if (a == "--param") popt.index = value();
    else if (a == "--detector-def") popt.detectorDef = value();
    else if (a == "--channel-map") popt.channelMap = value();
    else if (a == "--settings") popt.settings = value();
    else if (a == "--allow-unassigned") popt.allowUnassigned = true;
    else if (a == "--run") run = std::stoll(value());
    else if (a == "--window") window = std::stod(value());
    else if (a == "--tdc-lsb-ns") lsb = std::stod(value());
    else if (a == "--sc-modules") modulesText = value();
    else if (a == "--marks") marksPath = value();
    else if (a == "--summary") summaryPath = value();
    else if (a == "--max-frames") maxFrames = std::stoll(value());
    else if (a == "-h" || a == "--help") { Usage(); return 0; }
    else { std::cerr << "e88-sc-filter: unknown option " << a << "\n"; Usage(); return 2; }
  }
  if (input.empty() || output.empty() || !havePrescale) {
    std::cerr << "e88-sc-filter: --input, --output and --prescale are required\n";
    Usage();
    return 2;
  }
  if (marksPath.empty()) marksPath = output + ".marks.tsv";
  if (summaryPath.empty()) summaryPath = output + ".summary.json";
  for (const auto& p : {output, marksPath, summaryPath}) {
    if (Exists(p)) {
      std::cerr << "e88-sc-filter: " << p << " exists; outputs are never overwritten\n";
      return 2;
    }
  }
  std::string error;
  e88filter::Prescale prescale;
  if (!prescale.Configure(factor, offset, error)) {
    std::cerr << "e88-sc-filter: " << error << "\n";
    return 2;
  }

  nda::format::FileReader reader;
  if (!reader.Open(input, error)) {
    std::cerr << "e88-sc-filter: " << error << "\n";
    return 1;
  }
  const auto& info = reader.Info();
  const std::uint64_t runNumber = run >= 0 ? static_cast<std::uint64_t>(run) : info.runNumber;
  if (run < 0 && !info.hasFileSinkHeader) {
    std::cerr << "e88-sc-filter: the input has no FileSink header; give --run\n";
    return 2;
  }
  e88filter::Parameters params;
  if (!e88filter::LoadParameters(popt, runNumber, params, error)) {
    std::cerr << "e88-sc-filter: " << error << "\n";
    return 2;
  }
  for (const auto& w : params.warnings) std::cerr << "e88-sc-filter: warning: " << w << "\n";

  e88filter::ScPairConfig config;
  config.windowNs = window > 0 ? window : params.settings.Number("sc_pair_window", 0, -1);
  config.tdcLsbNs = lsb > 0 ? lsb : params.settings.Number("tdc_lsb_ns", 0, -1);
  config.b2ttDataType = static_cast<int>(params.settings.Integer("b2tt_data_type", 0, -1));
  if (!modulesText.empty()) {
    std::stringstream ss(modulesText);
    for (std::string m; std::getline(ss, m, ',');) config.modules.push_back(std::stoi(m));
  }
  e88filter::ScPairFilter filter;
  if (!filter.Configure(params.detectors, params.channels, config, error)) {
    std::cerr << "e88-sc-filter: " << error << " (window and LSB come from --window / --tdc-lsb-ns or the settings)\n";
    return 2;
  }

  std::ofstream out(output, std::ios::binary);
  std::ofstream marks(marksPath);
  if (!out || !marks) {
    std::cerr << "e88-sc-filter: cannot create " << output << " or " << marksPath << "\n";
    return 1;
  }
  marks << "# input_index\ttime_frame_id\toutput_index\treasons\tsc_counters\tfirst_module\tfirst_segment\n";
  constexpr std::size_t kBlock = 304;  // FileSink header and trailer
  if (info.hasFileSinkHeader) {
    std::vector<char> header;
    if (!ReadBlock(input, 0, kBlock, header)) {
      std::cerr << "e88-sc-filter: cannot read the FileSink header again\n";
      return 1;
    }
    out.write(header.data(), static_cast<std::streamsize>(header.size()));
  }

  std::uint64_t received = 0, kept = 0, scPass = 0, prescaled = 0, prescaleOnly = 0, malformed = 0;
  std::uint64_t bytesIn = 0, bytesOut = 0, leading = 0;
  std::map<int, std::uint64_t> passByModule;  // first counter's module
  std::vector<std::uint64_t> buffer;
  nda::format::TimeFrameView view;
  bool complete = false;
  while (maxFrames < 0 || static_cast<long long>(received) < maxFrames) {
    std::size_t size = 0;
    const auto status = reader.Next(buffer, size, error);
    if (status == nda::format::FileReader::Status::kEnd) {
      complete = true;
      break;
    }
    if (status != nda::format::FileReader::Status::kFrame) {
      std::cerr << "e88-sc-filter: " << error << "\n";
      return 1;
    }
    const std::uint64_t index = received++;
    bytesIn += size;
    const auto* header = reinterpret_cast<const ::TimeFrame::Header*>(buffer.data());
    std::string reasons;
    e88filter::ScPairResult r;
    if (!nda::format::ParseTimeFrame(reinterpret_cast<const std::uint8_t*>(buffer.data()), size, view, error)) {
      ++malformed;
      reasons = "malformed";
    } else {
      r = filter.Judge(view);
      leading += r.leading;
      if (r.pass) {
        ++scPass;
        ++passByModule[r.module];
        reasons = "sc";
      }
    }
    if (prescale.Select(index)) {
      ++prescaled;
      if (reasons.empty()) ++prescaleOnly;
      reasons += reasons.empty() ? "prescale" : ",prescale";
    }
    if (reasons.empty()) continue;
    out.write(reinterpret_cast<const char*>(buffer.data()), static_cast<std::streamsize>(size));
    bytesOut += size;
    marks << index << '\t' << header->timeFrameId << '\t' << kept << '\t' << reasons << '\t' << r.counters << '\t'
          << r.module << '\t' << r.segment << '\n';
    ++kept;
  }
  if (complete && info.hasFileSinkTrailer) {
    std::vector<char> trailer;
    if (!ReadBlock(input, reader.Offset() - kBlock, kBlock, trailer)) {
      std::cerr << "e88-sc-filter: cannot read the FileSink trailer again\n";
      return 1;
    }
    out.write(trailer.data(), static_cast<std::streamsize>(trailer.size()));
  }
  out.close();
  marks.close();
  if (!out || !marks) {
    std::cerr << "e88-sc-filter: write error\n";
    return 1;
  }

  std::ostringstream js;
  js << "{\n  \"filter\": \"sc_pair\",\n  \"input\": " << JsonString(input) << ",\n  \"output\": " << JsonString(output)
     << ",\n  \"marks\": " << JsonString(marksPath) << ",\n  \"run_number\": " << runNumber
     << ",\n  \"run_series\": " << JsonString(params.runSeries) << ",\n  \"parameters\": [";
  for (std::size_t i = 0; i < params.files.size(); ++i)
    js << (i ? ", " : "") << "{\"kind\": " << JsonString(params.files[i].first) << ", \"path\": "
       << JsonString(params.files[i].second) << ", \"sha256\": " << JsonString(params.sha256[i]) << "}";
  js << "],\n  \"window_ns\": " << config.windowNs << ",\n  \"tdc_lsb_ns\": " << config.tdcLsbNs
     << ",\n  \"b2tt_data_type\": " << config.b2ttDataType << ",\n  \"sc_counters\": " << filter.Counters().size()
     << ",\n  \"sc_channels\": " << filter.ChannelsUsed() << ",\n  \"prescale_factor\": " << prescale.Factor()
     << ",\n  \"prescale_offset\": " << prescale.Offset() << ",\n  \"complete\": " << (complete ? "true" : "false")
     << ",\n  \"received\": " << received << ",\n  \"kept\": " << kept << ",\n  \"sc_pass\": " << scPass
     << ",\n  \"sc_pass_by_first_module\": {";
  bool first = true;
  for (const auto& [m, n] : passByModule) {
    js << (first ? "" : ", ") << "\"" << m << "\": " << n;
    first = false;
  }
  js << "},\n  \"prescaled\": " << prescaled << ",\n  \"prescale_only\": " << prescaleOnly
     << ",\n  \"malformed_kept\": " << malformed << ",\n  \"sc_leading_edges\": " << leading
     << ",\n  \"bytes_in\": " << bytesIn << ",\n  \"bytes_out\": " << bytesOut << "\n}\n";
  std::ofstream(summaryPath) << js.str();
  std::cout << js.str();
  return 0;
}
