#include <string>

#include "json_validator.hpp"
#include "test_framework.hpp"

using jsonv::is_valid_json;

// covers: is_valid_json, parse_document, parse_value, skip_ws
TEST(validator_accepts_valid_documents) {
    CHECK(is_valid_json("{}"));
    CHECK(is_valid_json("[]"));
    CHECK(is_valid_json(" \t\r\n{ \"a\" : [ 1 , 2 ] } \n"));
    CHECK(is_valid_json("\"x\""));
    CHECK(is_valid_json("0"));
    CHECK(is_valid_json("true"));
    CHECK(is_valid_json("null"));
    CHECK(is_valid_json("{\"text\":\"\",\"ranges\":[]}"));
    CHECK(is_valid_json("{\"a\":{\"b\":[{\"c\":null},false]}}"));
}

// covers: parse_number
TEST(validator_numbers) {
    for (const char* ok : {"0", "-0", "7", "-12", "1.5", "0.25", "1e5", "1E+5", "2.5e-3", "10"}) {
        CHECK(is_valid_json(ok));
    }
    for (const char* bad : {"01", "1.", ".5", "-", "+1", "1e", "1e+", "--1", "0x10", "1.e5", "NaN"}) {
        CHECK(!is_valid_json(bad));
    }
}

// covers: parse_string, is_hex
TEST(validator_strings_and_escapes) {
    CHECK(is_valid_json("\"a\\n\\t\\\"\\\\\\/\\b\\f\\r\""));
    CHECK(is_valid_json("\"\\u00e9\\u00E9\\u0000\""));
    CHECK(is_valid_json("\"\xD0\x9F\xD1\x80\xD0\xB8\""));
    CHECK(!is_valid_json("\"unterminated"));
    CHECK(!is_valid_json("\"raw\nnewline\""));
    CHECK(!is_valid_json(std::string("\"nul\0\"", 6)));
    CHECK(!is_valid_json("\"bad \\x escape\""));
    CHECK(!is_valid_json("\"\\u12G4\""));
    CHECK(!is_valid_json("\"\\u12\""));
    CHECK(!is_valid_json("\"trailing backslash\\"));
    CHECK(!is_valid_json("'single'"));
}

// covers: is_valid_json
TEST(validator_rejects_invalid_utf8) {
    CHECK(!is_valid_json("\"\xFF\""));
    CHECK(!is_valid_json("\"\xC0\x80\""));
}

// covers: parse_object
TEST(validator_objects) {
    CHECK(!is_valid_json("{"));
    CHECK(!is_valid_json("{\"a\"}"));
    CHECK(!is_valid_json("{\"a\":}"));
    CHECK(!is_valid_json("{\"a\":1,}"));
    CHECK(!is_valid_json("{a:1}"));
    CHECK(!is_valid_json("{\"a\":1 \"b\":2}"));
    CHECK(!is_valid_json("{1:1}"));
    CHECK(!is_valid_json("{\"a\" 1}"));
}

// covers: parse_array
TEST(validator_arrays) {
    CHECK(!is_valid_json("["));
    CHECK(!is_valid_json("[1,]"));
    CHECK(!is_valid_json("[,1]"));
    CHECK(!is_valid_json("[1 2]"));
    CHECK(!is_valid_json("[1"));
}

// covers: parse_literal
TEST(validator_literals) {
    CHECK(!is_valid_json("tru"));
    CHECK(!is_valid_json("True"));
    CHECK(!is_valid_json("nul"));
    CHECK(!is_valid_json("falsey"));
}

// covers: parse_document
TEST(validator_document_level_errors) {
    CHECK(!is_valid_json(""));
    CHECK(!is_valid_json("   "));
    CHECK(!is_valid_json("{} {}"));
    CHECK(!is_valid_json("{}x"));
    CHECK(!is_valid_json("1 2"));
}

// covers: parse_value
TEST(validator_depth_limit) {
    const std::string deep_ok = std::string(60, '[') + std::string(60, ']');
    CHECK(is_valid_json(deep_ok));
    const std::string too_deep = std::string(100, '[') + std::string(100, ']');
    CHECK(!is_valid_json(too_deep));
}

// covers: parse_digits
TEST(validator_digit_runs) {
    CHECK(is_valid_json("1234567890"));
    CHECK(!is_valid_json("a"));
}

// covers: Scanner
TEST(scanner_can_be_used_directly) {
    jsonv::Scanner ok("[1]");
    CHECK(ok.parse_document());
    jsonv::Scanner bad("[1");
    CHECK(!bad.parse_document());
}
