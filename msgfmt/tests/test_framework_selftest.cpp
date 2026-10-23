#include <optional>
#include <sstream>
#include <stdexcept>

#include "msgfmt/contract.hpp"
#include "test_framework.hpp"

namespace {

/**
 * @brief Test body that passes: all checks hold.
 * @return nothing.
 * @pre none.
 * @post no failure recorded.
 * @throws does not throw.
 */
void passing_body() {
    CHECK(true);
    CHECK_EQ(1, 1);
    CHECK_THROWS(throw std::runtime_error("x"), std::runtime_error);
}

/**
 * @brief Test body with one false CHECK.
 * @return nothing.
 * @pre none.
 * @post exactly one failure recorded.
 * @throws does not throw.
 */
void failing_check_body() {
    CHECK(1 > 2);
}

/**
 * @brief Test body with a CHECK_EQ mismatch of two ints.
 * @return nothing.
 * @pre none.
 * @post exactly one failure recorded, message contains both values.
 * @throws does not throw.
 */
void failing_eq_body() {
    CHECK_EQ(1, 2);
}

/**
 * @brief Test body whose CHECK_THROWS expression does not throw.
 * @return nothing.
 * @pre none.
 * @post exactly one failure recorded.
 * @throws does not throw.
 */
void no_throw_body() {
    CHECK_THROWS((void)0, std::runtime_error);
}

/**
 * @brief Test body whose expression throws a different exception type.
 * @return nothing.
 * @pre none.
 * @post exactly one failure recorded.
 * @throws does not throw.
 */
void wrong_throw_body() {
    CHECK_THROWS(throw std::logic_error("x"), std::runtime_error);
}

/**
 * @brief Test body that lets an exception escape.
 * @return nothing.
 * @pre none.
 * @post none (never returns normally).
 * @throws std::runtime_error always.
 */
void escaping_body() {
    throw std::runtime_error("boom");
}

/**
 * @brief Test body that has two failing checks.
 * @return nothing.
 * @pre none.
 * @post exactly two failures recorded.
 * @throws does not throw.
 */
void two_failures_body() {
    CHECK(false);
    CHECK_EQ(std::string("a"), std::string("b"));
}

/**
 * @brief Violates a precondition through MSGFMT_EXPECTS.
 * @return nothing.
 * @pre none.
 * @post never returns normally.
 * @throws msgfmt::ContractViolation always.
 */
void violate() {
    MSGFMT_EXPECTS(false);
}

}  // namespace

// covers: run_case
TEST(run_case_passing_body_has_no_failures) {
    std::ostringstream sink;
    CHECK_EQ(tf::run_case(passing_body, sink), 0);
    CHECK_EQ(sink.str(), std::string());
}

// covers: run_case, check
TEST(run_case_detects_false_check) {
    std::ostringstream sink;
    CHECK_EQ(tf::run_case(failing_check_body, sink), 1);
    CHECK(sink.str().find("CHECK(1 > 2) failed") != std::string::npos);
}

// covers: run_case, check_eq, show
TEST(run_case_detects_eq_mismatch_and_prints_values) {
    std::ostringstream sink;
    CHECK_EQ(tf::run_case(failing_eq_body, sink), 1);
    CHECK(sink.str().find("1 != 2") != std::string::npos);
}

// covers: run_case
TEST(run_case_detects_missing_and_wrong_exceptions) {
    std::ostringstream sink;
    CHECK_EQ(tf::run_case(no_throw_body, sink), 1);
    CHECK(sink.str().find("did not throw") != std::string::npos);
    std::ostringstream sink2;
    CHECK_EQ(tf::run_case(wrong_throw_body, sink2), 1);
    CHECK(sink2.str().find("threw another type") != std::string::npos);
}

// covers: run_case
TEST(run_case_counts_escaped_exception_and_multiple_failures) {
    std::ostringstream sink;
    CHECK_EQ(tf::run_case(escaping_body, sink), 1);
    CHECK(sink.str().find("boom") != std::string::npos);
    CHECK_EQ(tf::run_case(two_failures_body, sink), 2);
}

// covers: run_case, report
TEST(run_case_restores_outer_counter_and_sink) {
    std::ostringstream sink;
    tf::run_case(failing_check_body, sink);
    CHECK_EQ(tf::detail::failure_counter(), 0);
    CHECK(tf::detail::output_slot() == &std::cerr);
}

// covers: run_tests, matches
TEST(run_tests_reports_failure_and_filters) {
    const std::vector<tf::TestCase> cases = {{"good_one", passing_body}, {"bad_one", failing_check_body}};
    std::ostringstream log;
    CHECK_EQ(tf::run_tests(cases, "", log), 1);
    CHECK(log.str().find("[FAIL] bad_one") != std::string::npos);
    CHECK(log.str().find("[ OK ] good_one") != std::string::npos);
    std::ostringstream only_good;
    CHECK_EQ(tf::run_tests(cases, "good", only_good), 0);
    CHECK(only_good.str().find("bad_one") == std::string::npos);
}

// covers: run_tests
TEST(run_tests_fails_when_no_test_matches) {
    const std::vector<tf::TestCase> cases = {{"good_one", passing_body}};
    std::ostringstream log;
    CHECK_EQ(tf::run_tests(cases, "nothing", log), 1);
    CHECK(log.str().find("no tests matched") != std::string::npos);
    CHECK_EQ(tf::run_tests({}, "", log), 1);
}

// covers: matches
TEST(matches_is_substring_search) {
    CHECK(tf::detail::matches("utf8_roundtrip", "round"));
    CHECK(tf::detail::matches("abc", ""));
    CHECK(!tf::detail::matches("abc", "abcd"));
    CHECK(!tf::detail::matches("abc", "x"));
}

// covers: run_all, registry
TEST(run_all_with_unmatched_filter_returns_failure) {
    char program[] = "prog";
    char filter[] = "no_such_test_name_xyz";
    char* argv[] = {program, filter};
    std::ostringstream sink;
    std::ostream* previous = tf::detail::output_slot();
    std::streambuf* old_buf = std::cerr.rdbuf(sink.rdbuf());
    const int code = tf::run_all(2, argv);
    std::cerr.rdbuf(old_buf);
    CHECK(tf::detail::output_slot() == previous);
    CHECK_EQ(code, 1);
    CHECK(sink.str().find("no tests matched") != std::string::npos);
}

// covers: Registrar, registry
TEST(registrar_adds_test_to_registry) {
    const std::size_t before = tf::detail::registry().size();
    tf::Registrar extra("extra_test_for_registry", passing_body);
    CHECK_EQ(tf::detail::registry().size(), before + 1);
    tf::detail::registry().pop_back();
    CHECK_EQ(tf::detail::registry().size(), before);
}

// covers: output_slot, failure_counter
TEST(output_slot_defaults_to_cerr_and_counter_is_accessible) {
    CHECK(tf::detail::output_slot() != nullptr);
    const int before = tf::detail::failure_counter();
    CHECK_EQ(before, 0);
}

// covers: show, show_cp
TEST(show_formats_values) {
    CHECK_EQ(tf::detail::show(42), std::string("42"));
    CHECK_EQ(tf::detail::show(std::string("s")), std::string("s"));
    CHECK_EQ(tf::detail::show(char32_t{0x41}), std::string("U+0041"));
    CHECK_EQ(tf::detail::show(char32_t{0x1F680}), std::string("U+1F680"));
    CHECK_EQ(tf::detail::show(std::u32string{0x41, 0x42}), std::string("[U+0041 U+0042]"));
    CHECK_EQ(tf::detail::show(std::u32string{}), std::string("[]"));
    CHECK_EQ(tf::detail::show(std::optional<int>{}), std::string("nullopt"));
    CHECK_EQ(tf::detail::show(std::optional<int>{7}), std::string("7"));
    CHECK_EQ(tf::detail::show(std::optional<std::u32string>{std::u32string{0x41}}), std::string("[U+0041]"));
    struct Opaque {};
    CHECK_EQ(tf::detail::show(Opaque{}), std::string("<unprintable>"));
}

// covers: ContractViolation, contract_failed
TEST(contract_macro_throws_with_condition_text_even_in_release) {
    bool thrown = false;
    try {
        MSGFMT_EXPECTS(1 + 1 == 3);
    } catch (const msgfmt::ContractViolation& e) {
        thrown = true;
        const std::string what = e.what();
        CHECK(what.find("1 + 1 == 3") != std::string::npos);
        CHECK(what.find("test_framework_selftest.cpp") != std::string::npos);
    }
    CHECK(thrown);
    MSGFMT_EXPECTS(1 + 1 == 2);
    CHECK_THROWS(violate(), std::logic_error);
    CHECK_THROWS(msgfmt::detail::contract_failed("c", "f", 1), msgfmt::ContractViolation);
}
