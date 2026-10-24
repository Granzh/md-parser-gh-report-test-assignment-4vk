#include <sstream>
#include <stdexcept>
#include <streambuf>
#include <string>
#include <vector>

#include "json_validator.hpp"
#include "msgfmt/cli.hpp"
#include "test_framework.hpp"

/// Stream buffer that fails on every read, to simulate an I/O error.
class ThrowingBuf : public std::streambuf {
protected:
    /**
     * @brief Simulates a failing read.
     * @return never returns normally.
     * @pre none.
     * @post none.
     * @throws std::runtime_error always.
     */
    int_type underflow() override { throw std::runtime_error("disk on fire"); }
};

/// Result of one CLI invocation.
struct Outcome {
    int code;
    std::string out;
    std::string err;
};

/**
 * @brief Runs msgfmt::run on in-memory streams.
 * @param args command-line arguments without the program name.
 * @param input bytes given on stdin.
 * @return exit code, stdout and stderr contents.
 * @pre none.
 * @post none.
 * @throws std::bad_alloc on allocation failure.
 */
static Outcome invoke(const std::vector<std::string>& args, const std::string& input) {
    std::istringstream in(input);
    std::ostringstream out;
    std::ostringstream err;
    const int code = msgfmt::run(args, in, out, err);
    return Outcome{code, out.str(), err.str()};
}

// covers: run
TEST(run_parse_mode_example_from_the_specification) {
    const Outcome o = invoke({}, "Привет, **мир**!");
    CHECK_EQ(o.code, 0);
    CHECK_EQ(o.out, std::string("{\"text\":\"Привет, мир!\",\"ranges\":[{\"style\":\"bold\",\"offset\":8,\"length\":3,\"url\":\"\"}]}"));
    CHECK(o.err.empty());
}

// covers: run
TEST(run_parse_mode_empty_input) {
    const Outcome o = invoke({}, "");
    CHECK_EQ(o.code, 0);
    CHECK_EQ(o.out, std::string("{\"text\":\"\",\"ranges\":[]}"));
}

// covers: run
TEST(run_parse_mode_invalid_utf8_gives_error_and_code_2) {
    const Outcome o = invoke({}, "**a**\xFF");
    CHECK_EQ(o.code, 2);
    CHECK_EQ(o.out, std::string("{\"error\": \"invalid_utf8\"}"));
    CHECK(jsonv::is_valid_json(o.out));
}

// covers: run
TEST(run_parse_mode_keeps_crlf_nul_and_missing_final_newline) {
    const Outcome crlf = invoke({}, "a\r\n**b**\r\n");
    CHECK_EQ(crlf.code, 0);
    CHECK_EQ(crlf.out, std::string("{\"text\":\"a\\r\\nb\\r\\n\",\"ranges\":[{\"style\":\"bold\",\"offset\":3,\"length\":1,\"url\":\"\"}]}"));
    const Outcome nul = invoke({}, std::string("a\0**b**", 7));
    CHECK_EQ(nul.code, 0);
    CHECK_EQ(nul.out, std::string("{\"text\":\"a\\u0000b\",\"ranges\":[{\"style\":\"bold\",\"offset\":2,\"length\":1,\"url\":\"\"}]}"));
    const Outcome spaces = invoke({}, "  **a**  ");
    CHECK_EQ(spaces.out, std::string("{\"text\":\"  a  \",\"ranges\":[{\"style\":\"bold\",\"offset\":2,\"length\":1,\"url\":\"\"}]}"));
    CHECK(crlf.out.back() == '}' && nul.out.back() == '}');
}

// covers: run
TEST(run_escape_mode) {
    const Outcome o = invoke({"escape"}, "2 * 3");
    CHECK_EQ(o.code, 0);
    CHECK_EQ(o.out, std::string("2 \\* 3"));
    CHECK(o.err.empty());
    CHECK_EQ(invoke({"escape"}, "").out, std::string());
    CHECK_EQ(invoke({"escape"}, "").code, 0);
}

// covers: run
TEST(run_escape_mode_preserves_other_bytes) {
    const Outcome o = invoke({"escape"}, std::string("a\r\n\0Привет 🚀\n", 22));
    CHECK_EQ(o.code, 0);
    CHECK_EQ(o.out, std::string("a\r\n\0Привет 🚀\n", 22));
    const Outcome all = invoke({"escape"}, "*_`[]()\\");
    CHECK_EQ(all.out, std::string("\\*\\_\\`\\[\\]\\(\\)\\\\"));
}

// covers: run
TEST(run_escape_mode_invalid_utf8_gives_error_and_code_2) {
    const Outcome o = invoke({"escape"}, "*\xFF");
    CHECK_EQ(o.code, 2);
    CHECK_EQ(o.out, std::string("{\"error\": \"invalid_utf8\"}"));
}

// covers: run
TEST(run_usage_errors_do_not_read_stdin_and_print_nothing_to_stdout) {
    const std::vector<std::vector<std::string>> bad = {
        {"--help"}, {"parse"}, {"escape", "extra"}, {"Escape"}, {"escape", ""}, {""}, {"a", "b", "c"},
    };
    for (const std::vector<std::string>& args : bad) {
        std::istringstream in("**a**");
        std::ostringstream out;
        std::ostringstream err;
        CHECK_EQ(msgfmt::run(args, in, out, err), msgfmt::kExitUsage);
        CHECK(out.str().empty());
        CHECK_EQ(err.str(), msgfmt::usage_text());
        CHECK(in.tellg() == 0);
    }
    CHECK(msgfmt::kExitUsage != 0 && msgfmt::kExitUsage != 2);
}

// covers: run
TEST(run_reports_internal_errors_with_code_70) {
    ThrowingBuf buf;
    std::istream in(&buf);
    in.exceptions(std::ios::badbit);
    std::ostringstream out;
    std::ostringstream err;
    CHECK_EQ(msgfmt::run({}, in, out, err), msgfmt::kExitInternal);
    CHECK(out.str().empty());
    CHECK(err.str().find("disk on fire") != std::string::npos);
}

// covers: run
TEST(run_is_deterministic) {
    const std::string input = "**a _b_** [c](u) `d` \\* 🚀";
    const Outcome first = invoke({}, input);
    const Outcome second = invoke({}, input);
    CHECK_EQ(first.code, second.code);
    CHECK_EQ(first.out, second.out);
    CHECK(jsonv::is_valid_json(first.out));
}

// covers: read_all
TEST(read_all_returns_bytes_unchanged) {
    std::istringstream a(std::string("a\r\n\0b \n", 7));
    CHECK_EQ(msgfmt::read_all(a), std::string("a\r\n\0b \n", 7));
    std::istringstream empty("");
    CHECK_EQ(msgfmt::read_all(empty), std::string());
    std::istringstream spaces("  x  ");
    CHECK_EQ(msgfmt::read_all(spaces), std::string("  x  "));
    const std::string big(300000, 'z');
    std::istringstream large(big);
    CHECK(msgfmt::read_all(large) == big);
}

// covers: usage_text
TEST(usage_text_mentions_both_modes_and_ends_with_newline) {
    const std::string u = msgfmt::usage_text();
    CHECK(u.find("escape") != std::string::npos);
    CHECK(u.find("usage") != std::string::npos);
    CHECK(u.back() == '\n');
}

// covers: set_binary_stdio
TEST(set_binary_stdio_can_be_called_repeatedly) {
    msgfmt::set_binary_stdio();
    msgfmt::set_binary_stdio();
    CHECK(true);
}
