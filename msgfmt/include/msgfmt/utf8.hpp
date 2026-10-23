#pragma once

#include <cstddef>
#include <optional>
#include <string>
#include <string_view>

namespace msgfmt {

/**
 * @brief Tells whether a value is a Unicode scalar value (valid code point).
 * @param cp value to test; any char32_t.
 * @return true iff cp <= U+10FFFF and cp is not a surrogate (U+D800..U+DFFF).
 * @pre none.
 * @post none.
 * @throws does not throw.
 */
bool is_scalar_value(char32_t cp) noexcept;

/**
 * @brief Strictly decodes UTF-8 into code points.
 *
 * Accepts NUL, U+FEFF (kept as a character) and all control characters; performs no
 * normalisation. Rejects stray continuation bytes, bytes C0, C1, F5..FF, overlong forms,
 * surrogates, values above U+10FFFF and truncated or malformed continuations.
 *
 * @param bytes input bytes; not required to be NUL-terminated; only read during the call.
 * @return the decoded code points; std::nullopt if the input is not valid UTF-8
 *         (an empty input yields an empty string, not nullopt).
 * @pre none.
 * @post on success every element is a scalar value and re-encoding gives back `bytes`.
 * @throws std::bad_alloc on allocation failure.
 */
std::optional<std::u32string> decode_utf8(std::string_view bytes);

/**
 * @brief Appends the UTF-8 encoding of one code point to a string.
 * @param out string to extend; existing content is preserved.
 * @param cp code point to encode; must satisfy is_scalar_value.
 * @pre is_scalar_value(cp) (checked with MSGFMT_EXPECTS).
 * @post out grew by 1..4 bytes forming a well-formed UTF-8 sequence for cp.
 * @throws ContractViolation if the precondition is violated (out is left unchanged).
 * @throws std::bad_alloc on allocation failure.
 */
void encode_utf8_append(std::string& out, char32_t cp);

/**
 * @brief Counts the UTF-16 code units needed for one code point.
 * @param cp code point; must satisfy is_scalar_value.
 * @return 1 for U+0000..U+FFFF, 2 for U+10000..U+10FFFF.
 * @pre is_scalar_value(cp) (checked with MSGFMT_EXPECTS).
 * @post result is 1 or 2.
 * @throws ContractViolation if the precondition is violated.
 */
std::size_t utf16_units(char32_t cp);

/**
 * @brief Counts the UTF-16 code units of a whole UTF-32 string.
 * @param s code points; every element must satisfy is_scalar_value; only read during the call.
 * @return sum of utf16_units over all elements (0 for an empty string).
 * @pre every element of s is a scalar value (checked with MSGFMT_EXPECTS).
 * @post result >= s.size() and result <= 2 * s.size().
 * @throws ContractViolation if some element is not a scalar value.
 */
std::size_t utf16_length(std::u32string_view s);

namespace detail {

/**
 * @brief Decodes one UTF-8 sequence starting at a position.
 * @param bytes input bytes; only read during the call.
 * @param pos in: index of the first byte of the sequence; out: index after the sequence
 *            on success, unchanged on failure.
 * @return the code point, or std::nullopt if the bytes at pos are not a well-formed sequence.
 * @pre pos < bytes.size() (checked with MSGFMT_EXPECTS).
 * @post on success pos advanced by 1..4 and the result is a scalar value.
 * @throws ContractViolation if the precondition is violated.
 */
std::optional<char32_t> decode_one(std::string_view bytes, std::size_t& pos);

}  // namespace detail
}  // namespace msgfmt
