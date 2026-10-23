#pragma once

// Internal building blocks of the parser, exposed in namespace detail so that tests can call them directly.

#include <cstddef>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "msgfmt/parser.hpp"

namespace msgfmt {
namespace detail {

/// Bit set of styles that are already open around the current position.
using StyleSet = unsigned;

/// A range together with the order in which its opening marker appeared in the input.
struct OrderedRange {
    Range range;
    std::size_t open_order;
};

/// A recognised, well-formed construct: where its content lies and where parsing resumes.
struct Marker {
    Style style;
    std::size_t content_begin;  ///< index of the first content code point
    std::size_t content_end;    ///< index one past the last content code point (start of the closing marker)
    std::size_t next;           ///< index right after the whole construct
    std::string url;            ///< only for Style::Link
};

/// Mutable state of one parse() call.
struct Context {
    std::u32string_view input;
    std::string text;
    std::size_t pos16 = 0;       ///< UTF-16 length of `text` so far
    std::size_t next_order = 0;  ///< opening order given to the next construct
    std::vector<OrderedRange> ranges;
};

/**
 * @brief Gives the bit of one style in a StyleSet.
 * @param style any enumerator of Style.
 * @return a value with exactly one bit set.
 * @pre none.
 * @post result != 0.
 * @throws does not throw.
 */
inline StyleSet style_bit(Style style) {
    return 1u << static_cast<unsigned>(style);
}

/**
 * @brief Tells whether a style is in a set.
 * @param set set of open styles.
 * @param style style to look for.
 * @return true iff the style's bit is set.
 * @pre none.
 * @post none.
 * @throws does not throw.
 */
inline bool has_style(StyleSet set, Style style) {
    return (set & style_bit(style)) != 0;
}

/**
 * @brief Tells whether a backslash before this code point turns it into an ordinary character (rule 9).
 * @param cp any code point.
 * @return true iff cp is one of * _ ` [ ] ( ) \ .
 * @pre none.
 * @post none.
 * @throws does not throw.
 */
bool is_escapable(char32_t cp) noexcept;

/**
 * @brief Appends a code point to a UTF-8 string, replacing non-scalar values by U+FFFD.
 * @param out string to extend.
 * @param cp any char32_t value.
 * @return nothing.
 * @pre none.
 * @post out grew by the UTF-8 encoding of cp (or of U+FFFD if cp is not a scalar value).
 * @throws std::bad_alloc on allocation failure.
 */
void append_utf8_lossy(std::string& out, char32_t cp);

/**
 * @brief Emits one code point into the plain text and advances the UTF-16 position.
 * @param ctx parse state to update.
 * @param cp any char32_t value (non-scalar values become U+FFFD).
 * @return nothing.
 * @pre none.
 * @post ctx.text grew by the encoding of cp; ctx.pos16 grew by 1 or 2.
 * @throws std::bad_alloc on allocation failure.
 */
void append_code_point(Context& ctx, char32_t cp);

/**
 * @brief Finds the closing marker of a bold, italic or code construct (rules 3, 5, 7).
 *
 * Scans [content_begin, end) for the nearest position k > content_begin (content is non-empty) where the
 * closing marker starts, the preceding code point is not whitespace and, for italic, the following code
 * point is not a letter or digit. Backslash-escaped code points are skipped except for code, where escaping
 * does not act.
 *
 * @param in whole input; only read.
 * @param content_begin index of the first content code point.
 * @param end exclusive upper bound of the search (the closing marker must fit before it).
 * @param style Style::Bold, Style::Italic or Style::Code.
 * @return start index of the closing marker, or std::nullopt if there is none.
 * @pre content_begin <= end <= in.size(); style != Style::Link (checked with MSGFMT_EXPECTS).
 * @post if a value k is returned: content_begin < k < end.
 * @throws ContractViolation if the precondition is violated.
 */
std::optional<std::size_t> find_closer(std::u32string_view in, std::size_t content_begin, std::size_t end,
                                       Style style);

/**
 * @brief Recognises a link [text](url) starting at an opening bracket (rule 8).
 *
 * The text ends at the first unescaped `]`; it is non-empty and its last code point is not whitespace.
 * `(` must follow immediately; the url extends to the first `)`, is non-empty, contains no whitespace and is
 * taken verbatim.
 *
 * @param in whole input; only read.
 * @param open index of the `[`.
 * @param end exclusive upper bound; the whole construct must lie before it.
 * @return Marker with Style::Link and the url, or std::nullopt if the construct is not a link.
 * @pre open < end <= in.size() and in[open] == '[' (checked with MSGFMT_EXPECTS).
 * @post on success: content_begin == open + 1 < content_end < next <= end and url is non-empty.
 * @throws ContractViolation if the precondition is violated; std::bad_alloc on allocation failure.
 */
std::optional<Marker> parse_link(std::u32string_view in, std::size_t open, std::size_t end);

/**
 * @brief Tries to read an opening marker at a position and find its closing partner (rules 2-5, 8, 10).
 * @param in whole input; only read.
 * @param pos index of the candidate opening marker.
 * @param end exclusive upper bound of the current slice.
 * @param opened styles already open around pos; a style in this set does not open again (rule 10).
 * @return the recognised construct, or std::nullopt if the code points at pos are ordinary text.
 * @pre pos < end <= in.size() (checked with MSGFMT_EXPECTS).
 * @post on success: pos < content_begin <= content_end < next <= end.
 * @throws ContractViolation if the precondition is violated; std::bad_alloc on allocation failure.
 */
std::optional<Marker> try_open_marker(std::u32string_view in, std::size_t pos, std::size_t end,
                                      StyleSet opened);

/**
 * @brief Parses the slice [begin, end) of the input, appending text and ranges to the context.
 * @param ctx parse state; ctx.input is the whole input.
 * @param begin first index of the slice.
 * @param end exclusive end of the slice.
 * @param opened styles already open around the slice.
 * @return nothing.
 * @pre begin <= end <= ctx.input.size() (checked with MSGFMT_EXPECTS).
 * @post ctx.text/ctx.pos16 extended by the plain text of the slice; every added range lies inside the slice's
 *       output and has length > 0; ctx.next_order increased by the number of constructs opened.
 * @throws ContractViolation if the precondition is violated; std::bad_alloc on allocation failure.
 */
void parse_slice(Context& ctx, std::size_t begin, std::size_t end, StyleSet opened);

/**
 * @brief Sorts ranges per rule 12.
 * @param ranges ranges to sort in place; any order on entry.
 * @return nothing.
 * @pre none.
 * @post sorted by offset ascending, then length descending, then open_order ascending.
 * @throws does not throw.
 */
void sort_ranges(std::vector<OrderedRange>& ranges);

}  // namespace detail
}  // namespace msgfmt
