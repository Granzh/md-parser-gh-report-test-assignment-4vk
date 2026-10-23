#pragma once

#include <optional>
#include <string>
#include <string_view>

namespace msgfmt {

/**
 * @brief Tells whether a code point is a service character of the markup.
 * @param cp any code point.
 * @return true iff cp is one of * _ ` [ ] ( ) \ .
 * @pre none.
 * @post none.
 * @throws does not throw.
 */
bool is_service_char(char32_t cp) noexcept;

/**
 * @brief Escapes text so that parsing the result gives the original text back.
 * @param text code points to escape; only read during the call. Values that are not Unicode scalar values are
 *        copied unchanged (they are not service characters).
 * @return a copy of `text` where every service character is preceded by a backslash; everything else
 *         (including a lone non-service character, emoji, NUL, line breaks) is unchanged; empty for empty input.
 * @pre none.
 * @post result.size() == text.size() + number of service characters in text.
 * @throws std::bad_alloc on allocation failure.
 */
std::u32string escape_text(std::u32string_view text);

/**
 * @brief Escapes UTF-8 text (command `msgfmt escape`).
 * @param bytes input bytes; only read during the call.
 * @return the escaped text as UTF-8 (see escape_text), or std::nullopt iff `bytes` is not valid UTF-8.
 * @pre none.
 * @post a value is returned iff decode_utf8(bytes) has a value.
 * @throws std::bad_alloc on allocation failure.
 */
std::optional<std::string> escape_utf8(std::string_view bytes);

}  // namespace msgfmt
