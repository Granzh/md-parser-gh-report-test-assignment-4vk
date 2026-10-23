#pragma once

// Shared helpers of the parser tests: invariant checker, result printer, expectation macro.

#include <cstddef>
#include <optional>
#include <sstream>
#include <string>
#include <vector>

#include "msgfmt/parser.hpp"
#include "msgfmt/utf8.hpp"
#include "test_framework.hpp"

namespace ptest {

using msgfmt::ParseResult;
using msgfmt::Range;
using msgfmt::Style;

/**
 * @brief Builds a Range value.
 * @param style style of the range.
 * @param offset start in UTF-16 code units.
 * @param length length in UTF-16 code units.
 * @param url link target; empty for non-link styles.
 * @return the range.
 * @pre none.
 * @post fields equal the arguments.
 * @throws std::bad_alloc on allocation failure.
 */
inline Range rg(Style style, std::size_t offset, std::size_t length, std::string url = std::string()) {
    return Range{style, offset, length, std::move(url)};
}

/**
 * @brief Renders a parse result for failure messages.
 * @param result result to print.
 * @return text like `"abc" [bold(0,1) link(1,2,url)]`.
 * @pre none.
 * @post none.
 * @throws std::bad_alloc on allocation failure.
 */
inline std::string describe(const ParseResult& result) {
    std::ostringstream os;
    os << '"' << result.text << "\" [";
    for (std::size_t i = 0; i < result.ranges.size(); ++i) {
        const Range& r = result.ranges[i];
        os << (i ? " " : "") << msgfmt::style_name(r.style) << "(" << r.offset << "," << r.length;
        if (!r.url.empty()) {
            os << "," << r.url;
        }
        os << ")";
    }
    os << "]";
    return os.str();
}

/**
 * @brief Collects every violated parser invariant of a result.
 *
 * Invariants: text is valid UTF-8; every range has length > 0 and lies inside the text; range borders do not
 * split a surrogate pair; url is non-empty exactly for links; ranges are sorted by offset asc, length desc;
 * any two ranges are nested or disjoint (no partial overlap); two ranges of the same style never nest.
 *
 * @param result parse result to examine.
 * @return one message per violation; empty if all invariants hold.
 * @pre none.
 * @post none.
 * @throws std::bad_alloc on allocation failure.
 */
inline std::vector<std::string> find_invariant_violations(const ParseResult& result) {
    std::vector<std::string> problems;
    const std::optional<std::u32string> decoded = msgfmt::decode_utf8(result.text);
    if (!decoded) {
        problems.push_back("text is not valid UTF-8");
        return problems;
    }
    std::vector<bool> boundary;  // boundary[p]: UTF-16 position p is not inside a surrogate pair
    for (const char32_t cp : *decoded) {
        boundary.push_back(true);
        if (cp >= 0x10000) {
            boundary.push_back(false);
        }
    }
    boundary.push_back(true);
    const std::size_t total = boundary.size() - 1;
    for (std::size_t i = 0; i < result.ranges.size(); ++i) {
        const Range& r = result.ranges[i];
        const std::string who = "range #" + std::to_string(i) + ": ";
        if (r.length == 0) {
            problems.push_back(who + "length is 0");
        }
        if (r.offset + r.length > total) {
            problems.push_back(who + "exceeds text length " + std::to_string(total));
        } else if (!boundary[r.offset] || !boundary[r.offset + r.length]) {
            problems.push_back(who + "border inside a surrogate pair");
        }
        if ((r.style == Style::Link) == r.url.empty()) {
            problems.push_back(who + "url must be non-empty exactly for link");
        }
        if (i > 0) {
            const Range& p = result.ranges[i - 1];
            if (p.offset > r.offset || (p.offset == r.offset && p.length < r.length)) {
                problems.push_back(who + "violates rule 12 order");
            }
        }
        for (std::size_t j = 0; j < i; ++j) {
            const Range& q = result.ranges[j];
            const bool disjoint = q.offset + q.length <= r.offset || r.offset + r.length <= q.offset;
            const bool q_in_r = r.offset <= q.offset && q.offset + q.length <= r.offset + r.length;
            const bool r_in_q = q.offset <= r.offset && r.offset + r.length <= q.offset + q.length;
            if (!disjoint && !q_in_r && !r_in_q) {
                problems.push_back(who + "partially overlaps range #" + std::to_string(j));
            }
            if (!disjoint && q.style == r.style) {
                problems.push_back(who + "same style nested/overlapping with range #" + std::to_string(j));
            }
        }
    }
    return problems;
}

/**
 * @brief Reports every violated invariant of a result as a test failure.
 * @param result parse result to examine.
 * @param file source file of the caller; non-null.
 * @param line source line of the caller.
 * @return true iff no invariant is violated.
 * @pre file is not null.
 * @post one failure is recorded per violated invariant.
 * @throws std::bad_alloc on allocation failure.
 */
inline bool check_invariants(const ParseResult& result, const char* file, int line) {
    const std::vector<std::string> problems = find_invariant_violations(result);
    for (const std::string& problem : problems) {
        tf::detail::report(file, line, "invariant violated: " + problem + " in " + describe(result));
    }
    return problems.empty();
}

/**
 * @brief Parses UTF-8 input, checks invariants and compares with the expectation.
 * @param file source file of the caller; non-null.
 * @param line source line of the caller.
 * @param input input bytes (must be valid UTF-8).
 * @param text expected plain text.
 * @param ranges expected ranges in rule 12 order.
 * @return true iff parsing succeeded, invariants hold and the result equals the expectation.
 * @pre file is not null.
 * @post failures are recorded for any mismatch.
 * @throws std::bad_alloc on allocation failure.
 */
inline bool expect_parse(const char* file, int line, const std::string& input, const std::string& text,
                         const std::vector<Range>& ranges) {
    const std::optional<ParseResult> got = msgfmt::parse_utf8(input);
    if (!got) {
        tf::detail::report(file, line, "parse_utf8 rejected input \"" + input + "\"");
        return false;
    }
    const bool invariants_ok = check_invariants(*got, file, line);
    const ParseResult want{text, ranges};
    if (!(*got == want)) {
        tf::detail::report(file, line,
                           "for input \"" + input + "\": got " + describe(*got) + ", expected " + describe(want));
        return false;
    }
    return invariants_ok;
}

}  // namespace ptest

/// Checks that parse_utf8(input) yields exactly (text, ranges) and satisfies all invariants.
#define EXPECT_PARSE(input, text, ...) \
    ::ptest::expect_parse(__FILE__, __LINE__, (input), (text), std::vector<::msgfmt::Range>{__VA_ARGS__})

/// Checks that parse_utf8(input) yields exactly `text` with no ranges and satisfies all invariants.
#define EXPECT_PLAIN(input, text)     ::ptest::expect_parse(__FILE__, __LINE__, (input), (text), std::vector<::msgfmt::Range>{})
