cmake_minimum_required(VERSION 3.20)
project(msgfmt LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

option(MSGFMT_WERROR "Treat compiler warnings as errors" OFF)
option(MSGFMT_SANITIZE "Build with AddressSanitizer and UBSan" OFF)

# Applies the project-wide warning and sanitizer flags to a target.
function(msgfmt_apply_flags target)
  target_compile_options(${target} PRIVATE -Wall -Wextra)
  if(MSGFMT_WERROR)
    target_compile_options(${target} PRIVATE -Werror)
  endif()
  if(MSGFMT_SANITIZE)
    target_compile_options(${target} PRIVATE
      -fsanitize=address,undefined -fno-sanitize-recover=all -fno-omit-frame-pointer)
    target_link_options(${target} PRIVATE -fsanitize=address,undefined)
  endif()
endfunction()

file(GLOB MSGFMT_CORE_SOURCES CONFIGURE_DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/src/*.cpp)
list(FILTER MSGFMT_CORE_SOURCES EXCLUDE REGEX "/main\.cpp$")

add_library(msgfmt_core STATIC ${MSGFMT_CORE_SOURCES})
target_include_directories(msgfmt_core PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
msgfmt_apply_flags(msgfmt_core)

add_executable(msgfmt src/main.cpp)
target_link_libraries(msgfmt PRIVATE msgfmt_core)
msgfmt_apply_flags(msgfmt)

enable_testing()

# Each test executable = its test file + the shared test main, linked with the core library.
function(msgfmt_add_test name)
  add_executable(${name} tests/${name}.cpp tests/test_main.cpp)
  target_include_directories(${name} PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/tests)
  target_link_libraries(${name} PRIVATE msgfmt_core)
  msgfmt_apply_flags(${name})
  add_test(NAME ${name} COMMAND ${name})
endfunction()

msgfmt_add_test(test_framework_selftest)
msgfmt_add_test(test_utf8)
msgfmt_add_test(test_charclass)
msgfmt_add_test(test_parser_utils)
msgfmt_add_test(test_parser_internal)
msgfmt_add_test(test_parser)
msgfmt_add_test(test_parser_props)
msgfmt_add_test(test_escape)
msgfmt_add_test(test_json_validator)
msgfmt_add_test(test_json)
msgfmt_add_test(test_fuzz)
msgfmt_add_test(test_cli)

# Process-level test: runs the built msgfmt binary.
msgfmt_add_test(test_cli_process)
add_dependencies(test_cli_process msgfmt)
target_compile_definitions(test_cli_process PRIVATE
  MSGFMT_BIN="$<TARGET_FILE:msgfmt>"
  MSGFMT_TMP_DIR="${CMAKE_CURRENT_BINARY_DIR}")

# Contract/coverage linter over this directory.
find_package(Python3 COMPONENTS Interpreter)
if(Python3_Interpreter_FOUND)
  set(MSGFMT_PYTHON ${Python3_EXECUTABLE})
else()
  find_program(MSGFMT_PYTHON NAMES python3 python REQUIRED)
endif()
add_test(NAME contracts_lint
  COMMAND ${MSGFMT_PYTHON} ${CMAKE_CURRENT_SOURCE_DIR}/../tools/check_contracts.py
          ${CMAKE_CURRENT_SOURCE_DIR} --tests ${CMAKE_CURRENT_SOURCE_DIR}/tests --check-covers)
