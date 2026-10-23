#pragma once

#include <cstddef>
#include <vector>

namespace msgfmt {

/**
 * @brief Tells whether a code point has the Unicode White_Space property.
 *
 * Exactly: U+0009..U+000D, U+0020, U+0085, U+00A0, U+1680, U+2000..U+200A, U+2028, U+2029,
 * U+202F, U+205F, U+3000. Zero-width characters (U+200B, U+FEFF) are NOT whitespace.
 *
 * @param cp code point; any value (invalid values give false).
 * @return true iff cp is one of the characters listed above.
 * @pre none.
 * @post none.
 * @throws does not throw.
 */
bool is_whitespace(char32_t cp) noexcept;

/**
 * @brief Tells whether a code point is a letter or decimal digit of the supported scripts.
 *
 * Table-driven approximation of Unicode categories L* and Nd (no <locale>, no ICU). Covered:
 * ASCII, Latin-1, Latin Extended-A/B, IPA and Latin Extended Additional, Greek (+ extended),
 * Cyrillic (+ supplement), Armenian, Georgian, Hebrew, Arabic, Devanagari, Bengali, Gurmukhi,
 * Gujarati, Oriya, Tamil, Telugu, Kannada, Malayalam, Thai, Hangul (Jamo, compatibility Jamo,
 * syllables), Hiragana, Katakana, CJK ideographs (BMP and planes 2-3, compatibility block),
 * fullwidth/halfwidth forms, and decimal digits of those scripts.
 *
 * NOT covered (returns false): combining marks and vowel signs (category M, including
 * Indic matras and Thai vowel marks), punctuation, symbols, emoji, `_`, `*`; also scripts
 * absent from the list above (Ethiopic, Khmer, Lao, Tibetan, Myanmar, Sinhala, Mongolian,
 * Cherokee, mathematical alphanumerics, ...) and letter-like symbols such as Roman numerals.
 * Inside covered blocks, letters that Unicode added later may be missing.
 *
 * @param cp code point; any value (invalid values give false).
 * @return true iff cp lies in the letter/digit interval table.
 * @pre none.
 * @post result equals a membership test in detail::letter_digit_table().
 * @throws does not throw.
 */
bool is_letter_or_digit(char32_t cp) noexcept;

namespace detail {

/// Inclusive interval of code points.
struct CodeRange {
    char32_t first;
    char32_t last;
};

/**
 * @brief Exposes the sorted interval table used by is_letter_or_digit.
 * @return reference to a static table sorted ascending, with disjoint and non-adjacent intervals.
 * @pre none.
 * @post the reference stays valid for the whole program run.
 * @throws std::bad_alloc on the first call if the table cannot be allocated.
 */
const std::vector<CodeRange>& letter_digit_table();

/**
 * @brief Binary-searches a code point in a sorted table of disjoint intervals.
 * @param table intervals sorted ascending by first, pairwise disjoint; only read.
 * @param cp code point to look for.
 * @return true iff some interval contains cp.
 * @pre table is sorted and disjoint (not verified).
 * @post none.
 * @throws does not throw.
 */
bool in_ranges(const std::vector<CodeRange>& table, char32_t cp) noexcept;

}  // namespace detail
}  // namespace msgfmt
