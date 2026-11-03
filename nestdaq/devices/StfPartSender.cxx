// Test driver: sends fixture multipart messages over a FairMQ channel.
//
// It replays saved STF multipart messages into a NestDAQ chain (for example
// TimeFrameBuilder -> FileSink) without a board, a sampler or a real STFBuilder. It reads a plain text file in which each line
// is one message and each whitespace-separated token on that line is one part
// in lowercase hex. That format is this repository's own test-driver
// convention; it says nothing about any upstream layout.
//
// It is not part of any production path.

#include <cstdint>
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

#include <fairmq/Device.h>
#include <fairmq/Parts.h>
#include <fairmq/runDevice.h>

namespace bpo = boost::program_options;

namespace {

std::vector<unsigned char> FromHex(const std::string& text)
{
    if (text.size() % 2 != 0) {
        throw std::runtime_error("hex token has an odd length: " + text);
    }
    auto value = [](char c) -> int {
        if (c >= '0' && c <= '9') return c - '0';
        if (c >= 'a' && c <= 'f') return c - 'a' + 10;
        if (c >= 'A' && c <= 'F') return c - 'A' + 10;
        throw std::runtime_error(std::string("not a hex digit: ") + c);
    };
    std::vector<unsigned char> out;
    out.reserve(text.size() / 2);
    for (std::size_t i = 0; i < text.size(); i += 2) {
        out.push_back(static_cast<unsigned char>((value(text[i]) << 4) | value(text[i + 1])));
    }
    return out;
}

}   // namespace

class StfPartSender : public fair::mq::Device
{
  public:
    struct OptionKey
    {
        static constexpr const char* OutputChannelName = "out-chan-name";
        static constexpr const char* InputPath = "messages-in";
    };

  private:
    void InitTask() override
    {
        fOutputChannelName = fConfig->GetProperty<std::string>(OptionKey::OutputChannelName);
        const auto path = fConfig->GetProperty<std::string>(OptionKey::InputPath);

        std::ifstream in(path);
        if (!in.is_open()) {
            throw std::runtime_error("cannot open fixture messages: " + path);
        }
        std::string line;
        while (std::getline(in, line)) {
            if (line.empty() || line[0] == '#') {
                continue;
            }
            std::istringstream tokens(line);
            std::string token;
            std::vector<std::vector<unsigned char>> message;
            while (tokens >> token) {
                message.push_back(FromHex(token));
            }
            if (!message.empty()) {
                fMessages.push_back(std::move(message));
            }
        }
        LOG(info) << "StfPartSender loaded " << fMessages.size() << " fixture messages from "
                  << path;
    }

    bool ConditionalRun() override
    {
        if (fNext >= fMessages.size()) {
            return false;
        }
        const auto& message = fMessages[fNext];
        fair::mq::Parts parts;
        for (const auto& part : message) {
            auto msg = NewMessageFor(fOutputChannelName, 0, part.size());
            std::memcpy(msg->GetData(), part.data(), part.size());
            parts.AddPart(std::move(msg));
        }
        if (Send(parts, fOutputChannelName) < 0) {
            LOG(warn) << "send failed for fixture message " << fNext;
            return true;
        }
        LOG(info) << "sent fixture message " << fNext << " with " << message.size() << " parts";
        ++fNext;
        return true;
    }

    std::string fOutputChannelName;
    std::vector<std::vector<std::vector<unsigned char>>> fMessages;
    std::size_t fNext{0};
};

void addCustomOptions(bpo::options_description& options)
{
    using opt = StfPartSender::OptionKey;
    options.add_options()(
        opt::OutputChannelName, bpo::value<std::string>()->default_value("out"),
        "output channel name")(
        opt::InputPath, bpo::value<std::string>()->default_value("stf-messages.txt"),
        "fixture file: one message per line, whitespace-separated hex parts");
}

std::unique_ptr<fair::mq::Device> getDevice(fair::mq::ProgOptions& /*config*/)
{
    return std::make_unique<StfPartSender>();
}
