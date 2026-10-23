#include <sstream>

#include "parser_test_utils.hpp"

using msgfmt::Style;
using ptest::rg;

namespace {

/**
 * @brief Test body that feeds check_invariants a broken result, to prove that it reports a failure.
 * @return nothing.
 * @pre none.
 * @post exactly one failure recorded.
 * @throws does not throw.
 */
void broken_result_body() {
    ptest::check_invariants(msgfmt::ParseResult{"ab", {rg(Style::Bold, 0, 5)}}, __FILE__, __LINE__);
}

/**
 * @brief Test body whose expectation does not match the parsed result.
 * @return nothing.
 * @pre none.
 * @post at least one failure recorded.
 * @throws does not throw.
 */
void mismatching_expectation_body() {
    EXPECT_PARSE("**a**", "a", rg(Style::Italic, 0, 1));
}

/**
 * @brief Test body that gives invalid UTF-8 to expect_parse.
 * @return nothing.
 * @pre none.
 * @post exactly one failure recorded.
 * @throws does not throw.
 */
void invalid_input_body() {
    EXPECT_PARSE("\xFF", "", );
}

}  // namespace

// covers: rg
TEST(rg_builds_range) {
    const msgfmt::Range r = rg(Style::Link, 1, 2, "u");
    CHECK(r.style == Style::Link && r.offset == 1 && r.length == 2 && r.url == "u");
    CHECK(rg(Style::Bold, 0, 1).url.empty());
}

// covers: describe
TEST(describe_formats_text_and_ranges) {
    const msgfmt::ParseResult r{"ab", {rg(Style::Bold, 0, 1), rg(Style::Link, 1, 1, "u")}};
    CHECK_EQ(ptest::describe(r), std::string("\"ab\" [bold(0,1) link(1,1,u)]"));
    CHECK_EQ(ptest::describe(msgfmt::ParseResult{}), std::string("\"\" []"));
}

// covers: find_invariant_violations
TEST(invariants_accept_good_results) {
    CHECK(ptest::find_invariant_violations(msgfmt::ParseResult{}).empty());
    const msgfmt::ParseResult nested{"abc", {rg(Style::Bold, 0, 3), rg(Style::Italic, 1, 1), rg(Style::Code, 2, 1)}};
    CHECK(ptest::find_invariant_violations(nested).empty());
    const msgfmt::ParseResult emoji{"\xF0\x9F\x9A\x80" "a", {rg(Style::Bold, 0, 2), rg(Style::Italic, 2, 1)}};
    CHECK(ptest::find_invariant_violations(emoji).empty());
    const msgfmt::ParseResult same_interval{"a", {rg(Style::Bold, 0, 1), rg(Style::Italic, 0, 1)}};
    CHECK(ptest::find_invariant_violations(same_interval).empty());
}

// covers: find_invariant_violations
TEST(invariants_detect_each_kind_of_violation) {
    using ptest::find_invariant_violations;
    CHECK_EQ(find_invariant_violations(msgfmt::ParseResult{"\xFF", {}}).size(), std::size_t{1});
    CHECK(!find_invariant_violations(msgfmt::ParseResult{"ab", {rg(Style::Bold, 1, 0)}}).empty());
    CHECK(!find_invariant_violations(msgfmt::ParseResult{"ab", {rg(Style::Bold, 1, 2)}}).empty());
    CHECK(!find_invariant_violations(msgfmt::ParseResult{"ab", {rg(Style::Bold, 0, 1, "u")}}).empty());
    CHECK(!find_invariant_violations(msgfmt::ParseResult{"ab", {rg(Style::Link, 0, 1)}}).empty());
    CHECK(!find_invariant_violations(
                msgfmt::ParseResult{"ab", {rg(Style::Bold, 1, 1), rg(Style::Italic, 0, 1)}}).empty());
    CHECK(!find_invariant_violations(
                msgfmt::ParseResult{"ab", {rg(Style::Italic, 0, 1), rg(Style::Bold, 0, 2)}}).empty());
    CHECK(!find_invariant_violations(
                msgfmt::ParseResult{"abc", {rg(Style::Bold, 0, 2), rg(Style::Italic, 1, 2)}}).empty());
    CHECK(!find_invariant_violations(
                msgfmt::ParseResult{"abc", {rg(Style::Bold, 0, 3), rg(Style::Bold, 1, 1)}}).empty());
    CHECK(!find_invariant_violations(
                msgfmt::ParseResult{"\xF0\x9F\x9A\x80", {rg(Style::Bold, 0, 1)}}).empty());
    CHECK(!find_invariant_violations(
                msgfmt::ParseResult{"\xF0\x9F\x9A\x80" "a", {rg(Style::Bold, 1, 2)}}).empty());
}

// covers: check_invariants
TEST(check_invariants_passes_good_and_reports_bad_results) {
    CHECK(ptest::check_invariants(msgfmt::ParseResult{"a", {rg(Style::Bold, 0, 1)}}, __FILE__, __LINE__));
    std::ostringstream sink;
    CHECK_EQ(tf::run_case(broken_result_body, sink), 1);
    CHECK(sink.str().find("invariant violated") != std::string::npos);
}

// covers: expect_parse
TEST(expect_parse_accepts_match_and_reports_mismatch_and_rejection) {
    CHECK(ptest::expect_parse(__FILE__, __LINE__, "**a**", "a", {rg(Style::Bold, 0, 1)}));
    std::ostringstream sink;
    CHECK(tf::run_case(mismatching_expectation_body, sink) > 0);
    CHECK(sink.str().find("expected") != std::string::npos);
    std::ostringstream sink2;
    CHECK_EQ(tf::run_case(invalid_input_body, sink2), 1);
    CHECK(sink2.str().find("rejected") != std::string::npos);
}
