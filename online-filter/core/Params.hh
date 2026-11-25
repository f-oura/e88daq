// Loads the E88 parameters (e88param: index, detector definition, channel map, settings) for one run, with the
// SHA-256 of every file used. Uses the readers of the online analyzer (experiments/e88/Params), without ROOT.
#pragma once

#include <cstdint>
#include <string>
#include <utility>
#include <vector>

#include "e88/Params.hh"

namespace e88filter {

struct ParamOptions {
  std::string index;        // param_index v1; selects the files by run number
  std::string detectorDef;  // overrides the index
  std::string channelMap;   // overrides the index
  std::string settings;     // overrides the index
  bool allowUnassigned = false;
};

struct Parameters {
  nda::e88::DetectorDef detectors;
  nda::e88::ChannelMap channels;
  nda::e88::Settings settings;
  bool haveSettings = false;
  std::string runSeries;
  std::vector<std::pair<std::string, std::string>> files;  // (kind, path), in the order read
  std::vector<std::string> sha256;                         // of each file
  std::vector<std::string> warnings;
};

bool LoadParameters(const ParamOptions& options, std::uint64_t run, Parameters& out, std::string& error);

}  // namespace e88filter
