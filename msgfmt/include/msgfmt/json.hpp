#pragma once

#include <string>
#include <string_view>

#include "msgfmt/parser.hpp"

namespace msgfmt {

/**
 * @brief Escapes bytes for use inside a JSON string literal (without the surrounding quotes).
 *
 * `"` becomes `\"`, `\` becomes `\\`; U+0008, U+000C, U+000A, U+000D, U+0009 become `\b \f \n \r \t`; every other
 * control character U+0000..U+001F becomes `\u00XX` with LOWER-case hex digits. All other bytes (ASCII, DEL
 * U+007F, all non-ASCII UTF-8 including U+2028/U+2029) are copied unchanged; `/` is not escaped.
 *
 * @param utf8 text to escape; expected to be valid UTF-8 (bytes >= 0x80 are copied verbatim, not validated).
 * @return the escaped text; empty for empty input.
 * @pre none.
 * @post result contains no raw byte < 0x20, no unescaped `"` and no lone `\`.
 * @throws std::bad_alloc on allocation failure.
 */
std::string json_escape(std::string_view utf8);

/**
 * @brief Serialises a parse result as compact JSON without a trailing newline.
 *
 * Format: {"text":"...","ranges":[{"style":"bold","offset":8,"length":3,"url":""},...]}. Fixed field order:
 * text, ranges; style, offset, length, url. No spaces, exactly these fields.
 *
 * @param result parse result to serialise; result.text must be valid UTF-8.
 * @return the JSON document as UTF-8.
 * @pre none.
 * @post the output is a syntactically valid JSON object and does not end with a newline.
 * @throws std::bad_alloc on allocation failure.
 */
std::string to_json(const ParseResult& result);

/**
 * @brief Builds the error document {"error": "<code>"} exactly as written in spec 3.6 (one space after the colon, no trailing newline).
 * @param code machine-readable error code, e.g. "invalid_utf8"; JSON-escaped in the output.
 * @return the JSON document.
 * @pre none.
 * @post the output is a syntactically valid JSON object with the single field "error".
 * @throws std::bad_alloc on allocation failure.
 */
std::string error_json(std::string_view code);

}  // namespace msgfmt
