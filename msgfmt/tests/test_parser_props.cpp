#include <algorithm>
#include <string>
#include <vector>

#include "msgfmt/utf8.hpp"
#include "parser_test_utils.hpp"

/**
 * @brief Calls `visit` for every string of length 0..max_len over an alphabet, in lexicographic order of length.
 * @param alphabet code points to combine; non-empty.
 * @param max_len maximum string length.
 * @param visit callback taking the generated string; must not throw.
 * @return number of strings visited.
 * @pre alphabet is not empty.
 * @post every string over the alphabet with length <= max_len was visited exactly once.
 * @throws whatever `visit` throws.
 */
template <typename Visitor>
static std::size_t for_each_string(const std::u32string& alphabet, std::size_t max_len, Visitor visit) {
    std::size_t count = 0;
    std::u32string current;
    std::vector<std::size_t> digits;
    for (std::size_t len = 0; len <= max_len; ++len) {
        digits.assign(len, 0);
        while (true) {
            current.clear();
            for (const std::size_t d : digits) {
                current.push_back(alphabet[d]);
            }
            visit(current);
            ++count;
            std::size_t pos = 0;
            while (pos < len && ++digits[pos] == alphabet.size()) {
                digits[pos++] = 0;
            }
            if (pos == len) {
                break;
            }
        }
    }
    return count;
}

/**
 * @brief Parses every string over an alphabet and checks no-throw, invariants, determinism and parse_utf8 equality.
 * @param alphabet code points to combine; non-empty, all valid scalar values.
 * @param max_len maximum string length.
 * @return number of strings that violated something (0 means success).
 * @pre alphabet is not empty.
 * @post for the first offending string a failure with its code points is recorded.
 * @throws does not throw.
 */
static std::size_t brute_force(const std::u32string& alphabet, std::size_t max_len) {
    std::size_t bad = 0;
    for_each_string(alphabet, max_len, [&bad](const std::u32string& input) {
        bool ok = true;
        try {
            const msgfmt::ParseResult first = msgfmt::parse(input);
            const msgfmt::ParseResult second = msgfmt::parse(input);
            std::string bytes;
            for (const char32_t cp : input) {
                msgfmt::encode_utf8_append(bytes, cp);
            }
            const auto via_utf8 = msgfmt::parse_utf8(bytes);
            ok = first == second && via_utf8.has_value() && *via_utf8 == first &&
                 ptest::find_invariant_violations(first).empty();
        } catch (...) {
            ok = false;
        }
        if (!ok) {
            if (bad == 0) {
                std::string shown;
                for (const char32_t cp : input) {
                    msgfmt::encode_utf8_append(shown, cp);
                }
                tf::detail::report(__FILE__, __LINE__, "property violated for input \"" + shown + "\"");
            }
            ++bad;
        }
    });
    return bad;
}

// covers: for_each_string
TEST(for_each_string_enumerates_all_strings_once) {
    std::vector<std::u32string> seen;
    const std::size_t n = for_each_string(U"ab", 3, [&seen](const std::u32string& s) { seen.push_back(s); });
    CHECK_EQ(n, std::size_t{1 + 2 + 4 + 8});
    CHECK_EQ(seen.size(), n);
    CHECK_EQ(seen.front(), std::u32string());
    std::vector<std::u32string> sorted = seen;
    std::sort(sorted.begin(), sorted.end());
    CHECK(std::adjacent_find(sorted.begin(), sorted.end()) == sorted.end());
    CHECK_EQ(for_each_string(U"x", 0, [](const std::u32string&) {}), std::size_t{1});
}

// covers: brute_force
TEST(brute_force_reports_nothing_for_a_trivial_alphabet) {
    CHECK_EQ(brute_force(U"ab", 4), std::size_t{0});
}

// covers: parse, parse_utf8
TEST(property_all_strings_over_service_characters_up_to_length_5) {
    CHECK_EQ(brute_force(U"*_`[]()\\a ", 5), std::size_t{0});
}

// covers: parse, parse_utf8
TEST(property_strings_with_emoji_cyrillic_digits_and_combining_marks) {
    const std::u32string alphabet = {U'*', U'_', 0x1F680, 0x439, U'1', U' ', U'[', U']', U'(', 0x306};
    CHECK_EQ(brute_force(alphabet, 4), std::size_t{0});
}

// covers: parse
TEST(property_parse_never_throws_on_non_scalar_values) {
    const std::u32string alphabet = {U'*', U'_', 0xD800, 0x110000, U'a'};
    std::size_t count = 0;
    for_each_string(alphabet, 4, [&count](const std::u32string& input) {
        const msgfmt::ParseResult r = msgfmt::parse(input);
        if (!ptest::find_invariant_violations(r).empty()) {
            ++count;
        }
    });
    CHECK_EQ(count, std::size_t{0});
}
