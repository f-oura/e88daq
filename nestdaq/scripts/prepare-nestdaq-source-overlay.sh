#!/usr/bin/env bash
# Create a build-only NestDAQ source copy with bounded local lifecycle fixes.
set -euo pipefail

source_tree="${NESTDDAQ_SOURCE_DIR:-}"
overlay="${1:-${NESTDDAQ_SOURCE_OVERLAY:-}}"

if [[ -z "${source_tree}" || ! -f "${source_tree}/plugins/ParameterConfigPlugin.cxx" ]]; then
  echo "set NESTDDAQ_SOURCE_DIR to the pinned NestDAQ source checkout" >&2
  exit 2
fi
if [[ -z "${overlay}" || "${overlay}" == "${source_tree}" ]]; then
  echo "set a separate NESTDDAQ_SOURCE_OVERLAY build-copy path" >&2
  exit 2
fi
if [[ -e "${overlay}" ]]; then
  echo "refusing to replace existing build-copy path: ${overlay}" >&2
  exit 2
fi
mkdir -p "${overlay}"
cp -a "${source_tree}/." "${overlay}/"
rm -rf "${overlay}/.git"

python3 - "${overlay}" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
source = root / "plugins/ParameterConfigPlugin.cxx"
text = source.read_text()
include_anchor = "#include <sw/redis++/redis++.h>\n"
if text.count(include_anchor) != 1:
    raise SystemExit("unexpected redis++ include")
text = text.replace(include_anchor,
                    "#include <chrono>\n" + include_anchor + "#include <sw/redis++/redis_uri.h>\n",
                    1)
client_old = "    if (!serverUri.empty()) {\n        fClient = std::make_shared<sw::redis::Redis>(serverUri);\n    }\n"
client_new = ("    if (!serverUri.empty()) {\n"
              "        fClient = std::make_shared<sw::redis::Redis>(serverUri);\n"
              "        sw::redis::Uri uri(serverUri);\n"
              "        auto connectionOptions = uri.connection_options();\n"
              "        connectionOptions.socket_timeout = std::chrono::milliseconds{500};\n"
              "        fSubscriberClient = std::make_shared<sw::redis::Redis>(connectionOptions, uri.connection_pool_options());\n"
              "    }\n")
if text.count(client_old) != 1:
    raise SystemExit("unexpected Redis client construction")
text = text.replace(client_old, client_new, 1)
sub_old = "    auto sub = fClient->subscriber();\n"
if text.count(sub_old) != 1:
    raise SystemExit("unexpected parameter subscriber construction")
text = text.replace(sub_old, "    auto sub = fSubscriberClient->subscriber();\n", 1)
old_ctor = '''    if (fClient) {
        ReadParameters();
    }
    fSubscriberThread = std::thread([this]() {
        try {
            SubscribeToParameterChange();
        } catch (const std::exception &e) {
            LOG(error) << MyClass << " in CheckThread" << e.what();
        } catch (...) {
            LOG(error) << MyClass << " unknown error in CheckThread";
        }
    });
    fSubscriberThread.detach();
'''
new_ctor = '''    if (fClient) {
        ReadParameters();
        fSubscriberThread = std::thread([this]() {
            try {
                SubscribeToParameterChange();
            } catch (const std::exception &e) {
                LOG(error) << MyClass << " in CheckThread" << e.what();
            } catch (...) {
                LOG(error) << MyClass << " unknown error in CheckThread";
            }
        });
    }
'''
if text.count(old_ctor) != 1:
    raise SystemExit("unexpected ParameterConfigPlugin subscriber-thread constructor")
text = text.replace(old_ctor, new_ctor, 1)
old_dtor = '''ParameterConfigPlugin::~ParameterConfigPlugin()
{
    UnsubscribeFromDeviceStateChange();
    LOG(debug) << MyClass << " bye";
}
'''
new_dtor = '''ParameterConfigPlugin::~ParameterConfigPlugin()
{
    fPluginShutdownRequested = true;
    UnsubscribeFromDeviceStateChange();
    if (fSubscriberThread.joinable()) {
        fSubscriberThread.join();
    }
    LOG(debug) << MyClass << " bye";
}
'''
if text.count(old_dtor) != 1:
    raise SystemExit("unexpected ParameterConfigPlugin destructor")
text = text.replace(old_dtor, new_dtor, 1)
source.write_text(text)

header = root / "plugins/ParameterConfigPlugin.h"
text = header.read_text()
client_member = '    std::shared_ptr<sw::redis::Redis> fClient;\n'
if text.count(client_member) != 1:
    raise SystemExit("unexpected Redis client member")
text = text.replace(client_member,
                    client_member + '    std::shared_ptr<sw::redis::Redis> fSubscriberClient;\n', 1)
old = '    std::atomic<bool> fPluginShutdownRequested{false};\n'
new = old + '    // The subscriber poll interval bounds shutdown latency before join.\n'
if text.count(old) != 1:
    raise SystemExit("unexpected shutdown flag declaration")
header.write_text(text.replace(old, new, 1))

# NestDAQ's examples link FairMQ by the raw linker name `fairmq`. That skips
# FairMQ::FairMQ's public compile definitions, including the filesystem type
# used in PluginManager's public class layout. Keep the consumers ABI-aligned
# with the installed FairMQ build by linking its imported target.
examples = root / "examples/CMakeLists.txt"
text = examples.read_text()
link_anchor = """      FairLogger;
      fairmq;
"""
link_replacement = """      FairMQ::FairMQ;
"""
if text.count(link_anchor) != 1:
    raise SystemExit("unexpected NestDAQ example FairMQ link block")
examples.write_text(text.replace(link_anchor, link_replacement, 1))

print(f"Prepared patched NestDAQ source overlay at {root}")
PY
