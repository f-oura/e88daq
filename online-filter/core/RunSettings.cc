#include "RunSettings.hh"

#include <fstream>
#include <sstream>

namespace e88filter {

bool ReadRunSettings(const std::string& path, RunSettings& settings, std::string& error) {
  std::ifstream in(path);
  if (!in) {
    error = "cannot read run settings " + path;
    return false;
  }
  RunSettings out = settings;
  std::string line;
  for (int number = 1; std::getline(in, line); ++number) {
    const auto hash = line.find('#');
    if (hash != std::string::npos) line.erase(hash);
    std::istringstream words(line);
    std::string key, value, extra;
    if (!(words >> key)) continue;
    const std::string where = path + ":" + std::to_string(number) + ": ";
    if (!(words >> value) || (words >> extra)) {
      error = where + "expected one key and one value";
      return false;
    }
    if (key == "kind") {
      if (value != "sc" && value != "prescale" && value != "off") {
        error = where + "kind must be sc, prescale or off";
        return false;
      }
      out.kind = value;
    } else if (key == "prescale") {
      if (value.empty() || value.find_first_not_of("0123456789") != std::string::npos || value.size() > 18 ||
          (value.size() > 1 && value[0] == '0') || std::stoull(value) == 0) {
        error = where + "prescale must be an integer >= 1";
        return false;
      }
      out.prescale = std::stoull(value);
    } else {
      error = where + "unknown key " + key;
      return false;
    }
  }
  settings = out;
  return true;
}

}  // namespace e88filter
