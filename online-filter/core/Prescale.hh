// Unbiased prescale: selects one time frame out of `factor` by its position in the input, without looking at
// its content. Every filter of this directory always runs one, so that the selected time frames form an
// unbiased sample (DEC-003 of the records repository: an unbiased prescale is always mixed in).
// This file depends on neither ROOT nor FairMQ.
#pragma once

#include <cstdint>
#include <string>

namespace e88filter {

class Prescale {
 public:
  // factor >= 1 (1 selects every time frame), 0 <= offset < factor. There is no "off" setting.
  bool Configure(std::uint64_t factor, std::uint64_t offset, std::string& error) {
    if (factor == 0) {
      error = "prescale factor must be at least 1 (an unbiased prescale is always mixed in)";
      return false;
    }
    if (offset >= factor) {
      error = "prescale offset must be below the factor";
      return false;
    }
    fFactor = factor;
    fOffset = offset;
    return true;
  }
  // `index` counts every time frame received, from 0.
  bool Select(std::uint64_t index) const { return fFactor > 0 && index % fFactor == fOffset; }
  std::uint64_t Factor() const { return fFactor; }
  std::uint64_t Offset() const { return fOffset; }

 private:
  std::uint64_t fFactor = 0;
  std::uint64_t fOffset = 0;
};

}  // namespace e88filter
