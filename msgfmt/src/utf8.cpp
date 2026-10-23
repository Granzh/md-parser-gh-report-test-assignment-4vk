#include "msgfmt/utf8.hpp"

#include "msgfmt/contract.hpp"

namespace msgfmt {

bool is_scalar_value(char32_t cp) noexcept {  // NOLINT(contract) documented in utf8.hpp
    return cp <= 0x10FFFF && !(cp >= 0xD800 && cp <= 0xDFFF);
}

namespace detail {

std::optional<char32_t> decode_one(std::string_view bytes, std::size_t& pos) {  // NOLINT(contract) documented in utf8.hpp
    MSGFMT_EXPECTS(pos < bytes.size());
    const auto lead = static_cast<unsigned char>(bytes[pos]);
    if (lead < 0x80) {
        pos += 1;
        return static_cast<char32_t>(lead);
    }
    std::size_t length = 0;
    char32_t cp = 0;
    unsigned char second_lo = 0x80;  // allowed range of the 2nd byte; excludes overlongs/surrogates/>10FFFF
    unsigned char second_hi = 0xBF;
    if (lead >= 0xC2 && lead <= 0xDF) {
        length = 2;
        cp = lead & 0x1F;
    } else if (lead >= 0xE0 && lead <= 0xEF) {
        length = 3;
        cp = lead & 0x0F;
        if (lead == 0xE0) second_lo = 0xA0;  // else overlong
        if (lead == 0xED) second_hi = 0x9F;  // else surrogate
    } else if (lead >= 0xF0 && lead <= 0xF4) {
        length = 4;
        cp = lead & 0x07;
        if (lead == 0xF0) second_lo = 0x90;  // else overlong
        if (lead == 0xF4) second_hi = 0x8F;  // else > U+10FFFF
    } else {
        return std::nullopt;  // stray continuation, C0, C1, F5..FF
    }
    if (bytes.size() - pos < length) {
        return std::nullopt;
    }
    for (std::size_t i = 1; i < length; ++i) {
        const auto b = static_cast<unsigned char>(bytes[pos + i]);
        const unsigned char lo = i == 1 ? second_lo : 0x80;
        const unsigned char hi = i == 1 ? second_hi : 0xBF;
        if (b < lo || b > hi) {
            return std::nullopt;
        }
        cp = (cp << 6) | (b & 0x3F);
    }
    pos += length;
    return cp;
}

}  // namespace detail

std::optional<std::u32string> decode_utf8(std::string_view bytes) {  // NOLINT(contract) documented in utf8.hpp
    std::u32string result;
    result.reserve(bytes.size());
    std::size_t pos = 0;
    while (pos < bytes.size()) {
        const std::optional<char32_t> cp = detail::decode_one(bytes, pos);
        if (!cp) {
            return std::nullopt;
        }
        result.push_back(*cp);
    }
    return result;
}

void encode_utf8_append(std::string& out, char32_t cp) {  // NOLINT(contract) documented in utf8.hpp
    MSGFMT_EXPECTS(is_scalar_value(cp));
    if (cp < 0x80) {
        out.push_back(static_cast<char>(cp));
    } else if (cp < 0x800) {
        out.push_back(static_cast<char>(0xC0 | (cp >> 6)));
        out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
    } else if (cp < 0x10000) {
        out.push_back(static_cast<char>(0xE0 | (cp >> 12)));
        out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
    } else {
        out.push_back(static_cast<char>(0xF0 | (cp >> 18)));
        out.push_back(static_cast<char>(0x80 | ((cp >> 12) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
    }
}

std::size_t utf16_units(char32_t cp) {  // NOLINT(contract) documented in utf8.hpp
    MSGFMT_EXPECTS(is_scalar_value(cp));
    return cp >= 0x10000 ? 2 : 1;
}

std::size_t utf16_length(std::u32string_view s) {  // NOLINT(contract) documented in utf8.hpp
    std::size_t total = 0;
    for (const char32_t cp : s) {
        total += utf16_units(cp);
    }
    return total;
}

}  // namespace msgfmt
