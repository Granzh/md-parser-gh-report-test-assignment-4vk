#pragma once

#include <cstddef>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace msgfmt {

/// Style of a formatted range.
enum class Style { Bold, Italic, Code, Link };

/// Formatted range; offset and length are measured in UTF-16 code units of the plain text.
struct Range {
    Style style;
    std::size_t offset;
    std::size_t length;
    std::string url;  ///< non-empty only for Style::Link
};

/// Result of parsing: plain text (UTF-8) and ranges sorted by rule 12.
struct ParseResult {
    std::string text;
    std::vector<Range> ranges;
};

/**
 * @brief Compares two ranges field by field.
 * @param a first range.
 * @param b second range.
 * @return true iff style, offset, length and url are all equal.
 * @pre none.
 * @post none.
 * @throws does not throw.
 */
inline bool operator==(const Range& a, const Range& b) {
    return a.style == b.style && a.offset == b.offset && a.length == b.length && a.url == b.url;
}

/**
 * @brief Compares two parse results.
 * @param a first result.
 * @param b second result.
 * @return true iff text and the whole range lists are equal (order matters).
 * @pre none.
 * @post none.
 * @throws does not throw.
 */
inline bool operator==(const ParseResult& a, const ParseResult& b) {
    return a.text == b.text && a.ranges == b.ranges;
}

/**
 * @brief Gives the JSON name of a style.
 * @param style any enumerator of Style.
 * @return "bold", "italic", "code" or "link"; points to static storage.
 * @pre style is a valid enumerator.
 * @post result is non-empty.
 * @throws does not throw.
 */
std::string_view style_name(Style style);

/**
 * @brief Parses the Markdown subset of section 3.5 into plain text and style ranges.
 *
 * Total function: every sequence of code points is accepted, constructs that are not
 * well-formed are emitted as ordinary text. Code points that are not Unicode scalar values
 * (surrogates, > U+10FFFF) are replaced by U+FFFD.
 *
 * @param input code points; only read during the call.
 * @return plain text in UTF-8 and ranges sorted by (offset asc, length desc, opening order asc);
 *         offsets/lengths are in UTF-16 code units of the plain text; empty input gives an empty result.
 * @pre none.
 * @post every range has length > 0 and lies inside the text; ranges are pairwise nested or disjoint;
 *       url is non-empty exactly for Style::Link.
 * @throws std::bad_alloc on allocation failure (nothing else).
 */
ParseResult parse(std::u32string_view input);

/**
 * @brief Decodes strict UTF-8 and parses it.
 * @param bytes input bytes; only read during the call.
 * @return the parse result, or std::nullopt iff `bytes` is not valid UTF-8 (see decode_utf8).
 * @pre none.
 * @post a value is returned iff decode_utf8(bytes) has a value, and it equals parse(*decoded).
 * @throws std::bad_alloc on allocation failure.
 */
std::optional<ParseResult> parse_utf8(std::string_view bytes);

}  // namespace msgfmt
