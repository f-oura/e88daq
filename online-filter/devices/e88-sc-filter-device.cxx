// e88-sc-filter-device: FairMQ device of the SC coincidence filter.
//
// Receives time frames (for example from the "dqm" output of the TimeFrameBuilder), keeps those in which one SC
// counter has a leading edge on the up and on the down side within the pair window, and those chosen by the
// unbiased prescale, and sends the kept time frames unchanged (the same multipart message) to its output channel,
// for example to a second FileSink. The decision is the one of the file version (core/ScFilterRun).
//
// The run number comes from the NestDAQ run information (property run_number, set by the DAQ service plugin, as
// for the FileSink); the parameters are read for that run at every RUN. For each run the device writes
// <marks-dir>/run<NNNNNN>_sc.marks.tsv (reasons of every kept time frame) and run<NNNNNN>_sc.summary.json.
//
// The device must never hold up the main data stream: on the "dqm" output the TimeFrameBuilder sends only when
// this device can take the time frame, and skips it otherwise. If the parameters cannot be read, the device logs
// the error, sends nothing for that run and records the error in the summary.
#include <fairmq/runDevice.h>

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

#include "ScFilterRun.hh"

namespace bpo = boost::program_options;

namespace {

struct OptionKey {
  static constexpr const char* InputChannelName = "in-chan-name";
  static constexpr const char* OutputChannelName = "out-chan-name";
  static constexpr const char* RunNumber = "run_number";
  static constexpr const char* MarksDir = "marks-dir";
  static constexpr const char* Prescale = "prescale";
  static constexpr const char* PrescaleOffset = "prescale-offset";
  static constexpr const char* Window = "window";
  static constexpr const char* TdcLsb = "tdc-lsb-ns";
  static constexpr const char* Modules = "sc-modules";
  static constexpr const char* Param = "e88-param";
  static constexpr const char* DetectorDef = "e88-detector-def";
  static constexpr const char* ChannelMap = "e88-channel-map";
  static constexpr const char* Settings = "e88-settings";
  static constexpr const char* AllowUnassigned = "e88-allow-unassigned";
};

std::string RunStem(std::uint64_t run) {
  char b[32];
  std::snprintf(b, sizeof(b), "run%06llu_sc", static_cast<unsigned long long>(run));
  return b;
}

class ScFilterDevice : public fair::mq::Device {
 protected:
  void InitTask() override {
    fIn = fConfig->GetProperty<std::string>(OptionKey::InputChannelName);
    fOut = fConfig->GetProperty<std::string>(OptionKey::OutputChannelName);
    fMarksDir = fConfig->GetProperty<std::string>(OptionKey::MarksDir);
    if (fMarksDir.empty()) throw std::runtime_error("--marks-dir is required");
    fOptions = e88filter::ScFilterOptions{};
    fOptions.prescale = std::stoull(fConfig->GetProperty<std::string>(OptionKey::Prescale));
    fOptions.prescaleOffset = std::stoull(fConfig->GetProperty<std::string>(OptionKey::PrescaleOffset));
    if (fOptions.prescale == 0) throw std::runtime_error("--prescale N (N >= 1) is required: an unbiased prescale is always mixed in");
    fOptions.windowNs = std::stod(fConfig->GetProperty<std::string>(OptionKey::Window));
    fOptions.tdcLsbNs = std::stod(fConfig->GetProperty<std::string>(OptionKey::TdcLsb));
    std::stringstream ss(fConfig->GetProperty<std::string>(OptionKey::Modules));
    for (std::string m; std::getline(ss, m, ',');)
      if (!m.empty()) fOptions.modules.push_back(std::stoi(m));
    fOptions.param.index = fConfig->GetProperty<std::string>(OptionKey::Param);
    fOptions.param.detectorDef = fConfig->GetProperty<std::string>(OptionKey::DetectorDef);
    fOptions.param.channelMap = fConfig->GetProperty<std::string>(OptionKey::ChannelMap);
    fOptions.param.settings = fConfig->GetProperty<std::string>(OptionKey::Settings);
    fOptions.param.allowUnassigned = fConfig->GetProperty<bool>(OptionKey::AllowUnassigned);
    LOG(info) << "e88-sc-filter: prescale " << fOptions.prescale << " offset " << fOptions.prescaleOffset
              << ", marks in " << fMarksDir;
  }

  void PreRun() override {
    fRun = 0;
    if (fConfig->Count(OptionKey::RunNumber)) fRun = std::stoull(fConfig->GetProperty<std::string>(OptionKey::RunNumber));
    fStem = fMarksDir + "/" + RunStem(fRun);
    fSendFailed = 0;
    std::string error;
    fActive = fFilter.Begin(fOptions, fRun, fStem + ".marks.tsv", error);
    fError = fActive ? "" : error;
    if (!fActive) {
      LOG(error) << "e88-sc-filter: run " << fRun << ": " << error << "; nothing is sent in this run";
      return;
    }
    for (std::size_t i = 0; i < fFilter.Params().files.size(); ++i)
      LOG(info) << "e88-sc-filter: " << fFilter.Params().files[i].first << " " << fFilter.Params().files[i].second
                << " sha256 " << fFilter.Params().sha256[i];
    for (const auto& w : fFilter.Params().warnings) LOG(warn) << "e88-sc-filter: " << w;
    LOG(info) << "e88-sc-filter: run " << fRun << " started";
  }

  bool ConditionalRun() override {
    fair::mq::Parts parts;
    if (Receive(parts, fIn, 0, 200) <= 0) return true;  // timeout in ms
    if (!fActive) {
      ++fReceivedInactive;
      return true;
    }
    // The parts are the time frame header, the sub time frame headers and the heartbeat frames in file order.
    std::size_t size = 0;
    for (const auto& part : parts) size += part->GetSize();
    fBuffer.resize((size + 7) / 8);
    auto* out = reinterpret_cast<std::uint8_t*>(fBuffer.data());
    for (const auto& part : parts) {
      std::memcpy(out, part->GetData(), part->GetSize());
      out += part->GetSize();
    }
    if (!fFilter.Process(reinterpret_cast<const std::uint8_t*>(fBuffer.data()), size)) return true;
    while (Send(parts, fOut, 0, 1000) < 0) {  // timeout in ms
      if (NewStatePending()) {
        ++fSendFailed;
        break;
      }
    }
    return true;
  }

  void PostRun() override {
    const std::string summaryPath = fStem + ".summary.json";
    std::string summary;
    if (fActive) {
      summary = fFilter.End({{"device", "true"}, {"send_failed", std::to_string(fSendFailed)}});
      const auto& c = fFilter.Counts();
      LOG(info) << "e88-sc-filter: run " << fRun << ": received " << c.received << ", kept " << c.kept << " (sc "
                << c.scPass << ", prescaled " << c.prescaled << ", malformed " << c.malformed << "), send failed "
                << fSendFailed;
    } else {
      summary = "{\n  \"filter\": \"sc_pair\",\n  \"device\": true,\n  \"run_number\": " + std::to_string(fRun) +
                ",\n  \"error\": " + e88filter::JsonString(fError) + ",\n  \"received\": " +
                std::to_string(fReceivedInactive) + ",\n  \"kept\": 0\n}\n";
    }
    std::ifstream exists(summaryPath);
    if (exists.good()) {
      LOG(error) << "e88-sc-filter: " << summaryPath << " exists; summary not written";
    } else {
      std::ofstream(summaryPath) << summary;
    }
    fActive = false;
    fReceivedInactive = 0;
  }

 private:
  std::string fIn, fOut, fMarksDir, fStem, fError;
  e88filter::ScFilterOptions fOptions;
  e88filter::ScFilterRun fFilter;
  std::vector<std::uint64_t> fBuffer;
  std::uint64_t fRun = 0, fSendFailed = 0, fReceivedInactive = 0;
  bool fActive = false;
};

}  // namespace

void addCustomOptions(bpo::options_description& options) {
  options.add_options()
    (OptionKey::InputChannelName, bpo::value<std::string>()->default_value("in"), "Name of the input channel")
    (OptionKey::OutputChannelName, bpo::value<std::string>()->default_value("out"), "Name of the output channel")
    (OptionKey::RunNumber, bpo::value<std::string>(), "Run number (given by the DAQ service plugin)")
    (OptionKey::MarksDir, bpo::value<std::string>()->default_value(""),
     "Directory of run<NNNNNN>_sc.marks.tsv and run<NNNNNN>_sc.summary.json (required)")
    (OptionKey::Prescale, bpo::value<std::string>()->default_value("0"),
     "Keep one time frame out of N regardless of its content (required, N >= 1)")
    (OptionKey::PrescaleOffset, bpo::value<std::string>()->default_value("0"), "Which of the N (0 <= K < N)")
    (OptionKey::Window, bpo::value<std::string>()->default_value("-1"),
     "Max |t(up) - t(down)| [ns] (default: sc_pair_window of the settings)")
    (OptionKey::TdcLsb, bpo::value<std::string>()->default_value("-1"), "HR-TDC LSB [ns] (default: tdc_lsb_ns of the settings)")
    (OptionKey::Modules, bpo::value<std::string>()->default_value(""),
     "Comma-separated SC modules (default: every SC plane with two sides)")
    (OptionKey::Param, bpo::value<std::string>()->default_value(""), "E88 parameter index (param_index v1)")
    (OptionKey::DetectorDef, bpo::value<std::string>()->default_value(""), "E88 detector definition, overrides the index")
    (OptionKey::ChannelMap, bpo::value<std::string>()->default_value(""), "E88 channel map, overrides the index")
    (OptionKey::Settings, bpo::value<std::string>()->default_value(""), "E88 settings, overrides the index")
    (OptionKey::AllowUnassigned, bpo::value<bool>()->default_value(false)->implicit_value(true),
     "Skip E88 channel-map rows with '-' in fem_ip and ch instead of failing");
}

std::unique_ptr<fair::mq::Device> getDevice(fair::mq::ProgOptions& /*config*/) { return std::make_unique<ScFilterDevice>(); }
