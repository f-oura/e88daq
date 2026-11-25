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

#include <cstdlib>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

#include "ScFilterRun.hh"
#include "format/FileReader.hh"

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
  std::string input, output, marksPath, summaryPath;
  e88filter::ScFilterOptions options;
  long long run = -1, maxFrames = -1;
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
    else if (a == "--prescale") { options.prescale = std::stoull(value()); havePrescale = true; }
    else if (a == "--prescale-offset") options.prescaleOffset = std::stoull(value());
    else if (a == "--param") options.param.index = value();
    else if (a == "--detector-def") options.param.detectorDef = value();
    else if (a == "--channel-map") options.param.channelMap = value();
    else if (a == "--settings") options.param.settings = value();
    else if (a == "--allow-unassigned") options.param.allowUnassigned = true;
    else if (a == "--run") run = std::stoll(value());
    else if (a == "--window") options.windowNs = std::stod(value());
    else if (a == "--tdc-lsb-ns") options.tdcLsbNs = std::stod(value());
    else if (a == "--sc-modules") {
      std::stringstream ss(value());
      for (std::string m; std::getline(ss, m, ',');) options.modules.push_back(std::stoi(m));
    }
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
  nda::format::FileReader reader;
  if (!reader.Open(input, error)) {
    std::cerr << "e88-sc-filter: " << error << "\n";
    return 1;
  }
  const auto& info = reader.Info();
  if (run < 0 && !info.hasFileSinkHeader) {
    std::cerr << "e88-sc-filter: the input has no FileSink header; give --run\n";
    return 2;
  }
  const std::uint64_t runNumber = run >= 0 ? static_cast<std::uint64_t>(run) : info.runNumber;
  e88filter::ScFilterRun filter;
  if (!filter.Begin(options, runNumber, marksPath, error)) {
    std::cerr << "e88-sc-filter: " << error << "\n";
    return 2;
  }
  for (const auto& w : filter.Params().warnings) std::cerr << "e88-sc-filter: warning: " << w << "\n";

  std::ofstream out(output, std::ios::binary);
  if (!out) {
    std::cerr << "e88-sc-filter: cannot create " << output << "\n";
    return 1;
  }
  constexpr std::size_t kBlock = 304;  // FileSink header and trailer
  if (info.hasFileSinkHeader) {
    std::vector<char> header;
    if (!ReadBlock(input, 0, kBlock, header)) {
      std::cerr << "e88-sc-filter: cannot read the FileSink header again\n";
      return 1;
    }
    out.write(header.data(), static_cast<std::streamsize>(header.size()));
  }
  std::vector<std::uint64_t> buffer;
  bool complete = false;
  while (maxFrames < 0 || static_cast<long long>(filter.Counts().received) < maxFrames) {
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
    if (filter.Process(reinterpret_cast<const std::uint8_t*>(buffer.data()), size))
      out.write(reinterpret_cast<const char*>(buffer.data()), static_cast<std::streamsize>(size));
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
  if (!out) {
    std::cerr << "e88-sc-filter: write error on " << output << "\n";
    return 1;
  }
  const std::string summary = filter.End({{"input", e88filter::JsonString(input)},
                                          {"output", e88filter::JsonString(output)},
                                          {"complete", complete ? "true" : "false"}});
  std::ofstream(summaryPath) << summary;
  std::cout << summary;
  return 0;
}
