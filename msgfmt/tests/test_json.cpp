#include <string>
#include <vector>

#include "json_validator.hpp"
#include "msgfmt/json.hpp"
#include "msgfmt/parser.hpp"
#include "test_framework.hpp"

using msgfmt::Range;
using msgfmt::Style;

/**
 * @brief Counts non-overlapping occurrences of a substring.
 * @param haystack text to search.
 * @param needle non-empty substring.
 * @return number of occurrences.
 * @pre needle is not empty.
 * @post none.
 * @throws does not throw.
 */
static std::size_t count_occurrences(const std::string& haystack, const std::string& needle) {
    std::size_t n = 0;
    for (std::size_t pos = haystack.find(needle); pos != std::string::npos; pos = haystack.find(needle, pos + needle.size())) {
        ++n;
    }
    return n;
}

// covers: json_escape
TEST(json_escape_every_control_character) {
    for (unsigned c = 0; c < 0x20; ++c) {
        const std::string input(1, static_cast<char>(c));
        std::string expected;
        switch (c) {
            case 0x08: expected = "\\b"; break;
            case 0x0C: expected = "\\f"; break;
            case 0x0A: expected = "\\n"; break;
            case 0x0D: expected = "\\r"; break;
            case 0x09: expected = "\\t"; break;
            default: {
                const char* hex = "0123456789abcdef";
                expected = std::string("\\u00") + hex[c >> 4] + hex[c & 15];
            }
        }
        CHECK_EQ(msgfmt::json_escape(input), expected);
    }
    CHECK_EQ(msgfmt::json_escape(std::string(1, '\0')), std::string("\\u0000"));
    CHECK_EQ(msgfmt::json_escape("\x1F"), std::string("\\u001f"));
    CHECK_EQ(msgfmt::json_escape("\x0B"), std::string("\\u000b"));
}

// covers: json_escape
TEST(json_escape_quote_backslash_and_passthrough) {
    CHECK_EQ(msgfmt::json_escape("\""), std::string("\\\""));
    CHECK_EQ(msgfmt::json_escape("\\"), std::string("\\\\"));
    CHECK_EQ(msgfmt::json_escape("a/b"), std::string("a/b"));
    CHECK_EQ(msgfmt::json_escape("\x7F"), std::string("\x7F"));
    CHECK_EQ(msgfmt::json_escape("plain 123"), std::string("plain 123"));
    CHECK_EQ(msgfmt::json_escape(""), std::string());
}

// covers: json_escape
TEST(json_escape_non_ascii_is_verbatim) {
    CHECK_EQ(msgfmt::json_escape("Привет"), std::string("Привет"));
    CHECK_EQ(msgfmt::json_escape("🚀"), std::string("🚀"));
    CHECK_EQ(msgfmt::json_escape("\xE2\x80\xA8\xE2\x80\xA9"), std::string("\xE2\x80\xA8\xE2\x80\xA9"));
    CHECK_EQ(msgfmt::json_escape("\xEF\xBB\xBF"), std::string("\xEF\xBB\xBF"));
}

// covers: to_json
TEST(to_json_example_from_the_specification) {
    const auto r = msgfmt::parse_utf8("Привет, **мир**!");
    CHECK(r.has_value());
    if (r) {
        CHECK_EQ(msgfmt::to_json(*r),
                 std::string("{\"text\":\"Привет, мир!\",\"ranges\":[{\"style\":\"bold\",\"offset\":8,\"length\":3,\"url\":\"\"}]}"));
    }
}

// covers: to_json
TEST(to_json_empty_result_and_no_trailing_newline) {
    const msgfmt::ParseResult empty;
    CHECK_EQ(msgfmt::to_json(empty), std::string("{\"text\":\"\",\"ranges\":[]}"));
    const std::string json = msgfmt::to_json(msgfmt::ParseResult{"a\n", {}});
    CHECK(json.back() == '}');
}

// covers: to_json
TEST(to_json_link_and_several_ranges) {
    const msgfmt::ParseResult r{"ab", {Range{Style::Link, 0, 1, "http://x"}, Range{Style::Code, 1, 1, ""}}};
    CHECK_EQ(msgfmt::to_json(r),
             std::string("{\"text\":\"ab\",\"ranges\":[{\"style\":\"link\",\"offset\":0,\"length\":1,\"url\":\"http://x\"},"
                         "{\"style\":\"code\",\"offset\":1,\"length\":1,\"url\":\"\"}]}"));
}

// covers: to_json
TEST(to_json_escapes_special_characters_in_text_and_url) {
    const auto r = msgfmt::parse_utf8("\"q\" \\\\ \n\t[a](u\"v)");
    CHECK(r.has_value());
    if (r) {
        const std::string json = msgfmt::to_json(*r);
        CHECK(jsonv::is_valid_json(json));
        CHECK(json.find("\"text\":\"\\\"q\\\" \\\\ \\n\\ta\"") != std::string::npos);
        CHECK(json.find("\"url\":\"u\\\"v\"") != std::string::npos);
    }
}

// covers: to_json
TEST(to_json_has_exactly_the_four_range_fields) {
    const auto r = msgfmt::parse_utf8("**a** _b_ `c` [d](u)");
    CHECK(r.has_value());
    if (r) {
        const std::string json = msgfmt::to_json(*r);
        CHECK_EQ(count_occurrences(json, "\"style\":"), std::size_t{4});
        CHECK_EQ(count_occurrences(json, "\"offset\":"), std::size_t{4});
        CHECK_EQ(count_occurrences(json, "\"length\":"), std::size_t{4});
        CHECK_EQ(count_occurrences(json, "\"url\":"), std::size_t{4});
        CHECK_EQ(count_occurrences(json, ":"), std::size_t{2 + 16});
        CHECK(json.find("\"style\":\"bold\"") < json.find("\"style\":\"italic\""));
    }
}

// covers: to_json
TEST(to_json_is_valid_json_for_many_inputs) {
    const std::u32string alphabet = {U'*', U'_', U'`', U'[', U']', U'(', U')', U'\\', U'a', U' ', U'"', U'\n', 0x1F680, 0};
    std::size_t invalid = 0;
    std::u32string current;
    std::vector<std::size_t> digits;
    for (std::size_t len = 0; len <= 4; ++len) {
        digits.assign(len, 0);
        while (true) {
            current.clear();
            for (const std::size_t d : digits) {
                current.push_back(alphabet[d]);
            }
            const std::string json = msgfmt::to_json(msgfmt::parse(current));
            if (!jsonv::is_valid_json(json) || json.back() == '\n') {
                ++invalid;
            }
            std::size_t pos = 0;
            while (pos < len && ++digits[pos] == alphabet.size()) {
                digits[pos++] = 0;
            }
            if (pos == len) {
                break;
            }
        }
    }
    CHECK_EQ(invalid, std::size_t{0});
}

// covers: error_json
TEST(error_json_format) {
    CHECK_EQ(msgfmt::error_json("invalid_utf8"), std::string("{\"error\": \"invalid_utf8\"}"));
    CHECK_EQ(msgfmt::error_json(""), std::string("{\"error\": \"\"}"));
    CHECK_EQ(msgfmt::error_json("a\"b"), std::string("{\"error\": \"a\\\"b\"}"));
    CHECK(jsonv::is_valid_json(msgfmt::error_json("x\ny")));
}
