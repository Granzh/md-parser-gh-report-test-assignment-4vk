#pragma once

// Minimal strict JSON syntax validator (RFC 8259) used by the tests to check the library's output.

#include <cstddef>
#include <optional>
#include <string>
#include <string_view>

#include "msgfmt/utf8.hpp"

namespace jsonv {

/// Recursive-descent scanner over one JSON text.
class Scanner {
public:
    /**
     * @brief Creates a scanner over a text.
     * @param text JSON text; must outlive the scanner.
     * @pre none.
     * @post the scanner is positioned at the start.
     * @throws does not throw.
     */
    explicit Scanner(std::string_view text) : text_(text) {}

    /**
     * @brief Checks that the whole text is exactly one JSON value surrounded by optional whitespace.
     * @return true iff the text is a syntactically valid JSON document.
     * @pre called at most once per scanner.
     * @post the scanner position is at the end on success.
     * @throws does not throw.
     */
    bool parse_document() {
        skip_ws();
        if (!parse_value(0)) {
            return false;
        }
        skip_ws();
        return pos_ == text_.size();
    }

private:
    /**
     * @brief Skips JSON whitespace (space, tab, CR, LF).
     * @return nothing.
     * @pre none.
     * @post pos_ points to a non-whitespace byte or the end.
     * @throws does not throw.
     */
    void skip_ws() {
        while (pos_ < text_.size() &&
               (text_[pos_] == ' ' || text_[pos_] == '\t' || text_[pos_] == '\r' || text_[pos_] == '\n')) {
            ++pos_;
        }
    }

    /**
     * @brief Consumes a fixed word such as `true`.
     * @param word literal to match.
     * @return true iff the text continues with `word`; the word is then consumed.
     * @pre none.
     * @post pos_ advanced by word.size() on success, unchanged otherwise.
     * @throws does not throw.
     */
    bool parse_literal(std::string_view word) {
        if (text_.substr(pos_, word.size()) != word) {
            return false;
        }
        pos_ += word.size();
        return true;
    }

    /**
     * @brief Consumes a run of ASCII digits.
     * @return the number of digits consumed.
     * @pre none.
     * @post pos_ points to a non-digit or the end.
     * @throws does not throw.
     */
    std::size_t parse_digits() {
        std::size_t n = 0;
        while (pos_ < text_.size() && text_[pos_] >= '0' && text_[pos_] <= '9') {
            ++pos_;
            ++n;
        }
        return n;
    }

    /**
     * @brief Consumes a JSON number: -?(0|[1-9][0-9]*)(.[0-9]+)?([eE][+-]?[0-9]+)?
     * @return true iff a well-formed number was consumed.
     * @pre none.
     * @post pos_ after the number on success.
     * @throws does not throw.
     */
    bool parse_number() {
        if (pos_ < text_.size() && text_[pos_] == '-') {
            ++pos_;
        }
        if (pos_ < text_.size() && text_[pos_] == '0') {
            ++pos_;
        } else if (parse_digits() == 0) {
            return false;
        }
        if (pos_ < text_.size() && text_[pos_] == '.') {
            ++pos_;
            if (parse_digits() == 0) {
                return false;
            }
        }
        if (pos_ < text_.size() && (text_[pos_] == 'e' || text_[pos_] == 'E')) {
            ++pos_;
            if (pos_ < text_.size() && (text_[pos_] == '+' || text_[pos_] == '-')) {
                ++pos_;
            }
            if (parse_digits() == 0) {
                return false;
            }
        }
        return true;
    }

    /**
     * @brief Consumes a JSON string literal: no raw control characters, only legal escapes.
     * @return true iff a well-formed string was consumed.
     * @pre text_[pos_] == '"'.
     * @post pos_ after the closing quote on success.
     * @throws does not throw.
     */
    bool parse_string() {
        ++pos_;
        while (pos_ < text_.size()) {
            const auto c = static_cast<unsigned char>(text_[pos_]);
            if (c == '"') {
                ++pos_;
                return true;
            }
            if (c < 0x20) {
                return false;
            }
            if (c == '\\') {
                ++pos_;
                if (pos_ >= text_.size()) {
                    return false;
                }
                const char e = text_[pos_];
                if (e == 'u') {
                    for (int i = 1; i <= 4; ++i) {
                        if (pos_ + i >= text_.size() || !is_hex(text_[pos_ + i])) {
                            return false;
                        }
                    }
                    pos_ += 4;
                } else if (std::string_view("\"\\/bfnrt").find(e) == std::string_view::npos) {
                    return false;
                }
            }
            ++pos_;
        }
        return false;
    }

    /**
     * @brief Tells whether a character is a hexadecimal digit.
     * @param c any character.
     * @return true iff c is 0-9, a-f or A-F.
     * @pre none.
     * @post none.
     * @throws does not throw.
     */
    static bool is_hex(char c) {
        return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F');
    }

    /**
     * @brief Consumes an array `[ value, ... ]`.
     * @param depth current nesting depth.
     * @return true iff a well-formed array was consumed.
     * @pre text_[pos_] == '['.
     * @post pos_ after the closing bracket on success.
     * @throws does not throw.
     */
    bool parse_array(int depth) {
        ++pos_;
        skip_ws();
        if (pos_ < text_.size() && text_[pos_] == ']') {
            ++pos_;
            return true;
        }
        while (true) {
            skip_ws();
            if (!parse_value(depth + 1)) {
                return false;
            }
            skip_ws();
            if (pos_ >= text_.size()) {
                return false;
            }
            if (text_[pos_] == ',') {
                ++pos_;
            } else if (text_[pos_] == ']') {
                ++pos_;
                return true;
            } else {
                return false;
            }
        }
    }

    /**
     * @brief Consumes an object `{ "key": value, ... }`.
     * @param depth current nesting depth.
     * @return true iff a well-formed object was consumed.
     * @pre text_[pos_] == '{'.
     * @post pos_ after the closing brace on success.
     * @throws does not throw.
     */
    bool parse_object(int depth) {
        ++pos_;
        skip_ws();
        if (pos_ < text_.size() && text_[pos_] == '}') {
            ++pos_;
            return true;
        }
        while (true) {
            skip_ws();
            if (pos_ >= text_.size() || text_[pos_] != '"' || !parse_string()) {
                return false;
            }
            skip_ws();
            if (pos_ >= text_.size() || text_[pos_] != ':') {
                return false;
            }
            ++pos_;
            skip_ws();
            if (!parse_value(depth + 1)) {
                return false;
            }
            skip_ws();
            if (pos_ >= text_.size()) {
                return false;
            }
            if (text_[pos_] == ',') {
                ++pos_;
            } else if (text_[pos_] == '}') {
                ++pos_;
                return true;
            } else {
                return false;
            }
        }
    }

    /**
     * @brief Consumes any JSON value.
     * @param depth current nesting depth; values nested deeper than 64 are rejected.
     * @return true iff a well-formed value was consumed.
     * @pre none.
     * @post pos_ after the value on success.
     * @throws does not throw.
     */
    bool parse_value(int depth) {
        if (depth > 64 || pos_ >= text_.size()) {
            return false;
        }
        const char c = text_[pos_];
        if (c == '{') {
            return parse_object(depth);
        }
        if (c == '[') {
            return parse_array(depth);
        }
        if (c == '"') {
            return parse_string();
        }
        if (c == 't') {
            return parse_literal("true");
        }
        if (c == 'f') {
            return parse_literal("false");
        }
        if (c == 'n') {
            return parse_literal("null");
        }
        return parse_number();
    }

    std::string_view text_;
    std::size_t pos_ = 0;
};

/**
 * @brief Tells whether a byte string is a valid JSON document in valid UTF-8.
 * @param text candidate document.
 * @return true iff `text` is well-formed UTF-8 and one syntactically valid JSON value (RFC 8259, nesting <= 64).
 * @pre none.
 * @post none.
 * @throws std::bad_alloc on allocation failure.
 */
inline bool is_valid_json(std::string_view text) {
    if (!msgfmt::decode_utf8(text)) {
        return false;
    }
    return Scanner(text).parse_document();
}

}  // namespace jsonv
