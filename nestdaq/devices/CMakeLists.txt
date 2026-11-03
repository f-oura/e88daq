# E88 FairMQ devices, built against a NestDAQ install prefix.
# Build output stays outside the repository; see ../scripts/build-devices.sh.
cmake_minimum_required(VERSION 3.16)
project(e88daq_devices CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(Threads REQUIRED)
find_package(Boost REQUIRED COMPONENTS container program_options filesystem regex)
find_package(FairLogger REQUIRED)
find_package(FairMQ 1.10.0 REQUIRED)

set(e88daq_device_libraries
  FairMQ::FairMQ
  Boost::container
  Boost::program_options
  Boost::filesystem
  Boost::regex
  Threads::Threads
)

foreach(device StfPartSender)
  add_executable(${device} ${device}.cxx)
  target_include_directories(${device} PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
  target_link_libraries(${device} PRIVATE ${e88daq_device_libraries})
endforeach()
