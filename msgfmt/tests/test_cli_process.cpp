// Process-level tests: run the built msgfmt binary with files as stdin/stdout.
// MSGFMT_BIN (path of the binary) and MSGFMT_TMP_DIR (writable directory) come from CMake.

#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iterator>
#include <string>

#ifndef _WIN32
#include <sys/wait.h>
#endif

#include "test_framework.hpp"

/// Result of one process run.
struct ProcessResult {
    int code;
    std::string out;
};

/**
 * @brief Writes bytes to a file in binary mode.
 * @param path target file path.
 * @param bytes content to write.
 * @return nothing.
 * @pre the directory of `path` exists and is writable.
 * @post the file contains exactly `bytes`.
 * @throws std::bad_alloc on allocation failure.
 */
static void write_file(const std::string& path, const std::string& bytes) {
    std::ofstream f(path, std::ios::binary | std::ios::trunc);
    f.write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
}

/**
 * @brief Reads a whole file in binary mode.
 * @param path file to read.
 * @return the content; empty if the file is missing or empty.
 * @pre none.
 * @post none.
 * @throws std::bad_alloc on allocation failure.
 */
static std::string read_file(const std::string& path) {
    std::ifstream f(path, std::ios::binary);
    return std::string(std::istreambuf_iterator<char>(f), std::istreambuf_iterator<char>());
}

/**
 * @brief Runs the msgfmt binary with the given argument, feeding `input` on stdin and capturing stdout.
 * @param tag unique short name of the test case (used for temporary file names).
 * @param arg command-line argument, or empty for none.
 * @param input bytes for stdin.
 * @return exit code of the process and its stdout bytes; code is -1 if the process could not be started.
 * @pre MSGFMT_BIN names an existing executable; MSGFMT_TMP_DIR is writable.
 * @post temporary files are removed.
 * @throws std::bad_alloc on allocation failure.
 */
static ProcessResult run_process(const std::string& tag, const std::string& arg, const std::string& input) {
    const std::string base = std::string(MSGFMT_TMP_DIR) + "/proc_" + tag;
    const std::string in_path = base + ".in";
    const std::string out_path = base + ".out";
    const std::string err_path = base + ".err";
    write_file(in_path, input);
    std::string cmd = "\"" + std::string(MSGFMT_BIN) + "\"" + (arg.empty() ? "" : " " + arg) + " < \"" + in_path +
                      "\" > \"" + out_path + "\" 2> \"" + err_path + "\"";
#ifdef _WIN32
    cmd = "\"" + cmd + "\"";
#endif
    int status = std::system(cmd.c_str());
#ifndef _WIN32
    status = WIFEXITED(status) ? WEXITSTATUS(status) : -1;
#endif
    ProcessResult result{status, read_file(out_path)};
    std::remove(in_path.c_str());
    std::remove(out_path.c_str());
    std::remove(err_path.c_str());
    return result;
}

// covers: run, main
TEST(process_parse_example) {
    const ProcessResult r = run_process("parse", "", "Привет, **мир**!");
    CHECK_EQ(r.code, 0);
    CHECK_EQ(r.out, std::string("{\"text\":\"Привет, мир!\",\"ranges\":[{\"style\":\"bold\",\"offset\":8,\"length\":3,\"url\":\"\"}]}"));
}

// covers: run, main
TEST(process_escape) {
    const ProcessResult r = run_process("escape", "escape", "2 * 3");
    CHECK_EQ(r.code, 0);
    CHECK_EQ(r.out, std::string("2 \\* 3"));
}

// covers: run, main
TEST(process_invalid_utf8_in_both_modes) {
    const ProcessResult parse = run_process("bad_parse", "", "\xFF");
    CHECK_EQ(parse.code, 2);
    CHECK_EQ(parse.out, std::string("{\"error\":\"invalid_utf8\"}"));
    const ProcessResult escape = run_process("bad_escape", "escape", "\xFF");
    CHECK_EQ(escape.code, 2);
    CHECK_EQ(escape.out, std::string("{\"error\":\"invalid_utf8\"}"));
}

// covers: run, main
TEST(process_empty_input) {
    const ProcessResult r = run_process("empty", "", "");
    CHECK_EQ(r.code, 0);
    CHECK_EQ(r.out, std::string("{\"text\":\"\",\"ranges\":[]}"));
    const ProcessResult e = run_process("empty_escape", "escape", "");
    CHECK_EQ(e.code, 0);
    CHECK_EQ(e.out, std::string());
}

// covers: run, read_all, set_binary_stdio
TEST(process_keeps_crlf_and_nul_bytes) {
    const ProcessResult r = run_process("crlf", "", std::string("a\r\n**b**\0", 9));
    CHECK_EQ(r.code, 0);
    CHECK_EQ(r.out, std::string("{\"text\":\"a\\r\\nb\\u0000\",\"ranges\":[{\"style\":\"bold\",\"offset\":3,\"length\":1,\"url\":\"\"}]}"));
    const ProcessResult e = run_process("crlf_escape", "escape", std::string("a\r\n*\0\n", 6));
    CHECK_EQ(e.code, 0);
    CHECK_EQ(e.out, std::string("a\r\n\\*\0\n", 7));
}

// covers: run, main
TEST(process_usage_error) {
    const ProcessResult r = run_process("usage", "--bogus", "**a**");
    CHECK_EQ(r.code, 64);
    CHECK(r.out.empty());
}
