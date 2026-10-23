#include "msgfmt/json.hpp"

#include <cstddef>

namespace msgfmt {

std::string json_escape(std::string_view utf8) {  // NOLINT(contract) documented in json.hpp
    static const char kHex[] = "0123456789abcdef";
    std::string out;
    out.reserve(utf8.size());
    for (const char ch : utf8) {
        const auto byte = static_cast<unsigned char>(ch);
        switch (byte) {
            case '"':
                out += "\\\"";
                break;
            case '\\':
                out += "\\\\";
                break;
            case '\b':
                out += "\\b";
                break;
            case '\f':
                out += "\\f";
                break;
            case '\n':
                out += "\\n";
                break;
            case '\r':
                out += "\\r";
                break;
            case '\t':
                out += "\\t";
                break;
            default:
                if (byte < 0x20) {
                    out += "\\u00";
                    out.push_back(kHex[byte >> 4]);
                    out.push_back(kHex[byte & 0xF]);
                } else {
                    out.push_back(ch);
                }
        }
    }
    return out;
}

std::string to_json(const ParseResult& result) {  // NOLINT(contract) documented in json.hpp
    std::string out = "{\"text\":\"" + json_escape(result.text) + "\",\"ranges\":[";
    for (std::size_t i = 0; i < result.ranges.size(); ++i) {
        const Range& r = result.ranges[i];
        if (i > 0) {
            out += ",";
        }
        out += "{\"style\":\"";
        out += style_name(r.style);
        out += "\",\"offset\":" + std::to_string(r.offset) + ",\"length\":" + std::to_string(r.length) +
               ",\"url\":\"" + json_escape(r.url) + "\"}";
    }
    out += "]}";
    return out;
}

std::string error_json(std::string_view code) {  // NOLINT(contract) documented in json.hpp
    return "{\"error\":\"" + json_escape(code) + "\"}";
}

}  // namespace msgfmt
