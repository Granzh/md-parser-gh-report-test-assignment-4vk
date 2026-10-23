#include "msgfmt/parser.hpp"

#include <algorithm>

#include "msgfmt/charclass.hpp"
#include "msgfmt/contract.hpp"
#include "msgfmt/parser_internal.hpp"
#include "msgfmt/utf8.hpp"

namespace msgfmt {

std::string_view style_name(Style style) {  // NOLINT(contract) documented in parser.hpp
    switch (style) {
        case Style::Bold:
            return "bold";
        case Style::Italic:
            return "italic";
        case Style::Code:
            return "code";
        case Style::Link:
            return "link";
    }
    return "";
}

namespace detail {

bool is_escapable(char32_t cp) noexcept {  // NOLINT(contract) documented in parser_internal.hpp
    return cp == U'*' || cp == U'_' || cp == U'`' || cp == U'[' || cp == U']' || cp == U'(' || cp == U')' ||
           cp == U'\\';
}

void append_utf8_lossy(std::string& out, char32_t cp) {  // NOLINT(contract) documented in parser_internal.hpp
    encode_utf8_append(out, is_scalar_value(cp) ? cp : char32_t{0xFFFD});
}

void append_code_point(Context& ctx, char32_t cp) {  // NOLINT(contract) documented in parser_internal.hpp
    append_utf8_lossy(ctx.text, cp);
    ctx.pos16 += is_scalar_value(cp) ? utf16_units(cp) : 1;
}

std::optional<std::size_t> find_closer(std::u32string_view in, std::size_t content_begin,  // NOLINT(contract)
                                       std::size_t end, Style style) {
    MSGFMT_EXPECTS(content_begin <= end && end <= in.size());
    MSGFMT_EXPECTS(style != Style::Link);
    std::size_t k = content_begin;
    while (k < end) {
        if (style != Style::Code && in[k] == U'\\' && k + 1 < end && is_escapable(in[k + 1])) {
            k += 2;
            continue;
        }
        if (k > content_begin && !is_whitespace(in[k - 1])) {
            bool closes = false;
            if (style == Style::Bold) {
                closes = in[k] == U'*' && k + 1 < end && in[k + 1] == U'*';
            } else if (style == Style::Italic) {
                closes = in[k] == U'_' && (k + 1 >= in.size() || !is_letter_or_digit(in[k + 1]));
            } else {
                closes = in[k] == U'`';
            }
            if (closes) {
                return k;
            }
        }
        ++k;
    }
    return std::nullopt;
}

std::optional<Marker> parse_link(std::u32string_view in, std::size_t open, std::size_t end) {  // NOLINT(contract)
    MSGFMT_EXPECTS(open < end && end <= in.size());
    MSGFMT_EXPECTS(in[open] == U'[');
    const std::size_t text_begin = open + 1;
    std::size_t k = text_begin;
    while (k < end && in[k] != U']') {
        k += (in[k] == U'\\' && k + 1 < end && is_escapable(in[k + 1])) ? 2 : 1;
    }
    if (k >= end || k == text_begin || is_whitespace(in[k - 1])) {
        return std::nullopt;
    }
    const std::size_t text_end = k;
    if (text_end + 1 >= end || in[text_end + 1] != U'(') {
        return std::nullopt;
    }
    const std::size_t url_begin = text_end + 2;
    std::size_t close = url_begin;
    while (close < end && in[close] != U')') {
        if (is_whitespace(in[close])) {
            return std::nullopt;
        }
        ++close;
    }
    if (close >= end || close == url_begin) {
        return std::nullopt;
    }
    std::string url;
    for (std::size_t j = url_begin; j < close; ++j) {
        append_utf8_lossy(url, in[j]);
    }
    return Marker{Style::Link, text_begin, text_end, close + 1, std::move(url)};
}

std::optional<Marker> try_open_marker(std::u32string_view in, std::size_t pos, std::size_t end,  // NOLINT(contract)
                                      StyleSet opened) {
    MSGFMT_EXPECTS(pos < end && end <= in.size());
    const char32_t c = in[pos];
    Style style;
    std::size_t open_len = 1;
    if (c == U'*' && pos + 1 < end && in[pos + 1] == U'*') {
        style = Style::Bold;
        open_len = 2;
    } else if (c == U'_') {
        style = Style::Italic;
        if (pos > 0 && is_letter_or_digit(in[pos - 1])) {
            return std::nullopt;
        }
    } else if (c == U'`') {
        style = Style::Code;
    } else if (c == U'[') {
        style = Style::Link;
    } else {
        return std::nullopt;
    }
    if (has_style(opened, style)) {
        return std::nullopt;
    }
    const std::size_t content_begin = pos + open_len;
    if (content_begin >= end || is_whitespace(in[content_begin])) {
        return std::nullopt;
    }
    if (style == Style::Link) {
        return parse_link(in, pos, end);
    }
    const std::optional<std::size_t> closer = find_closer(in, content_begin, end, style);
    if (!closer) {
        return std::nullopt;
    }
    return Marker{style, content_begin, *closer, *closer + open_len, std::string()};
}

void parse_slice(Context& ctx, std::size_t begin, std::size_t end, StyleSet opened) {  // NOLINT(contract)
    MSGFMT_EXPECTS(begin <= end && end <= ctx.input.size());
    const std::u32string_view in = ctx.input;
    std::size_t i = begin;
    while (i < end) {
        const char32_t c = in[i];
        if (c == U'\\') {
            if (i + 1 < end && is_escapable(in[i + 1])) {
                append_code_point(ctx, in[i + 1]);
                i += 2;
            } else {
                append_code_point(ctx, c);
                ++i;
            }
            continue;
        }
        std::optional<Marker> marker = try_open_marker(in, i, end, opened);
        if (marker) {
            const std::size_t order = ctx.next_order++;
            const std::size_t start = ctx.pos16;
            if (marker->style == Style::Code) {
                for (std::size_t j = marker->content_begin; j < marker->content_end; ++j) {
                    append_code_point(ctx, in[j]);
                }
            } else {
                parse_slice(ctx, marker->content_begin, marker->content_end, opened | style_bit(marker->style));
            }
            const std::size_t length = ctx.pos16 - start;
            if (length > 0) {
                ctx.ranges.push_back({Range{marker->style, start, length, std::move(marker->url)}, order});
            }
            i = marker->next;
            continue;
        }
        const bool star_pair = c == U'*' && i + 1 < end && in[i + 1] == U'*';
        append_code_point(ctx, c);
        if (star_pair) {
            append_code_point(ctx, c);
        }
        i += star_pair ? 2 : 1;
    }
}

void sort_ranges(std::vector<OrderedRange>& ranges) {  // NOLINT(contract) documented in parser_internal.hpp
    std::sort(ranges.begin(), ranges.end(), [](const OrderedRange& a, const OrderedRange& b) {
        if (a.range.offset != b.range.offset) {
            return a.range.offset < b.range.offset;
        }
        if (a.range.length != b.range.length) {
            return a.range.length > b.range.length;
        }
        return a.open_order < b.open_order;
    });
}

}  // namespace detail

ParseResult parse(std::u32string_view input) {  // NOLINT(contract) documented in parser.hpp
    detail::Context ctx;
    ctx.input = input;
    detail::parse_slice(ctx, 0, input.size(), 0);
    detail::sort_ranges(ctx.ranges);
    ParseResult result;
    result.text = std::move(ctx.text);
    result.ranges.reserve(ctx.ranges.size());
    for (detail::OrderedRange& item : ctx.ranges) {
        result.ranges.push_back(std::move(item.range));
    }
    return result;
}

std::optional<ParseResult> parse_utf8(std::string_view bytes) {  // NOLINT(contract) documented in parser.hpp
    const std::optional<std::u32string> decoded = decode_utf8(bytes);
    if (!decoded) {
        return std::nullopt;
    }
    return parse(*decoded);
}

}  // namespace msgfmt
