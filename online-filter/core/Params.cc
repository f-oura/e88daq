#include "Params.hh"

#include "util/Sha256.hh"

namespace e88filter {

namespace {

bool Record(Parameters& p, const std::string& kind, const std::string& path, std::string& error) {
  std::string hex;
  if (!nda::util::Sha256File(path, hex, error)) return false;
  p.files.emplace_back(kind, path);
  p.sha256.push_back(hex);
  return true;
}

}  // namespace

bool LoadParameters(const ParamOptions& options, std::uint64_t run, Parameters& out, std::string& error) {
  out = Parameters{};
  nda::e88::ParamFiles files;
  if (!options.index.empty()) {
    if (!nda::e88::ReadParamIndex(options.index, run, files, error)) return false;
    if (!Record(out, "param_index", options.index, error)) return false;
    out.runSeries = files.runSeries;
    out.warnings = files.warnings;
  }
  if (!options.detectorDef.empty()) files.detectorDef = options.detectorDef;
  if (!options.channelMap.empty()) files.channelMap = options.channelMap;
  if (!options.settings.empty()) files.settings = options.settings;
  if (files.detectorDef.empty() || files.channelMap.empty()) {
    error = "a detector definition and a channel map are needed (or a parameter index)";
    return false;
  }
  if (!nda::e88::ReadDetectorDef(files.detectorDef, out.detectors, error)) return false;
  if (!Record(out, "detector_def", files.detectorDef, error)) return false;
  if (!nda::e88::ReadChannelMap(files.channelMap, out.detectors, out.channels, error, options.allowUnassigned))
    return false;
  if (!Record(out, "channel_map", files.channelMap, error)) return false;
  if (run < out.channels.firstRun || run > out.channels.lastRun) {
    error = files.channelMap + ": run " + std::to_string(run) + " is outside valid_runs";
    return false;
  }
  for (const auto& w : out.channels.warnings) out.warnings.push_back("channel map: " + w);
  if (!files.settings.empty()) {
    if (!nda::e88::ReadSettings(files.settings, out.settings, error)) return false;
    if (!Record(out, "settings", files.settings, error)) return false;
    out.haveSettings = true;
    for (const auto& w : out.settings.warnings) out.warnings.push_back("settings: " + w);
  }
  return true;
}

}  // namespace e88filter
