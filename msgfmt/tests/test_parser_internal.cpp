#include <string>
#include <vector>

#include "msgfmt/contract.hpp"
#include "msgfmt/parser_internal.hpp"
#include "parser_test_utils.hpp"

using msgfmt::ContractViolation;
using msgfmt::Style;
using namespace msgfmt::detail;
using ptest::rg;

// covers: is_escapable
TEST(is_escapable_exactly_the_eight_service_characters) {
    for (const char32_t cp : std::u32string(U"*_`[]()\\")) {
        CHECK(is_escapable(cp));
    }
    for (const char32_t cp : std::u32string(U"a Z0-.!{}<>~#\n\tй")) {
        CHECK(!is_escapable(cp));
    }
    CHECK(!is_escapable(0));
    CHECK(!is_escapable(0x1F680));
    CHECK(!is_escapable(0xFFFFFFFF));
}

// covers: style_bit, has_style
TEST(style_bits_are_distinct_and_set_membership_works) {
    const Style all[] = {Style::Bold, Style::Italic, Style::Code, Style::Link};
    StyleSet seen = 0;
    for (const Style s : all) {
        CHECK(style_bit(s) != 0);
        CHECK_EQ(seen & style_bit(s), 0u);
        seen |= style_bit(s);
    }
    CHECK(!has_style(0, Style::Bold));
    CHECK(has_style(style_bit(Style::Italic), Style::Italic));
    CHECK(!has_style(style_bit(Style::Italic), Style::Bold));
    CHECK(has_style(seen, Style::Link));
}

// covers: append_utf8_lossy
TEST(append_utf8_lossy_encodes_and_replaces_invalid) {
    std::string s;
    append_utf8_lossy(s, U'a');
    append_utf8_lossy(s, 0x439);
    CHECK_EQ(s, std::string("a\xD0\xB9"));
    std::string bad;
    append_utf8_lossy(bad, 0xD800);
    append_utf8_lossy(bad, 0x110000);
    append_utf8_lossy(bad, 0xFFFFFFFF);
    CHECK_EQ(bad, std::string("\xEF\xBF\xBD\xEF\xBF\xBD\xEF\xBF\xBD"));
}

// covers: append_code_point
TEST(append_code_point_tracks_utf16_position) {
    Context ctx;
    append_code_point(ctx, U'a');
    CHECK_EQ(ctx.pos16, std::size_t{1});
    append_code_point(ctx, 0x1F680);
    CHECK_EQ(ctx.pos16, std::size_t{3});
    append_code_point(ctx, 0xDC00);
    CHECK_EQ(ctx.pos16, std::size_t{4});
    CHECK_EQ(ctx.text, std::string("a\xF0\x9F\x9A\x80\xEF\xBF\xBD"));
}

// covers: find_closer
TEST(find_closer_bold) {
    const std::u32string s = U"**ab**c";
    CHECK_EQ(find_closer(s, 2, s.size(), Style::Bold), std::optional<std::size_t>(4));
    const std::u32string spaced = U"**a **b**";
    CHECK_EQ(find_closer(spaced, 2, spaced.size(), Style::Bold), std::optional<std::size_t>(7));
    const std::u32string none = U"**a*";
    CHECK_EQ(find_closer(none, 2, none.size(), Style::Bold), std::optional<std::size_t>());
}

// covers: find_closer
TEST(find_closer_requires_non_empty_content_and_respects_end) {
    const std::u32string s = U"****";
    CHECK_EQ(find_closer(s, 2, 4, Style::Bold), std::optional<std::size_t>());
    const std::u32string t = U"**a**";
    CHECK_EQ(find_closer(t, 2, 4, Style::Bold), std::optional<std::size_t>());
    CHECK_EQ(find_closer(t, 2, 5, Style::Bold), std::optional<std::size_t>(3));
    CHECK_EQ(find_closer(t, 5, 5, Style::Bold), std::optional<std::size_t>());
}

// covers: find_closer
TEST(find_closer_italic_needs_word_boundary_after_closer) {
    const std::u32string a = U"_a_b_";
    CHECK_EQ(find_closer(a, 1, a.size(), Style::Italic), std::optional<std::size_t>(4));
    const std::u32string b = U"_a_ b";
    CHECK_EQ(find_closer(b, 1, b.size(), Style::Italic), std::optional<std::size_t>(2));
    const std::u32string c = U"_a_я";
    CHECK_EQ(find_closer(c, 1, c.size(), Style::Italic), std::optional<std::size_t>());
    const std::u32string d = U"_a _";
    CHECK_EQ(find_closer(d, 1, d.size(), Style::Italic), std::optional<std::size_t>());
}

// covers: find_closer
TEST(find_closer_escapes_skip_markers_except_in_code) {
    const std::u32string bold = U"**a\\**b**";
    CHECK_EQ(find_closer(bold, 2, bold.size(), Style::Bold), std::optional<std::size_t>(7));
    const std::u32string italic = U"_a\\_b_";
    CHECK_EQ(find_closer(italic, 1, italic.size(), Style::Italic), std::optional<std::size_t>(5));
    const std::u32string code = U"`a\\`b";
    CHECK_EQ(find_closer(code, 1, code.size(), Style::Code), std::optional<std::size_t>(3));
    const std::u32string spaced = U"`a ` b`";
    CHECK_EQ(find_closer(spaced, 1, spaced.size(), Style::Code), std::optional<std::size_t>(6));
}

// covers: find_closer
TEST(find_closer_precondition_violations) {
    const std::u32string s = U"**a**";
    CHECK_THROWS(find_closer(s, 3, 2, Style::Bold), ContractViolation);
    CHECK_THROWS(find_closer(s, 2, 9, Style::Bold), ContractViolation);
    CHECK_THROWS(find_closer(s, 1, 4, Style::Link), ContractViolation);
}

// covers: parse_link
TEST(parse_link_success) {
    const std::u32string s = U"x[ab](http://u)y";
    const auto m = parse_link(s, 1, s.size());
    CHECK(m.has_value());
    if (m) {
        CHECK(m->style == Style::Link);
        CHECK_EQ(m->content_begin, std::size_t{2});
        CHECK_EQ(m->content_end, std::size_t{4});
        CHECK_EQ(m->next, std::size_t{15});
        CHECK_EQ(m->url, std::string("http://u"));
    }
}

// covers: parse_link
TEST(parse_link_failures) {
    const char32_t* const bad[] = {U"[a]", U"[a](", U"[a]()", U"[](u)", U"[a ](u)", U"[a](u v)", U"[a] (u)",
                                   U"[a]](u)", U"[a](u"};
    for (const char32_t* text : bad) {
        const std::u32string s = text;
        CHECK(!parse_link(s, 0, s.size()).has_value());
    }
}

// covers: parse_link
TEST(parse_link_escaped_bracket_in_text_and_bound_by_end) {
    const std::u32string s = U"[a\\]b](u)";
    const auto m = parse_link(s, 0, s.size());
    CHECK(m.has_value() && m->content_end == 5 && m->next == s.size());
    const std::u32string t = U"[a](u)";
    CHECK(!parse_link(t, 0, 5).has_value());
    CHECK(parse_link(t, 0, 6).has_value());
}

// covers: parse_link
TEST(parse_link_precondition_violations) {
    const std::u32string s = U"a[b](u)";
    CHECK_THROWS(parse_link(s, 0, s.size()), ContractViolation);
    CHECK_THROWS(parse_link(s, 7, 7), ContractViolation);
    CHECK_THROWS(parse_link(s, 1, 99), ContractViolation);
}

// covers: try_open_marker
TEST(try_open_marker_recognises_each_style) {
    const std::u32string b = U"**a**";
    const auto mb = try_open_marker(b, 0, b.size(), 0);
    CHECK(mb.has_value() && mb->style == Style::Bold && mb->content_begin == 2 && mb->content_end == 3 &&
          mb->next == 5);
    const std::u32string i = U"_a_";
    const auto mi = try_open_marker(i, 0, i.size(), 0);
    CHECK(mi.has_value() && mi->style == Style::Italic && mi->content_begin == 1 && mi->next == 3);
    const std::u32string c = U"`a`";
    const auto mc = try_open_marker(c, 0, c.size(), 0);
    CHECK(mc.has_value() && mc->style == Style::Code && mc->content_end == 2);
    const std::u32string l = U"[a](u)";
    const auto ml = try_open_marker(l, 0, l.size(), 0);
    CHECK(ml.has_value() && ml->style == Style::Link && ml->url == "u");
}

// covers: try_open_marker
TEST(try_open_marker_rejects_non_markers_and_open_styles) {
    const std::u32string plain = U"abc";
    CHECK(!try_open_marker(plain, 0, 3, 0).has_value());
    const std::u32string star = U"*a*";
    CHECK(!try_open_marker(star, 0, 3, 0).has_value());
    const std::u32string b = U"**a**";
    CHECK(!try_open_marker(b, 0, b.size(), style_bit(Style::Bold)).has_value());
    CHECK(try_open_marker(b, 0, b.size(), style_bit(Style::Italic)).has_value());
    const std::u32string l = U"[a](u)";
    CHECK(!try_open_marker(l, 0, l.size(), style_bit(Style::Link)).has_value());
}

// covers: try_open_marker
TEST(try_open_marker_rule3_word_boundary_and_rule4_space) {
    const std::u32string word = U"a_b_";
    CHECK(!try_open_marker(word, 1, word.size(), 0).has_value());
    const std::u32string cyr = U"я_b_";
    CHECK(!try_open_marker(cyr, 1, cyr.size(), 0).has_value());
    const std::u32string emoji = U"\U0001F680_b_";
    CHECK(try_open_marker(emoji, 1, emoji.size(), 0).has_value());
    const std::u32string spaced = U"** a**";
    CHECK(!try_open_marker(spaced, 0, spaced.size(), 0).has_value());
    const std::u32string at_end = U"**";
    CHECK(!try_open_marker(at_end, 0, 2, 0).has_value());
}

// covers: try_open_marker
TEST(try_open_marker_is_bounded_by_end) {
    const std::u32string s = U"**a**";
    CHECK(!try_open_marker(s, 0, 4, 0).has_value());
    CHECK(!try_open_marker(s, 0, 2, 0).has_value());
}

// covers: try_open_marker
TEST(try_open_marker_precondition_violations) {
    const std::u32string s = U"**a**";
    CHECK_THROWS(try_open_marker(s, 5, 5, 0), ContractViolation);
    CHECK_THROWS(try_open_marker(s, 0, 6, 0), ContractViolation);
}

// covers: parse_slice
TEST(parse_slice_appends_text_and_ranges_with_opening_order) {
    const std::u32string s = U"a **b _c_** `d`";
    Context ctx;
    ctx.input = s;
    parse_slice(ctx, 0, s.size(), 0);
    CHECK_EQ(ctx.text, std::string("a b c d"));
    CHECK_EQ(ctx.pos16, std::size_t{7});
    CHECK_EQ(ctx.next_order, std::size_t{3});
    CHECK_EQ(ctx.ranges.size(), std::size_t{3});
}

// covers: parse_slice
TEST(parse_slice_works_on_subslice_with_open_styles) {
    const std::u32string s = U"xx**a**yy";
    Context ctx;
    ctx.input = s;
    parse_slice(ctx, 2, 7, 0);
    CHECK_EQ(ctx.text, std::string("a"));
    Context inner;
    inner.input = s;
    parse_slice(inner, 2, 7, style_bit(Style::Bold));
    CHECK_EQ(inner.text, std::string("**a**"));
    CHECK(inner.ranges.empty());
    Context empty;
    empty.input = s;
    parse_slice(empty, 3, 3, 0);
    CHECK(empty.text.empty());
}

// covers: parse_slice
TEST(parse_slice_precondition_violations) {
    const std::u32string s = U"abc";
    Context ctx;
    ctx.input = s;
    CHECK_THROWS(parse_slice(ctx, 2, 1, 0), ContractViolation);
    CHECK_THROWS(parse_slice(ctx, 0, 4, 0), ContractViolation);
}

// covers: sort_ranges
TEST(sort_ranges_by_offset_then_length_desc_then_open_order) {
    std::vector<OrderedRange> v = {
        {rg(Style::Code, 5, 1), 4},   {rg(Style::Italic, 0, 1), 2}, {rg(Style::Bold, 0, 1), 1},
        {rg(Style::Link, 0, 3, "u"), 3}, {rg(Style::Bold, 2, 2), 0},
    };
    sort_ranges(v);
    CHECK_EQ(v.size(), std::size_t{5});
    CHECK_EQ(v[0].open_order, std::size_t{3});  // longest at offset 0
    CHECK_EQ(v[1].open_order, std::size_t{1});  // equal intervals: opened earlier first
    CHECK_EQ(v[2].open_order, std::size_t{2});
    CHECK_EQ(v[3].open_order, std::size_t{0});
    CHECK_EQ(v[4].open_order, std::size_t{4});
}

// covers: sort_ranges
TEST(sort_ranges_trivial_inputs) {
    std::vector<OrderedRange> empty;
    sort_ranges(empty);
    CHECK(empty.empty());
    std::vector<OrderedRange> one = {{rg(Style::Bold, 3, 1), 0}};
    sort_ranges(one);
    CHECK_EQ(one.size(), std::size_t{1});
}
