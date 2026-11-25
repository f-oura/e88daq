// One run of the SC coincidence filter with the unbiased prescale, shared by the file version and the FairMQ
// device: loads the E88 parameters for the run, judges each time frame, writes the marks of the kept time frames
// and the summary. The caller writes or sends the kept time frames unchanged.
// This file depends on neither ROOT nor FairMQ.
#pragma once

#include <cstdint>
#include <fstream>
#include <map>
#include <string>
#include <vector>

#include "Params.hh"
#include "Prescale.hh"
#include "ScPairFilter.hh"
#include "format/Frames.hh"

namespace e88filter {

struct ScFilterOptions {
  ParamOptions param;
  double windowNs = -1;        // <= 0: sc_pair_window of the settings
  double tdcLsbNs = -1;        // <= 0: tdc_lsb_ns of the settings
  std::vector<int> modules;    // SC modules; empty: every SC plane with two sides
  std::uint64_t prescale = 0;  // required, >= 1
  std::uint64_t prescaleOffset = 0;
};

struct ScFilterCounts {
  std::uint64_t received = 0, kept = 0, scPass = 0, prescaled = 0, prescaleOnly = 0, malformed = 0;
  std::uint64_t bytesIn = 0, bytesOut = 0, scLeading = 0;
  std::map<int, std::uint64_t> passByFirstModule;
};

class ScFilterRun {
 public:
  // Loads the parameters for `run`, configures the filter and creates the marks file (never overwritten).
  bool Begin(const ScFilterOptions& options, std::uint64_t run, const std::string& marksPath, std::string& error);
  // Judges one time frame (8-byte aligned). Returns true when it is kept; the mark is then written.
  bool Process(const std::uint8_t* data, std::size_t size);
  // Closes the marks file and returns the summary as JSON; `extra` is added as further "key": value lines.
  std::string End(const std::vector<std::pair<std::string, std::string>>& extra = {});

  const ScFilterCounts& Counts() const { return fCounts; }
  const Parameters& Params() const { return fParams; }
  const std::string& LastError() const { return fLastError; }

 private:
  ScFilterOptions fOptions;
  Parameters fParams;
  ScPairConfig fConfig;
  ScPairFilter fFilter;
  Prescale fPrescale;
  ScFilterCounts fCounts;
  std::uint64_t fRun = 0;
  std::string fMarksPath;
  std::ofstream fMarks;
  nda::format::TimeFrameView fView;
  std::string fLastError;
};

std::string JsonString(const std::string& s);

}  // namespace e88filter
