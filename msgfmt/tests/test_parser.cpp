#include <string>
#include <vector>

#include "parser_test_utils.hpp"

using msgfmt::Style;
using ptest::rg;

constexpr Style kBold = Style::Bold;
constexpr Style kItalic = Style::Italic;
constexpr Style kCode = Style::Code;
constexpr Style kLink = Style::Link;

// ---------------------------------------------------------------- Appendix A

// covers: parse, parse_utf8
TEST(appendix_a_examples_1_to_8) {
    EXPECT_PARSE("**важно**", "важно", rg(kBold, 0, 5));
    EXPECT_PARSE("Привет, **мир**!", "Привет, мир!", rg(kBold, 8, 3));
    EXPECT_PARSE("_курсив_", "курсив", rg(kItalic, 0, 6));
    EXPECT_PLAIN("a_b_c", "a_b_c");
    EXPECT_PLAIN("**не закрыто", "**не закрыто");
    EXPECT_PLAIN("2 \\* 3", "2 * 3");
    EXPECT_PARSE("`чат * список`", "чат * список", rg(kCode, 0, 12));
    EXPECT_PARSE("[документация](https://docs.github.com)", "документация",
                 rg(kLink, 0, 12, "https://docs.github.com"));
}

// covers: parse, parse_utf8
TEST(appendix_a_examples_9_to_15) {
    EXPECT_PARSE("**жирный _и_ курсив**", "жирный и курсив", rg(kBold, 0, 15), rg(kItalic, 7, 1));
    EXPECT_PARSE("🚀 **ок**", "🚀 ок", rg(kBold, 3, 2));
    EXPECT_PARSE("**🚀 ок**", "🚀 ок", rg(kBold, 0, 5));
    EXPECT_PLAIN("[текст]()", "[текст]()");
    EXPECT_PLAIN("\\\\", "\\");
    EXPECT_PLAIN("", "");
    EXPECT_PLAIN("_ е_", "_ е_");
}

// covers: parse
TEST(decision_a1_example9_both_variants) {
    EXPECT_PARSE("**жирный _и_ курсив**", "жирный и курсив", rg(kBold, 0, 15), rg(kItalic, 7, 1));
    EXPECT_PARSE("**жирный _и курсив_**", "жирный и курсив", rg(kBold, 0, 15), rg(kItalic, 7, 8));
}

// ---------------------------------------------------------------- Rule 1 (single left-to-right pass)

// covers: parse
TEST(rule1_several_constructs_left_to_right) {
    EXPECT_PARSE("a **b** c **d**", "a b c d", rg(kBold, 2, 1), rg(kBold, 6, 1));
    EXPECT_PARSE("**a** _b_ `c`", "a b c", rg(kBold, 0, 1), rg(kItalic, 2, 1), rg(kCode, 4, 1));
}

// covers: parse
TEST(rule1_plain_text_is_unchanged) {
    EXPECT_PLAIN("hello, world 123", "hello, world 123");
    EXPECT_PLAIN("one * star", "one * star");
    EXPECT_PLAIN("line1\nline2\t\x01", "line1\nline2\t\x01");
}

// ---------------------------------------------------------------- Rule 2 (opening markers)

// covers: parse
TEST(rule2_each_marker_opens_its_style) {
    EXPECT_PARSE("**a**", "a", rg(kBold, 0, 1));
    EXPECT_PARSE("_b_", "b", rg(kItalic, 0, 1));
    EXPECT_PARSE("`c`", "c", rg(kCode, 0, 1));
    EXPECT_PARSE("[d](u)", "d", rg(kLink, 0, 1, "u"));
}

// covers: parse
TEST(rule2_single_star_is_not_a_marker) {
    EXPECT_PLAIN("*a*", "*a*");
    EXPECT_PLAIN("a * b ** c", "a * b ** c");
}

// ---------------------------------------------------------------- Rule 3 (underscore at word boundaries)

// covers: parse
TEST(rule3_underscore_opens_at_boundary) {
    EXPECT_PARSE(" _a_ ", " a ", rg(kItalic, 1, 1));
    EXPECT_PARSE("a _b_", "a b", rg(kItalic, 2, 1));
    EXPECT_PARSE("(_a_)", "(a)", rg(kItalic, 1, 1));
    EXPECT_PARSE("_a_.", "a.", rg(kItalic, 0, 1));
    EXPECT_PARSE("_a b_", "a b", rg(kItalic, 0, 3));
}

// covers: parse
TEST(rule3_underscore_inside_words_is_literal) {
    EXPECT_PLAIN("a_b_c", "a_b_c");
    EXPECT_PLAIN("1_a_", "1_a_");
    EXPECT_PLAIN("й_a_", "й_a_");
    EXPECT_PLAIN("_a_1", "_a_1");
    EXPECT_PLAIN("_a_я", "_a_я");
}

// covers: parse
TEST(rule3_emoji_is_a_boundary_but_digits_and_cyrillic_are_not) {
    EXPECT_PARSE("🚀_a_", "🚀a", rg(kItalic, 2, 1));
    EXPECT_PARSE("_a_🚀", "a🚀", rg(kItalic, 0, 1));
    EXPECT_PLAIN("9_a_", "9_a_");
    EXPECT_PLAIN("я_a_", "я_a_");
}

// covers: parse
TEST(rule3_underscore_followed_by_space_does_not_open) {
    EXPECT_PLAIN("_ a_", "_ a_");
    EXPECT_PLAIN("_", "_");
    EXPECT_PLAIN("a _ b _", "a _ b _");
}

// covers: parse
TEST(rule3_inner_underscore_inside_italic_is_text) {
    EXPECT_PARSE("_a_b_", "a_b", rg(kItalic, 0, 3));
    EXPECT_PARSE("_a _b_", "a _b", rg(kItalic, 0, 4));
}

// ---------------------------------------------------------------- Rule 4 (non-space after opening marker)

// covers: parse
TEST(rule4_space_after_marker_gives_literals) {
    EXPECT_PLAIN("2 ** 3", "2 ** 3");
    EXPECT_PLAIN("** 3", "** 3");
    EXPECT_PLAIN("` a`", "` a`");
    EXPECT_PLAIN("[ a](u)", "[ a](u)");
    EXPECT_PLAIN("**\ta**", "**\ta**");
}

// covers: parse
TEST(rule4_marker_at_end_of_input_is_literal) {
    EXPECT_PLAIN("**", "**");
    EXPECT_PLAIN("`", "`");
    EXPECT_PLAIN("[", "[");
    EXPECT_PLAIN("a **", "a **");
}

// covers: parse
TEST(rule4_unicode_space_after_marker_is_also_space) {
    EXPECT_PLAIN("**\xC2\xA0" "a**", "**\xC2\xA0" "a**");
    EXPECT_PLAIN("**\xE3\x80\x80" "a**", "**\xE3\x80\x80" "a**");
}

// ---------------------------------------------------------------- Rule 5 (nearest suitable closing marker)

// covers: parse
TEST(rule5_closer_must_follow_non_space) {
    EXPECT_PARSE("**a **b**", "a **b", rg(kBold, 0, 5));
    EXPECT_PARSE("`a ` b`", "a ` b", rg(kCode, 0, 5));
}

// covers: parse
TEST(rule5_nearest_closer_wins) {
    EXPECT_PARSE("**a** b**", "a b**", rg(kBold, 0, 1));
    EXPECT_PARSE("`a` `b`", "a b", rg(kCode, 0, 1), rg(kCode, 2, 1));
}

// covers: parse
TEST(rule5_escaped_closer_is_skipped) {
    EXPECT_PARSE("**a\\** b**", "a** b", rg(kBold, 0, 5));
    EXPECT_PARSE("_a\\_ b_", "a_ b", rg(kItalic, 0, 4));
}

// ---------------------------------------------------------------- Rule 6 (unclosed constructs are literal)

// covers: parse
TEST(rule6_unclosed_constructs_are_literal) {
    EXPECT_PLAIN("**a", "**a");
    EXPECT_PLAIN("_a", "_a");
    EXPECT_PLAIN("`a", "`a");
    EXPECT_PLAIN("[a](", "[a](");
    EXPECT_PLAIN("[a]", "[a]");
    EXPECT_PLAIN("[a", "[a");
    EXPECT_PLAIN("_a _", "_a _");
}

// covers: parse
TEST(rule6_literal_opener_does_not_hide_later_constructs) {
    EXPECT_PARSE("**a _b_", "**a b", rg(kItalic, 4, 1));
    EXPECT_PARSE("`a **b**", "`a b", rg(kBold, 3, 1));
}

// ---------------------------------------------------------------- Rule 7 (code is verbatim)

// covers: parse
TEST(rule7_markup_inside_code_is_not_interpreted) {
    EXPECT_PARSE("`**a**`", "**a**", rg(kCode, 0, 5));
    EXPECT_PARSE("`_a_ [b](c)`", "_a_ [b](c)", rg(kCode, 0, 10));
}

// covers: parse
TEST(rule7_escaping_does_not_act_in_code) {
    EXPECT_PARSE("`a\\*b`", "a\\*b", rg(kCode, 0, 4));
    EXPECT_PARSE("`a\\`", "a\\", rg(kCode, 0, 2));
}

// covers: parse
TEST(rule7_code_inside_other_styles) {
    EXPECT_PARSE("**`a`**", "a", rg(kBold, 0, 1), rg(kCode, 0, 1));
    EXPECT_PARSE("x `**` y", "x ** y", rg(kCode, 2, 2));
}

// ---------------------------------------------------------------- Rule 8 (links)

// covers: parse
TEST(rule8_valid_links) {
    EXPECT_PARSE("[a b](u)", "a b", rg(kLink, 0, 3, "u"));
    EXPECT_PARSE("[a](http://x.y/z?q=1&r=2)", "a", rg(kLink, 0, 1, "http://x.y/z?q=1&r=2"));
    EXPECT_PARSE("see [a](u) now", "see a now", rg(kLink, 4, 1, "u"));
}

// covers: parse
TEST(rule8_invalid_links_are_literal) {
    EXPECT_PLAIN("[a]()", "[a]()");
    EXPECT_PLAIN("[](u)", "[](u)");
    EXPECT_PLAIN("[a](u v)", "[a](u v)");
    EXPECT_PLAIN("[a]( u)", "[a]( u)");
    EXPECT_PLAIN("[a] (u)", "[a] (u)");
    EXPECT_PLAIN("[a](u", "[a](u");
}

// covers: parse
TEST(rule8_text_cannot_contain_closing_bracket_and_url_ends_at_first_paren) {
    EXPECT_PLAIN("[a]](u)", "[a]](u)");
    EXPECT_PARSE("[a](u))", "a)", rg(kLink, 0, 1, "u"));
    EXPECT_PARSE("[a](u)(v)", "a(v)", rg(kLink, 0, 1, "u"));
}

// covers: parse
TEST(rule8_url_is_verbatim_and_may_be_non_ascii) {
    EXPECT_PARSE("[a](http://я.рф/*_*)", "a", rg(kLink, 0, 1, "http://я.рф/*_*"));
    EXPECT_PARSE("[a](u\\)", "a", rg(kLink, 0, 1, "u\\"));
}

// ---------------------------------------------------------------- Rule 9 (escaping)

// covers: parse
TEST(rule9_backslash_before_service_character) {
    EXPECT_PLAIN("\\*a\\*", "*a*");
    EXPECT_PLAIN("\\_a_", "_a_");
    EXPECT_PLAIN("\\`\\[\\]\\(\\)", "`[]()");
    EXPECT_PLAIN("\\\\*a\\\\*", "\\*a\\*");
}

// covers: parse
TEST(rule9_backslash_before_other_character_is_kept) {
    EXPECT_PLAIN("\\a", "\\a");
    EXPECT_PLAIN("\\ ", "\\ ");
    EXPECT_PLAIN("C:\\dir\\file", "C:\\dir\\file");
    EXPECT_PLAIN("\\й", "\\й");
}

// covers: parse
TEST(rule9_escape_inside_link_text) {
    EXPECT_PARSE("[a\\]b](u)", "a]b", rg(kLink, 0, 3, "u"));
    EXPECT_PARSE("[\\*a\\*](u)", "*a*", rg(kLink, 0, 3, "u"));
}

// ---------------------------------------------------------------- Rule 10 (nesting)

// covers: parse
TEST(rule10_different_styles_nest) {
    EXPECT_PARSE("**a _b_ c**", "a b c", rg(kBold, 0, 5), rg(kItalic, 2, 1));
    EXPECT_PARSE("_a **b** c_", "a b c", rg(kItalic, 0, 5), rg(kBold, 2, 1));
    EXPECT_PARSE("**[a](u) `b`**", "a b", rg(kBold, 0, 3), rg(kLink, 0, 1, "u"), rg(kCode, 2, 1));
}

// covers: parse
TEST(rule10_same_style_inside_itself_is_text) {
    EXPECT_PARSE("**a **b** c**", "a **b c**", rg(kBold, 0, 5));
    EXPECT_PARSE("_a _b_ c_", "a _b c_", rg(kItalic, 0, 4));
}

// covers: parse
TEST(rule10_links_and_bold_nest_both_ways) {
    EXPECT_PARSE("**[a](u)**", "a", rg(kBold, 0, 1), rg(kLink, 0, 1, "u"));
    EXPECT_PARSE("[**a**](u)", "a", rg(kLink, 0, 1, "u"), rg(kBold, 0, 1));
    EXPECT_PARSE("[_a_ **b**](u)", "a b", rg(kLink, 0, 3, "u"), rg(kItalic, 0, 1), rg(kBold, 2, 1));
}

// covers: parse
TEST(rule10_three_levels) {
    EXPECT_PARSE("**a _b `c`_**", "a b c", rg(kBold, 0, 5), rg(kItalic, 2, 3), rg(kCode, 4, 1));
}

// ---------------------------------------------------------------- Rule 11 (empty input)

// covers: parse, parse_utf8
TEST(rule11_empty_input) {
    EXPECT_PLAIN("", "");
    const msgfmt::ParseResult r = msgfmt::parse(std::u32string());
    CHECK(r.text.empty() && r.ranges.empty());
    const auto via_utf8 = msgfmt::parse_utf8("");
    CHECK(via_utf8.has_value() && via_utf8->text.empty() && via_utf8->ranges.empty());
}

// ---------------------------------------------------------------- Rule 12 (ordering)

// covers: parse
TEST(rule12_sorted_by_offset_then_length_desc) {
    EXPECT_PARSE("`a` **b _c_**", "a b c", rg(kCode, 0, 1), rg(kBold, 2, 3), rg(kItalic, 4, 1));
    EXPECT_PARSE("**_ab_ c**", "ab c", rg(kBold, 0, 4), rg(kItalic, 0, 2));
}

// covers: parse
TEST(decision_a8_identical_intervals_follow_opening_order) {
    EXPECT_PARSE("**_a_**", "a", rg(kBold, 0, 1), rg(kItalic, 0, 1));
    EXPECT_PARSE("_**a**_", "a", rg(kItalic, 0, 1), rg(kBold, 0, 1));
    EXPECT_PARSE("**`a`**", "a", rg(kBold, 0, 1), rg(kCode, 0, 1));
    EXPECT_PARSE("`a`", "a", rg(kCode, 0, 1));
}

// covers: parse
TEST(rule12_no_partial_overlaps_for_crossing_markers) {
    EXPECT_PARSE("**a _b** c_", "a _b c_", rg(kBold, 0, 4));
    EXPECT_PARSE("_a **b_ c**", "a **b c**", rg(kItalic, 0, 5));
    EXPECT_PARSE("**a `x**y` b**", "a `xy` b**", rg(kBold, 0, 4));
}

// ---------------------------------------------------------------- Decisions A2..A7

// covers: parse
TEST(decision_a2_closer_search_is_raw_and_bounded_by_the_outer_pair) {
    EXPECT_PARSE("**a _b** c_", "a _b c_", rg(kBold, 0, 4));
    EXPECT_PARSE("**a `x**y` b**", "a `xy` b**", rg(kBold, 0, 4));
    EXPECT_PARSE("_a **b_ c**", "a **b c**", rg(kItalic, 0, 5));
}

// covers: parse
TEST(decision_a3_empty_content_is_literal) {
    EXPECT_PLAIN("****", "****");
    EXPECT_PLAIN("``", "``");
    EXPECT_PLAIN("__", "__");
    EXPECT_PLAIN("a **** b", "a **** b");
    EXPECT_PLAIN("[]()", "[]()");
}

// covers: parse
TEST(decision_a4_triple_and_mixed_markers) {
    EXPECT_PARSE("***a***", "*a*", rg(kBold, 0, 2));
    EXPECT_PARSE("**_a_**", "a", rg(kBold, 0, 1), rg(kItalic, 0, 1));
    EXPECT_PARSE("_**a**_", "a", rg(kItalic, 0, 1), rg(kBold, 0, 1));
    EXPECT_PARSE("__a__", "_a_", rg(kItalic, 0, 2));
}

// covers: parse
TEST(decision_a5_link_details) {
    EXPECT_PARSE("[a[b](u)", "a[b", rg(kLink, 0, 3, "u"));
    EXPECT_PARSE("[[a](u)](v)", "[a](v)", rg(kLink, 0, 2, "u"));
    EXPECT_PARSE("[a\\]b](u)", "a]b", rg(kLink, 0, 3, "u"));
    EXPECT_PARSE("[a](u\\)", "a", rg(kLink, 0, 1, "u\\"));
    EXPECT_PLAIN("[a ](u)", "[a ](u)");
    EXPECT_PARSE("**[a](u)**", "a", rg(kBold, 0, 1), rg(kLink, 0, 1, "u"));
    EXPECT_PARSE("[**a**](u)", "a", rg(kLink, 0, 1, "u"), rg(kBold, 0, 1));
    EXPECT_PARSE("**a [b](u) c**", "a b c", rg(kBold, 0, 5), rg(kLink, 2, 1, "u"));
}

// covers: parse
TEST(decision_a6_underscore_boundaries_and_escapes) {
    EXPECT_PLAIN("\\_a_", "_a_");
    EXPECT_PLAIN("a\\__", "a__");
    EXPECT_PARSE("**_a_**", "a", rg(kBold, 0, 1), rg(kItalic, 0, 1));
    EXPECT_PARSE("🚀_a_", "🚀a", rg(kItalic, 2, 1));
    EXPECT_PLAIN("5_a_", "5_a_");
    EXPECT_PLAIN("ж_a_", "ж_a_");
    EXPECT_PARSE("_a_🚀", "a🚀", rg(kItalic, 0, 1));
}

// covers: parse
TEST(decision_a7_trailing_and_ordinary_backslashes) {
    EXPECT_PLAIN("a\\", "a\\");
    EXPECT_PLAIN("\\", "\\");
    EXPECT_PLAIN("\\\\\\", "\\\\");
    EXPECT_PLAIN("\\a", "\\a");
    EXPECT_PLAIN("**a\\**", "**a**");
}

// ---------------------------------------------------------------- Emoji and Cyrillic positions

// covers: parse
TEST(emoji_inside_outside_and_on_the_borders_of_ranges) {
    EXPECT_PARSE("🚀**a**", "🚀a", rg(kBold, 2, 1));
    EXPECT_PARSE("**🚀**", "🚀", rg(kBold, 0, 2));
    EXPECT_PARSE("**a🚀b**", "a🚀b", rg(kBold, 0, 4));
    EXPECT_PARSE("**a**🚀", "a🚀", rg(kBold, 0, 1));
    EXPECT_PARSE("🚀 _🚀_ 🚀", "🚀 🚀 🚀", rg(kItalic, 3, 2));
    EXPECT_PARSE("[🚀](u) `🚀🚀`", "🚀 🚀🚀", rg(kLink, 0, 2, "u"), rg(kCode, 3, 4));
}

// covers: parse
TEST(cyrillic_composed_and_decomposed_have_different_positions) {
    EXPECT_PARSE("**й**", "й", rg(kBold, 0, 1));
    EXPECT_PARSE("**и\xCC\x86**", "и\xCC\x86", rg(kBold, 0, 2));
    EXPECT_PARSE("до **й** после", "до й после", rg(kBold, 3, 1));
}

// ---------------------------------------------------------------- API

// covers: style_name
TEST(style_name_of_every_style) {
    CHECK_EQ(msgfmt::style_name(Style::Bold), std::string_view("bold"));
    CHECK_EQ(msgfmt::style_name(Style::Italic), std::string_view("italic"));
    CHECK_EQ(msgfmt::style_name(Style::Code), std::string_view("code"));
    CHECK_EQ(msgfmt::style_name(Style::Link), std::string_view("link"));
}

// covers: operator==
TEST(range_and_result_equality) {
    CHECK(rg(kBold, 0, 1) == rg(kBold, 0, 1));
    CHECK(!(rg(kBold, 0, 1) == rg(kItalic, 0, 1)));
    CHECK(!(rg(kBold, 0, 1) == rg(kBold, 1, 1)));
    CHECK(!(rg(kBold, 0, 1) == rg(kBold, 0, 2)));
    CHECK(!(rg(kLink, 0, 1, "u") == rg(kLink, 0, 1, "v")));
    const msgfmt::ParseResult a{"x", {rg(kBold, 0, 1)}};
    const msgfmt::ParseResult same{"x", {rg(kBold, 0, 1)}};
    const msgfmt::ParseResult other_text{"y", {rg(kBold, 0, 1)}};
    const msgfmt::ParseResult no_ranges{"x", {}};
    CHECK(a == same);
    CHECK(!(a == other_text));
    CHECK(!(a == no_ranges));
}

// covers: parse_utf8
TEST(parse_utf8_rejects_invalid_utf8_only) {
    CHECK(!msgfmt::parse_utf8("\xFF").has_value());
    CHECK(!msgfmt::parse_utf8("**a\xC0\x80**").has_value());
    CHECK(!msgfmt::parse_utf8("a\xED\xA0\x80").has_value());
    CHECK(!msgfmt::parse_utf8("**a\xE2\x82").has_value());
    CHECK(msgfmt::parse_utf8("\xEF\xBB\xBF**a**").has_value());
    CHECK(msgfmt::parse_utf8(std::string("a\0b", 3)).has_value());
}

// covers: parse_utf8
TEST(parse_utf8_keeps_bom_nul_and_controls_as_text) {
    EXPECT_PARSE("\xEF\xBB\xBF**a**", "\xEF\xBB\xBF" "a", rg(kBold, 1, 1));
    const auto r = msgfmt::parse_utf8(std::string("**a\0b**", 7));
    CHECK(r.has_value());
    if (r) {
        CHECK_EQ(r->text, std::string("a\0b", 3));
        CHECK(r->ranges == std::vector<msgfmt::Range>{rg(kBold, 0, 3)});
    }
}

// covers: parse
TEST(parse_replaces_invalid_scalar_values) {
    const msgfmt::ParseResult r = msgfmt::parse(std::u32string{U'a', 0xD800, 0x110000, U'b'});
    CHECK_EQ(r.text, std::string("a\xEF\xBF\xBD\xEF\xBF\xBD" "b"));
    CHECK(r.ranges.empty());
    const msgfmt::ParseResult inside = msgfmt::parse(std::u32string{U'*', U'*', 0xDFFF, U'*', U'*'});
    CHECK_EQ(inside.text, std::string("\xEF\xBF\xBD"));
    CHECK(inside.ranges == std::vector<msgfmt::Range>{rg(kBold, 0, 1)});
    CHECK(ptest::find_invariant_violations(inside).empty());
}

// covers: parse
TEST(parse_is_deterministic) {
    const std::u32string input = U"**a _b_ [c](u)** `d` \\* 🚀";
    CHECK(msgfmt::parse(input) == msgfmt::parse(input));
}
