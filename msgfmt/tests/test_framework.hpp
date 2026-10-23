#pragma once

// Minimal self-contained unit-test framework (stdlib only).

#include <cstddef>
#include <exception>
#include <iomanip>
#include <iostream>
#include <optional>
#include <sstream>
#include <string>
#include <type_traits>
#include <utility>
#include <vector>

namespace tf {

/// A registered test: its name and body.
struct TestCase {
    std::string name;
    void (*body)();
};

namespace detail {

/**
 * @brief Gives access to the global list of registered tests.
 * @return mutable reference to the registry (function-local static, valid for the whole run).
 * @pre none.
 * @post the same object is returned on every call.
 * @throws does not throw.
 */
inline std::vector<TestCase>& registry() {
    static std::vector<TestCase> tests;
    return tests;
}

/**
 * @brief Gives access to the stream that failure messages are written to.
 * @return reference to the current sink pointer; never null (defaults to std::cerr).
 * @pre none.
 * @post the pointer stays valid as long as its owner keeps the stream alive.
 * @throws does not throw.
 */
inline std::ostream*& output_slot() {
    static std::ostream* sink = &std::cerr;
    return sink;
}

/**
 * @brief Gives access to the failed-check counter of the currently running test.
 * @return mutable reference to the counter.
 * @pre none.
 * @post none.
 * @throws does not throw.
 */
inline int& failure_counter() {
    static int count = 0;
    return count;
}

/**
 * @brief Records one failed check and prints it to the current sink.
 * @param file source file of the check; non-null.
 * @param line source line of the check.
 * @param message description of the failure.
 * @pre file is not null.
 * @post failure_counter() is incremented by one.
 * @throws std::bad_alloc on stream allocation failure.
 */
inline void report(const char* file, int line, const std::string& message) {
    ++failure_counter();
    *output_slot() << file << ":" << line << ": " << message << "\n";
}

template <typename T, typename = void>
struct is_streamable : std::false_type {};
template <typename T>
struct is_streamable<T, std::void_t<decltype(std::declval<std::ostream&>() << std::declval<const T&>())>>
    : std::true_type {};

/**
 * @brief Formats a code point as "U+XXXX".
 * @param cp code point; any value.
 * @return text of the form U+0041 (at least four hex digits, upper case).
 * @pre none.
 * @post result starts with "U+".
 * @throws std::bad_alloc on allocation failure.
 */
inline std::string show_cp(char32_t cp) {
    std::ostringstream os;
    os << "U+" << std::uppercase << std::hex << std::setw(4) << std::setfill('0')
       << static_cast<unsigned long>(cp);
    return os.str();
}

/**
 * @brief Formats a char32_t value for failure messages.
 * @param value code point.
 * @return same as show_cp.
 * @pre none.
 * @post none.
 * @throws std::bad_alloc on allocation failure.
 */
inline std::string show(char32_t value) {
    return show_cp(value);
}

/**
 * @brief Formats a UTF-32 string for failure messages.
 * @param value string to show.
 * @return list of code points in brackets, e.g. "[U+0041 U+0042]".
 * @pre none.
 * @post result starts with '[' and ends with ']'.
 * @throws std::bad_alloc on allocation failure.
 */
inline std::string show(const std::u32string& value) {
    std::string out = "[";
    for (std::size_t i = 0; i < value.size(); ++i) {
        out += (i ? " " : "") + show_cp(value[i]);
    }
    return out + "]";
}

/**
 * @brief Formats an optional value for failure messages.
 * @param value optional to show.
 * @return "nullopt" when empty, otherwise the formatted contained value.
 * @pre none.
 * @post none.
 * @throws std::bad_alloc on allocation failure.
 */
template <typename T>
std::string show(const std::optional<T>& value);

/**
 * @brief Formats any value for failure messages.
 * @param value value to show; streamable types use operator<<, others print "<unprintable>".
 * @return textual representation.
 * @pre none.
 * @post none.
 * @throws std::bad_alloc on allocation failure.
 */
template <typename T>
std::string show(const T& value) {
    if constexpr (is_streamable<T>::value) {
        std::ostringstream os;
        os << value;
        return os.str();
    } else {
        return "<unprintable>";
    }
}

template <typename T>
std::string show(const std::optional<T>& value) {  // NOLINT(contract)
    return value ? show(*value) : std::string("nullopt");
}

/**
 * @brief Implements CHECK: reports a failure when the condition is false.
 * @param ok value of the checked condition.
 * @param expr source text of the condition.
 * @param file source file; non-null.
 * @param line source line.
 * @return ok, unchanged.
 * @pre file is not null.
 * @post failure_counter() grew by one iff ok is false.
 * @throws std::bad_alloc on allocation failure.
 */
inline bool check(bool ok, const char* expr, const char* file, int line) {
    if (!ok) {
        report(file, line, std::string("CHECK(") + expr + ") failed");
    }
    return ok;
}

/**
 * @brief Implements CHECK_EQ: compares two values with == and prints both on mismatch.
 * @param a left value.
 * @param b right value; a == b must compile.
 * @param expr_a source text of the left expression.
 * @param expr_b source text of the right expression.
 * @param file source file; non-null.
 * @param line source line.
 * @return true iff a == b.
 * @pre file is not null.
 * @post failure_counter() grew by one iff the values differ.
 * @throws std::bad_alloc on allocation failure.
 */
template <typename A, typename B>
bool check_eq(const A& a, const B& b, const char* expr_a, const char* expr_b, const char* file, int line) {
    if (a == b) {
        return true;
    }
    report(file, line,
           std::string("CHECK_EQ(") + expr_a + ", " + expr_b + ") failed: " + show(a) + " != " + show(b));
    return false;
}

/**
 * @brief Tells whether a test name is selected by a filter.
 * @param name test name.
 * @param filter substring to look for; empty selects everything.
 * @return true iff name contains filter.
 * @pre none.
 * @post none.
 * @throws does not throw.
 */
inline bool matches(const std::string& name, const std::string& filter) {
    return name.find(filter) != std::string::npos;
}

}  // namespace detail

/// Registers a test at static-initialisation time.
struct Registrar {
    /**
     * @brief Adds a test to the global registry.
     * @param name test name; copied.
     * @param body test function; non-null.
     * @pre body is not null.
     * @post the test is listed in the registry.
     * @throws std::bad_alloc on allocation failure.
     */
    Registrar(const char* name, void (*body)()) { detail::registry().push_back({name, body}); }
};

/**
 * @brief Runs one test body and counts its failed checks.
 * @param body test function; non-null.
 * @param sink stream receiving failure messages; must outlive the call.
 * @return number of failed checks; an escaped exception counts as one failure.
 * @pre body is not null.
 * @post the sink and the failure counter of the caller are restored.
 * @throws does not throw (exceptions from body are caught and reported).
 */
inline int run_case(void (*body)(), std::ostream& sink) {
    std::ostream* previous_sink = detail::output_slot();
    const int previous_count = detail::failure_counter();
    detail::output_slot() = &sink;
    detail::failure_counter() = 0;
    try {
        body();
    } catch (const std::exception& e) {
        detail::report("<test>", 0, std::string("unexpected exception: ") + e.what());
    } catch (...) {
        detail::report("<test>", 0, "unexpected non-standard exception");
    }
    const int result = detail::failure_counter();
    detail::output_slot() = previous_sink;
    detail::failure_counter() = previous_count;
    return result;
}

/**
 * @brief Runs the tests whose names match a filter and prints a summary.
 * @param tests tests to consider.
 * @param filter substring of test names; empty runs all.
 * @param log stream for progress lines and failure messages.
 * @return 0 if at least one test ran and all passed; 1 if any failed or none matched.
 * @pre none.
 * @post every selected test was executed exactly once.
 * @throws std::bad_alloc on allocation failure.
 */
inline int run_tests(const std::vector<TestCase>& tests, const std::string& filter, std::ostream& log) {
    int ran = 0;
    int failed = 0;
    for (const TestCase& test : tests) {
        if (!detail::matches(test.name, filter)) {
            continue;
        }
        ++ran;
        const int failures = run_case(test.body, log);
        log << (failures == 0 ? "[ OK ] " : "[FAIL] ") << test.name << "\n";
        failed += failures != 0 ? 1 : 0;
    }
    log << ran << " test(s) run, " << failed << " failed\n";
    if (ran == 0) {
        log << "no tests matched filter '" << filter << "'\n";
        return 1;
    }
    return failed == 0 ? 0 : 1;
}

/**
 * @brief Runs all registered tests, optionally filtered by argv[1].
 * @param argc number of arguments.
 * @param argv arguments; argv[1], if present, is a substring filter.
 * @return process exit code: 0 on success, 1 on any failure or empty selection.
 * @pre argv has argc valid entries.
 * @post summary printed to stderr.
 * @throws std::bad_alloc on allocation failure.
 */
inline int run_all(int argc, char** argv) {
    const std::string filter = argc > 1 ? argv[1] : "";
    return run_tests(detail::registry(), filter, std::cerr);
}

}  // namespace tf

/// Declares and registers a test case: TEST(name) { ... }
#define TEST(name)                                           \
    static void name();                                      \
    static ::tf::Registrar tf_registrar_##name(#name, name); \
    static void name()

/// Non-fatal check that a condition is true.
#define CHECK(cond) ::tf::detail::check(static_cast<bool>(cond), #cond, __FILE__, __LINE__)

/// Non-fatal check that two values are equal; prints both on failure.
#define CHECK_EQ(a, b) ::tf::detail::check_eq((a), (b), #a, #b, __FILE__, __LINE__)

/// Non-fatal check that evaluating expr throws an exception of the given type.
#define CHECK_THROWS(expr, ExceptionType)                                                           \
    do {                                                                                            \
        bool tf_ok_ = false;                                                                        \
        try {                                                                                       \
            (void)(expr);                                                                           \
        } catch (const ExceptionType&) {                                                            \
            tf_ok_ = true;                                                                          \
        } catch (...) {                                                                             \
            tf_ok_ = true;                                                                          \
            ::tf::detail::report(__FILE__, __LINE__,                                                \
                                 "CHECK_THROWS(" #expr ", " #ExceptionType ") threw another type"); \
        }                                                                                           \
        if (!tf_ok_) {                                                                              \
            ::tf::detail::report(__FILE__, __LINE__,                                                \
                                 "CHECK_THROWS(" #expr ", " #ExceptionType ") did not throw");      \
        }                                                                                           \
    } while (false)
