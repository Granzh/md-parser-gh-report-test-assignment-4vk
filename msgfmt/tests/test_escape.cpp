#include <string>

#include "msgfmt/escape.hpp"
#include "msgfmt/parser.hpp"
#include "msgfmt/parser_internal.hpp"
#include "msgfmt/utf8.hpp"
#include "test_framework.hpp"

using msgfmt::escape_text;
using msgfmt::escape_utf8;
using msgfmt::is_service_char;

// covers: is_service_char
TEST(is_service_char_exactly_eight) {
    for (const char32_t cp : std::u32string(U"*_`[]()\\")) {
        CHECK(is_service_char(cp));
    }
    for (const char32_t cp : std::u32string(U"aZ 09-.!{}<>~#/\n\u0439")) {
        CHECK(!is_service_char(cp));
    }
    CHECK(!is_service_char(0));
    CHECK(!is_service_char(0x1F680));
    CHECK(!is_service_char(0xFFFFFFFF));
}

// covers: is_service_char
TEST(is_service_char_agrees_with_parser_is_escapable) {
    for (char32_t cp = 0; cp < 0x3000; ++cp) {
        CHECK_EQ(is_service_char(cp), msgfmt::detail::is_escapable(cp));
    }
}

// covers: escape_text
TEST(escape_text_basic_and_empty) {
    CHECK_EQ(escape_text(U"2 * 3"), std::u32string(U"2 \\* 3"));
    CHECK_EQ(escape_text(U""), std::u32string());
    CHECK_EQ(escape_text(U"plain text"), std::u32string(U"plain text"));
}

// covers: escape_text
TEST(escape_text_every_service_character) {
    CHECK_EQ(escape_text(U"*"), std::u32string(U"\\*"));
    CHECK_EQ(escape_text(U"_"), std::u32string(U"\\_"));
    CHECK_EQ(escape_text(U"`"), std::u32string(U"\\`"));
    CHECK_EQ(escape_text(U"["), std::u32string(U"\\["));
    CHECK_EQ(escape_text(U"]"), std::u32string(U"\\]"));
    CHECK_EQ(escape_text(U"("), std::u32string(U"\\("));
    CHECK_EQ(escape_text(U")"), std::u32string(U"\\)"));
    CHECK_EQ(escape_text(U"\\"), std::u32string(U"\\\\"));
    CHECK_EQ(escape_text(U"\\\\"), std::u32string(U"\\\\\\\\"));
    CHECK_EQ(escape_text(U"**"), std::u32string(U"\\*\\*"));
}

// covers: escape_text
TEST(escape_text_leaves_other_characters_unchanged) {
    const std::u32string other = {0x439, 0x1F680, 0xFEFF, 0, U'\n', U'\r', U'\t', U' ', 0x2028, 0xD800};
    CHECK_EQ(escape_text(other), other);
}

// covers: escape_utf8
TEST(escape_utf8_examples) {
    CHECK_EQ(escape_utf8("2 * 3"), std::optional<std::string>("2 \\* 3"));
    CHECK_EQ(escape_utf8(""), std::optional<std::string>(""));
    CHECK_EQ(escape_utf8("[a](b)"), std::optional<std::string>("\\[a\\]\\(b\\)"));
    CHECK_EQ(escape_utf8("\\"), std::optional<std::string>("\\\\"));
    CHECK_EQ(escape_utf8("Привет 🚀 _мир_"), std::optional<std::string>("Привет 🚀 \\_мир\\_"));
}

// covers: escape_utf8
TEST(escape_utf8_keeps_bom_nul_and_line_breaks) {
    CHECK_EQ(escape_utf8("\xEF\xBB\xBF*"), std::optional<std::string>("\xEF\xBB\xBF\\*"));
    CHECK_EQ(escape_utf8(std::string("a\0*\r\n", 5)), std::optional<std::string>(std::string("a\0\\*\r\n", 6)));
}

// covers: escape_utf8
TEST(escape_utf8_rejects_invalid_utf8) {
    CHECK(!escape_utf8("\xFF").has_value());
    CHECK(!escape_utf8("*\xC0\x80").has_value());
    CHECK(!escape_utf8("a\xED\xA0\x80").has_value());
    CHECK(!escape_utf8("\xE2\x82").has_value());
}

// covers: escape_text, escape_utf8, parse
TEST(round_trip_parse_of_escaped_text_gives_original_text) {
    const std::u32string alphabet = {U'*', U'_', U'`', U'[', U']', U'(', U')', U'\\', U'a', U' ', 0x439, 0x1F680};
    std::size_t checked = 0;
    std::u32string current;
    std::vector<std::size_t> digits;
    for (std::size_t len = 0; len <= 4; ++len) {
        digits.assign(len, 0);
        while (true) {
            current.clear();
            for (const std::size_t d : digits) {
                current.push_back(alphabet[d]);
            }
            const msgfmt::ParseResult r = msgfmt::parse(escape_text(current));
            std::string expected;
            for (const char32_t cp : current) {
                msgfmt::encode_utf8_append(expected, cp);
            }
            if (r.text != expected || !r.ranges.empty()) {
                CHECK_EQ(r.text, expected);
                CHECK(r.ranges.empty());
                return;
            }
            ++checked;
            std::size_t pos = 0;
            while (pos < len && ++digits[pos] == alphabet.size()) {
                digits[pos++] = 0;
            }
            if (pos == len) {
                break;
            }
        }
    }
    CHECK_EQ(checked, std::size_t{1 + 12 + 144 + 1728 + 20736});
}

// covers: escape_utf8, parse_utf8
TEST(round_trip_on_realistic_strings) {
    const char* const samples[] = {
        "Привет, **мир**!", "a_b_c", "[текст](https://x.y)", "`код` и \\ слеш", "C:\\dir\\*.txt",
        "🚀 **ок** _да_", "", "***", "((()))", "line1\nline2\r\n", "tab\there",
    };
    for (const char* sample : samples) {
        const auto escaped = escape_utf8(sample);
        CHECK(escaped.has_value());
        if (!escaped) {
            continue;
        }
        const auto parsed = msgfmt::parse_utf8(*escaped);
        CHECK(parsed.has_value());
        if (parsed) {
            CHECK_EQ(parsed->text, std::string(sample));
            CHECK(parsed->ranges.empty());
        }
    }
}
