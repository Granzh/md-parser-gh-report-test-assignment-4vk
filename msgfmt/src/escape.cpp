#include "msgfmt/escape.hpp"

#include "msgfmt/utf8.hpp"

namespace msgfmt {

bool is_service_char(char32_t cp) noexcept {  // NOLINT(contract) documented in escape.hpp
    return cp == U'*' || cp == U'_' || cp == U'`' || cp == U'[' || cp == U']' || cp == U'(' || cp == U')' ||
           cp == U'\\';
}

std::u32string escape_text(std::u32string_view text) {  // NOLINT(contract) documented in escape.hpp
    std::u32string out;
    out.reserve(text.size());
    for (const char32_t cp : text) {
        if (is_service_char(cp)) {
            out.push_back(U'\\');
        }
        out.push_back(cp);
    }
    return out;
}

std::optional<std::string> escape_utf8(std::string_view bytes) {  // NOLINT(contract) documented in escape.hpp
    const std::optional<std::u32string> decoded = decode_utf8(bytes);
    if (!decoded) {
        return std::nullopt;
    }
    std::string out;
    out.reserve(bytes.size());
    for (const char32_t cp : escape_text(*decoded)) {
        encode_utf8_append(out, cp);
    }
    return out;
}

}  // namespace msgfmt
