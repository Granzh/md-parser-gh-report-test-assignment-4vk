#include <cstddef>
#include <vector>

#include "msgfmt/charclass.hpp"
#include "test_framework.hpp"

using msgfmt::detail::CodeRange;

/**
 * @brief Reference (linear) membership test used to cross-check the binary search.
 * @param table interval table; only read.
 * @param cp code point to look for.
 * @return true iff some interval contains cp.
 * @pre none.
 * @post none.
 * @throws does not throw.
 */
static bool naive_contains(const std::vector<CodeRange>& table, char32_t cp) {
    for (const CodeRange& r : table) {
        if (cp >= r.first && cp <= r.last) {
            return true;
        }
    }
    return false;
}

// covers: is_whitespace
TEST(whitespace_exact_set_over_all_bmp_and_beyond) {
    const std::vector<char32_t> expected = {0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x20, 0x85, 0xA0, 0x1680,
                                            0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006,
                                            0x2007, 0x2008, 0x2009, 0x200A, 0x2028, 0x2029, 0x202F,
                                            0x205F, 0x3000};
    std::size_t count = 0;
    for (char32_t cp = 0; cp <= 0x10FFFF; ++cp) {
        bool want = false;
        for (const char32_t e : expected) {
            want = want || e == cp;
        }
        count += want ? 1 : 0;
        if (msgfmt::is_whitespace(cp) != want) {
            CHECK_EQ(cp, char32_t{0});  // prints the offending code point
        }
    }
    CHECK_EQ(count, expected.size());
}

// covers: is_whitespace
TEST(whitespace_neighbours_and_lookalikes) {
    CHECK(!msgfmt::is_whitespace(0x08));
    CHECK(!msgfmt::is_whitespace(0x0E));
    CHECK(!msgfmt::is_whitespace(0x1F));
    CHECK(!msgfmt::is_whitespace(0x21));
    CHECK(!msgfmt::is_whitespace(0x84));
    CHECK(!msgfmt::is_whitespace(0x86));
    CHECK(!msgfmt::is_whitespace(0x180E));
    CHECK(!msgfmt::is_whitespace(0x200B));
    CHECK(!msgfmt::is_whitespace(0x2027));
    CHECK(!msgfmt::is_whitespace(0x202A));
    CHECK(!msgfmt::is_whitespace(0x2060));
    CHECK(!msgfmt::is_whitespace(0x3001));
    CHECK(!msgfmt::is_whitespace(0xFEFF));
    CHECK(!msgfmt::is_whitespace(0x110000));
}

// covers: is_letter_or_digit
TEST(letter_digit_all_128_ascii) {
    for (char32_t cp = 0; cp < 128; ++cp) {
        const bool want = (cp >= '0' && cp <= '9') || (cp >= 'A' && cp <= 'Z') || (cp >= 'a' && cp <= 'z');
        if (msgfmt::is_letter_or_digit(cp) != want) {
            CHECK_EQ(cp, char32_t{0});  // prints the offending code point
        }
    }
    CHECK(!msgfmt::is_letter_or_digit(U'_'));
    CHECK(!msgfmt::is_letter_or_digit(U'*'));
    CHECK(!msgfmt::is_letter_or_digit(U'`'));
    CHECK(!msgfmt::is_letter_or_digit(U'\\'));
    CHECK(!msgfmt::is_letter_or_digit(U'['));
    CHECK(!msgfmt::is_letter_or_digit(U' '));
    CHECK(!msgfmt::is_letter_or_digit(U'\0'));
}

// covers: is_letter_or_digit
TEST(letter_digit_representatives_per_script) {
    const char32_t yes[] = {
        0xE9,    0xDF,   0xFF,   0x100,  0x17F,  0x1E9E, 0x1EA0,         // Latin-1, Ext-A/B, Additional
        0xAA,    0xB5,   0xBA,                                           // ordinal indicators, micro sign
        0x3B1,   0x3A9,  0x386,  0x1F00, 0x1FFC,                         // Greek
        0x430,   0x439,  0x416,  0x451,  0x4FF,  0x52F,                  // Cyrillic
        0x531,   0x561,                                                  // Armenian
        0x5D0,   0x5EA,                                                  // Hebrew
        0x627,   0x649,  0x663,  0x6F3,                                  // Arabic letters and digits
        0x905,   0x939,  0x966,  0x96F,                                  // Devanagari
        0x985,   0x9E6,  0xA05,  0xA85,  0xB05,  0xB85,  0xC05,  0xC85,  0xD05,  // Indic scripts
        0xE01,   0xE30,  0xE50,                                          // Thai
        0x10D0,  0x1100,                                                 // Georgian, Jamo
        0xAC00,  0xD7A3, 0x3131,                                         // Hangul
        0x3042,  0x30A2, 0x30FC,                                         // Hiragana, Katakana
        0x4E2D,  0x3400, 0x9FFF, 0xF900, 0x20000, 0x2A6DF, 0x30000,      // CJK
        0xFF11,  0xFF21, 0xFF41, 0xFF76,                                 // fullwidth / halfwidth
    };
    for (const char32_t cp : yes) {
        if (!msgfmt::is_letter_or_digit(cp)) {
            CHECK_EQ(cp, char32_t{0});  // prints the offending code point
        }
    }
}

// covers: is_letter_or_digit
TEST(letter_digit_negative_examples_emoji_punctuation_symbols) {
    const char32_t no[] = {
        0x1F680, 0x1F600, 0x2764, 0x2605,                  // emoji, symbols
        0x21, 0x2C, 0x2E, 0x3A, 0x2D, 0x5F, 0x2A,          // ASCII punctuation
        0xA1, 0xA9, 0xAB, 0xB7, 0xBB, 0xBF, 0xD7, 0xF7,    // Latin-1 punctuation and signs
        0x2013, 0x2014, 0x2019, 0x201C, 0x2026,            // general punctuation
        0x300, 0x301, 0x306, 0x36F,                        // combining marks
        0x37E, 0x387, 0x589, 0x5BE, 0x60C, 0x61F, 0x964,   // script punctuation
        0x93C, 0x93E, 0xE31, 0xE34,                        // vowel signs and marks
        0x3000, 0x3001, 0x3002, 0x30FB, 0xFF0C, 0xFF01,    // CJK / fullwidth punctuation
        0x200B, 0xFEFF, 0xE000, 0xD7A4, 0x110000, 0xFFFFFFFF,
    };
    for (const char32_t cp : no) {
        if (msgfmt::is_letter_or_digit(cp)) {
            CHECK_EQ(cp, char32_t{0});  // prints the offending code point
        }
    }
}

// covers: is_letter_or_digit, in_ranges
TEST(letter_digit_both_sides_of_every_table_boundary) {
    const std::vector<CodeRange>& table = msgfmt::detail::letter_digit_table();
    for (const CodeRange& r : table) {
        CHECK(msgfmt::is_letter_or_digit(r.first));
        CHECK(msgfmt::is_letter_or_digit(r.last));
        CHECK(msgfmt::is_letter_or_digit(r.first + (r.last - r.first) / 2));
        CHECK(!msgfmt::is_letter_or_digit(r.first - 1));
        CHECK(!msgfmt::is_letter_or_digit(r.last + 1));
    }
}

// covers: is_letter_or_digit, in_ranges
TEST(letter_digit_matches_naive_scan_for_all_code_points) {
    const std::vector<CodeRange>& table = msgfmt::detail::letter_digit_table();
    std::size_t mismatches = 0;
    for (char32_t cp = 0; cp <= 0x10FFFF; ++cp) {
        if (msgfmt::is_letter_or_digit(cp) != naive_contains(table, cp)) {
            ++mismatches;
        }
    }
    CHECK_EQ(mismatches, std::size_t{0});
}

// covers: letter_digit_table
TEST(table_is_sorted_disjoint_non_adjacent_and_valid) {
    const std::vector<CodeRange>& table = msgfmt::detail::letter_digit_table();
    CHECK(!table.empty());
    for (std::size_t i = 0; i < table.size(); ++i) {
        CHECK(table[i].first <= table[i].last);
        CHECK(table[i].last <= 0x10FFFF);
        CHECK(table[i].last < 0xD800 || table[i].first > 0xDFFF);
        if (i > 0) {
            CHECK(table[i - 1].last + 1 < table[i].first);
        }
    }
    CHECK_EQ(&msgfmt::detail::letter_digit_table(), &table);
}

// covers: in_ranges
TEST(in_ranges_on_small_tables) {
    const std::vector<CodeRange> empty;
    CHECK(!msgfmt::detail::in_ranges(empty, 0));
    const std::vector<CodeRange> table = {{10, 20}, {30, 30}, {40, 50}};
    CHECK(!msgfmt::detail::in_ranges(table, 9));
    CHECK(msgfmt::detail::in_ranges(table, 10));
    CHECK(msgfmt::detail::in_ranges(table, 20));
    CHECK(!msgfmt::detail::in_ranges(table, 21));
    CHECK(!msgfmt::detail::in_ranges(table, 29));
    CHECK(msgfmt::detail::in_ranges(table, 30));
    CHECK(!msgfmt::detail::in_ranges(table, 31));
    CHECK(msgfmt::detail::in_ranges(table, 50));
    CHECK(!msgfmt::detail::in_ranges(table, 51));
    CHECK(!msgfmt::detail::in_ranges(table, 0xFFFFFFFF));
}
