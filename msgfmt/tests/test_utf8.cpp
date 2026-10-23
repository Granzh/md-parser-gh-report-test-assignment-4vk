#include <cstddef>
#include <optional>
#include <string>

#include "msgfmt/contract.hpp"
#include "msgfmt/utf8.hpp"
#include "test_framework.hpp"

using msgfmt::decode_utf8;
using msgfmt::encode_utf8_append;
using msgfmt::utf16_length;
using msgfmt::utf16_units;

/**
 * @brief Encodes one code point into a fresh string.
 * @param cp valid scalar value.
 * @return its UTF-8 bytes.
 * @pre msgfmt::is_scalar_value(cp).
 * @post result has 1..4 bytes.
 * @throws msgfmt::ContractViolation if cp is not a scalar value.
 */
static std::string enc(char32_t cp) {
    std::string s;
    encode_utf8_append(s, cp);
    return s;
}

/**
 * @brief Checks that a byte string is rejected by the strict decoder.
 * @param bytes input to decode.
 * @return true iff decode_utf8 returned nullopt.
 * @pre none.
 * @post none.
 * @throws does not throw.
 */
static bool rejected(const std::string& bytes) {
    return !decode_utf8(bytes).has_value();
}

// covers: is_scalar_value
TEST(is_scalar_value_boundaries) {
    CHECK(msgfmt::is_scalar_value(0));
    CHECK(msgfmt::is_scalar_value(0xD7FF));
    CHECK(!msgfmt::is_scalar_value(0xD800));
    CHECK(!msgfmt::is_scalar_value(0xDBFF));
    CHECK(!msgfmt::is_scalar_value(0xDC00));
    CHECK(!msgfmt::is_scalar_value(0xDFFF));
    CHECK(msgfmt::is_scalar_value(0xE000));
    CHECK(msgfmt::is_scalar_value(0x10FFFF));
    CHECK(!msgfmt::is_scalar_value(0x110000));
    CHECK(!msgfmt::is_scalar_value(0xFFFFFFFF));
}

// covers: encode_utf8_append, decode_utf8, decode_one
TEST(roundtrip_all_code_points) {
    std::size_t failures = 0;
    for (char32_t cp = 0; cp <= 0x10FFFF; ++cp) {
        if (cp >= 0xD800 && cp <= 0xDFFF) {
            continue;
        }
        const std::string bytes = enc(cp);
        const std::optional<std::u32string> back = decode_utf8(bytes);
        if (!back || back->size() != 1 || (*back)[0] != cp) {
            ++failures;
        }
    }
    CHECK_EQ(failures, std::size_t{0});
}

// covers: encode_utf8_append
TEST(encode_lengths_at_boundaries) {
    CHECK_EQ(enc(0x7F).size(), std::size_t{1});
    CHECK_EQ(enc(0x80).size(), std::size_t{2});
    CHECK_EQ(enc(0x7FF).size(), std::size_t{2});
    CHECK_EQ(enc(0x800).size(), std::size_t{3});
    CHECK_EQ(enc(0xD7FF).size(), std::size_t{3});
    CHECK_EQ(enc(0xE000).size(), std::size_t{3});
    CHECK_EQ(enc(0xFFFF).size(), std::size_t{3});
    CHECK_EQ(enc(0x10000).size(), std::size_t{4});
    CHECK_EQ(enc(0x10FFFF).size(), std::size_t{4});
    CHECK_EQ(enc(0x439), std::string("\xD0\xB9"));
    CHECK_EQ(enc(0x1F680), std::string("\xF0\x9F\x9A\x80"));
    CHECK_EQ(enc(0), std::string(1, '\0'));
}

// covers: encode_utf8_append
TEST(encode_appends_and_keeps_existing_content) {
    std::string s = "ab";
    encode_utf8_append(s, 0x439);
    encode_utf8_append(s, 'c');
    CHECK_EQ(s, std::string("ab\xD0\xB9" "c"));
}

// covers: encode_utf8_append
TEST(encode_rejects_surrogates_and_out_of_range) {
    std::string s = "keep";
    CHECK_THROWS(encode_utf8_append(s, 0xD800), msgfmt::ContractViolation);
    CHECK_THROWS(encode_utf8_append(s, 0xDFFF), msgfmt::ContractViolation);
    CHECK_THROWS(encode_utf8_append(s, 0x110000), msgfmt::ContractViolation);
    CHECK_THROWS(encode_utf8_append(s, 0xFFFFFFFF), msgfmt::ContractViolation);
    CHECK_EQ(s, std::string("keep"));
}

// covers: decode_utf8
TEST(decode_boundary_code_points) {
    const char32_t cps[] = {0x7F, 0x80, 0x7FF, 0x800, 0xD7FF, 0xE000, 0xFFFF, 0x10000, 0x10FFFF};
    for (const char32_t cp : cps) {
        const auto decoded = decode_utf8(enc(cp));
        CHECK(decoded.has_value());
        if (decoded) {
            CHECK_EQ(*decoded, std::u32string(1, cp));
        }
    }
}

// covers: decode_utf8
TEST(decode_empty_nul_bom_and_controls) {
    CHECK_EQ(decode_utf8(""), std::optional<std::u32string>(std::u32string()));
    const std::string with_nul("a\0b", 3);
    CHECK_EQ(decode_utf8(with_nul), std::optional<std::u32string>(std::u32string{U'a', 0, U'b'}));
    CHECK_EQ(decode_utf8("\xEF\xBB\xBF" "x"), std::optional<std::u32string>(std::u32string{0xFEFF, U'x'}));
    CHECK_EQ(decode_utf8("\x01\x1F\x7F\r\n"),
             std::optional<std::u32string>(std::u32string{1, 0x1F, 0x7F, U'\r', U'\n'}));
    CHECK_EQ(decode_utf8("\xC2\x85"), std::optional<std::u32string>(std::u32string{0x85}));
}

// covers: decode_utf8, decode_one
TEST(decode_rejects_invalid_start_bytes) {
    CHECK(rejected("\x80"));
    CHECK(rejected("\xBF"));
    CHECK(rejected("a\x80" "b"));
    CHECK(rejected("\xC0\x80"));
    CHECK(rejected("\xC1\xBF"));
    CHECK(rejected("\xF5\x80\x80\x80"));
    CHECK(rejected("\xF8\x88\x80\x80\x80"));
    CHECK(rejected("\xFC"));
    CHECK(rejected("\xFE"));
    CHECK(rejected("\xFF"));
}

// covers: decode_utf8, decode_one
TEST(decode_rejects_overlong_forms) {
    CHECK(rejected("\xC0\xAF"));
    CHECK(rejected("\xE0\x80\xAF"));
    CHECK(rejected("\xE0\x9F\xBF"));
    CHECK(rejected("\xF0\x80\x80\xAF"));
    CHECK(rejected("\xF0\x8F\xBF\xBF"));
    CHECK(decode_utf8("\xE0\xA0\x80").has_value());
    CHECK(decode_utf8("\xF0\x90\x80\x80").has_value());
}

// covers: decode_utf8, decode_one
TEST(decode_rejects_surrogates_and_too_large) {
    CHECK(rejected("\xED\xA0\x80"));
    CHECK(rejected("\xED\xBF\xBF"));
    CHECK(rejected("\xED\xB0\x80"));
    CHECK(decode_utf8("\xED\x9F\xBF").has_value());
    CHECK(decode_utf8("\xEE\x80\x80").has_value());
    CHECK(rejected("\xF4\x90\x80\x80"));
    CHECK(rejected("\xF7\xBF\xBF\xBF"));
    CHECK(decode_utf8("\xF4\x8F\xBF\xBF").has_value());
}

// covers: decode_utf8, decode_one
TEST(decode_rejects_truncated_and_bad_continuations) {
    CHECK(rejected("\xC2"));
    CHECK(rejected("\xE2\x82"));
    CHECK(rejected("\xF0\x9F\x9A"));
    CHECK(rejected("a\xE2"));
    CHECK(rejected("\xC2\x41"));
    CHECK(rejected("\xE2\x82\x41"));
    CHECK(rejected("\xE2\x41\xAC"));
    CHECK(rejected("\xF0\x9F\x41\x80"));
    CHECK(rejected("\xF0\x9F\x9A\x41"));
    CHECK(rejected("\xC2\xC2\x80"));
}

// covers: decode_utf8
TEST(decode_nfc_and_nfd_differ) {
    const auto composed = decode_utf8("\xD0\xB9");
    const auto decomposed = decode_utf8("\xD0\xB8\xCC\x86");
    CHECK(composed.has_value() && decomposed.has_value());
    if (composed && decomposed) {
        CHECK_EQ(*composed, std::u32string{0x439});
        CHECK_EQ(*decomposed, (std::u32string{0x438, 0x306}));
        CHECK(*composed != *decomposed);
        CHECK_EQ(utf16_length(*composed), std::size_t{1});
        CHECK_EQ(utf16_length(*decomposed), std::size_t{2});
    }
}

// covers: decode_one
TEST(decode_one_advances_only_on_success) {
    const std::string s = "a\xD0\xB9\xFF";
    std::size_t pos = 0;
    CHECK_EQ(msgfmt::detail::decode_one(s, pos), std::optional<char32_t>(U'a'));
    CHECK_EQ(pos, std::size_t{1});
    CHECK_EQ(msgfmt::detail::decode_one(s, pos), std::optional<char32_t>(0x439));
    CHECK_EQ(pos, std::size_t{3});
    CHECK_EQ(msgfmt::detail::decode_one(s, pos), std::optional<char32_t>());
    CHECK_EQ(pos, std::size_t{3});
}

// covers: decode_one
TEST(decode_one_precondition_violation) {
    std::size_t pos = 1;
    CHECK_THROWS(msgfmt::detail::decode_one("a", pos), msgfmt::ContractViolation);
    pos = 0;
    CHECK_THROWS(msgfmt::detail::decode_one("", pos), msgfmt::ContractViolation);
}

// covers: utf16_units
TEST(utf16_units_boundaries) {
    CHECK_EQ(utf16_units(0), std::size_t{1});
    CHECK_EQ(utf16_units(0x7F), std::size_t{1});
    CHECK_EQ(utf16_units(0xD7FF), std::size_t{1});
    CHECK_EQ(utf16_units(0xE000), std::size_t{1});
    CHECK_EQ(utf16_units(0xFFFF), std::size_t{1});
    CHECK_EQ(utf16_units(0x10000), std::size_t{2});
    CHECK_EQ(utf16_units(0x1F680), std::size_t{2});
    CHECK_EQ(utf16_units(0x10FFFF), std::size_t{2});
}

// covers: utf16_units
TEST(utf16_units_precondition_violation) {
    CHECK_THROWS(utf16_units(0xD800), msgfmt::ContractViolation);
    CHECK_THROWS(utf16_units(0xDFFF), msgfmt::ContractViolation);
    CHECK_THROWS(utf16_units(0x110000), msgfmt::ContractViolation);
}

// covers: utf16_length
TEST(utf16_length_sums_units) {
    CHECK_EQ(utf16_length(U""), std::size_t{0});
    CHECK_EQ(utf16_length(U"abc"), std::size_t{3});
    CHECK_EQ(utf16_length(std::u32string{0x1F680, U' ', 0x1F680}), std::size_t{5});
    CHECK_EQ(utf16_length(std::u32string{0, 0xFEFF}), std::size_t{2});
}

// covers: utf16_length
TEST(utf16_length_precondition_violation) {
    CHECK_THROWS(utf16_length(std::u32string{U'a', 0xD800}), msgfmt::ContractViolation);
    CHECK_THROWS(utf16_length(std::u32string{0x110000}), msgfmt::ContractViolation);
}
