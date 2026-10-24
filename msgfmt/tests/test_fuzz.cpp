// Deterministic fuzz/property tests: a hand-written generator with a fixed seed (std::mt19937_64),
// no external fuzzers. The seed is printed with every failure.

#include <cstddef>
#include <cstdint>
#include <optional>
#include <random>
#include <sstream>
#include <string>
#include <vector>

#include "json_validator.hpp"
#include "msgfmt/contract.hpp"
#include "msgfmt/escape.hpp"
#include "msgfmt/json.hpp"
#include "msgfmt/parser.hpp"
#include "msgfmt/utf8.hpp"
#include "parser_test_utils.hpp"

namespace {

constexpr std::uint64_t kSeed = 0x5EEDF00DCAFE2026ULL;
constexpr std::size_t kIterations = 20000;

/// Alphabet of the random generator: service characters, letters, digit, space, emoji, newline.
const std::u32string kAlphabet = {U'*', U'_', U'`', U'[', U']', U'(', U')', U'\\', U'a', 0x0431, U'1', U' ', 0x1F680, U'\n'};

/// Inputs of Appendix A (as code points) used as mutation bases.
const std::vector<std::u32string> kAppendixInputs = {
    U"**важно**",
    U"Привет, **мир**!",
    U"_курсив_",
    U"a_b_c",
    U"**не закрыто",
    U"2 \\* 3",
    U"`чат * список`",
    U"[документация](https://docs.github.com)",
    U"**жирный _и_ курсив**",
    U"**жирный _и курсив_**",
    std::u32string(U"\U0001F680 **ок**"),
    std::u32string(U"**\U0001F680 ок**"),
    U"[текст]()",
    U"\\\\",
    U"",
    U"_ е_",
};

}  // namespace

namespace fuzz {

/**
 * @brief Encodes a UTF-32 string of valid scalar values as UTF-8.
 * @param s code points; every element must be a Unicode scalar value.
 * @return the UTF-8 bytes.
 * @pre all elements of s are scalar values.
 * @post decoding the result gives s back.
 * @throws msgfmt::ContractViolation if an element is not a scalar value; std::bad_alloc on allocation failure.
 */
std::string to_utf8(const std::u32string& s) {
    std::string out;
    for (const char32_t cp : s) {
        msgfmt::encode_utf8_append(out, cp);
    }
    return out;
}

/**
 * @brief Generates a random string over kAlphabet.
 * @param rng random source; advanced.
 * @param max_len maximum length (inclusive).
 * @return a string of length 0..max_len; every code point is a scalar value from the alphabet.
 * @pre none.
 * @post result.size() <= max_len.
 * @throws std::bad_alloc on allocation failure.
 */
std::u32string random_string(std::mt19937_64& rng, std::size_t max_len) {
    const std::size_t len = std::uniform_int_distribution<std::size_t>(0, max_len)(rng);
    std::uniform_int_distribution<std::size_t> pick(0, kAlphabet.size() - 1);
    std::u32string s;
    for (std::size_t i = 0; i < len; ++i) {
        s.push_back(kAlphabet[pick(rng)]);
    }
    return s;
}

/**
 * @brief Applies 1-3 random edits (insert, delete, replace, duplicate a character, swap neighbours) to a string.
 * @param base string to mutate; copied.
 * @param rng random source; advanced.
 * @return the mutated string; every inserted character comes from kAlphabet.
 * @pre none.
 * @post the result contains only characters of base and kAlphabet.
 * @throws std::bad_alloc on allocation failure.
 */
std::u32string mutate(std::u32string base, std::mt19937_64& rng) {
    const int edits = std::uniform_int_distribution<int>(1, 3)(rng);
    for (int e = 0; e < edits; ++e) {
        const std::size_t pos = std::uniform_int_distribution<std::size_t>(0, base.size())(rng);
        const char32_t ch = kAlphabet[std::uniform_int_distribution<std::size_t>(0, kAlphabet.size() - 1)(rng)];
        switch (std::uniform_int_distribution<int>(0, 4)(rng)) {
            case 0:
                base.insert(base.begin() + static_cast<std::ptrdiff_t>(pos), ch);
                break;
            case 1:
                if (!base.empty()) {
                    base.erase(pos == base.size() ? pos - 1 : pos, 1);
                }
                break;
            case 2:
                if (!base.empty()) {
                    base[pos == base.size() ? pos - 1 : pos] = ch;
                }
                break;
            case 3:
                if (!base.empty()) {
                    const std::size_t at = pos == base.size() ? pos - 1 : pos;
                    base.insert(base.begin() + static_cast<std::ptrdiff_t>(at), base[at]);
                }
                break;
            default:
                if (base.size() >= 2) {
                    const std::size_t at = pos >= base.size() - 1 ? base.size() - 2 : pos;
                    std::swap(base[at], base[at + 1]);
                }
                break;
        }
    }
    return base;
}

/**
 * @brief Generates random bytes biased towards UTF-8 lead and continuation bytes.
 * @param rng random source; advanced.
 * @param max_len maximum length (inclusive).
 * @return 0..max_len bytes; may or may not be valid UTF-8.
 * @pre none.
 * @post result.size() <= max_len.
 * @throws std::bad_alloc on allocation failure.
 */
std::string random_bytes(std::mt19937_64& rng, std::size_t max_len) {
    static const unsigned char interesting[] = {0x00, 0x2A, 0x5F, 0x60, 0x5C, 0x61, 0x80, 0xBF, 0xC0, 0xC1, 0xC2,
                                                0xD0, 0xDF, 0xE0, 0xED, 0xEF, 0xF0, 0xF4, 0xF5, 0xFF, 0x9F, 0xA0};
    const std::size_t len = std::uniform_int_distribution<std::size_t>(0, max_len)(rng);
    std::string bytes;
    for (std::size_t i = 0; i < len; ++i) {
        if (std::uniform_int_distribution<int>(0, 2)(rng) == 0) {
            bytes.push_back(static_cast<char>(std::uniform_int_distribution<int>(0, 255)(rng)));
        } else {
            bytes.push_back(static_cast<char>(
                interesting[std::uniform_int_distribution<std::size_t>(0, sizeof(interesting) - 1)(rng)]));
        }
    }
    return bytes;
}

/**
 * @brief Checks every text-level property of one input.
 *
 * Properties: parse does not throw; parser invariants hold; plain text is not longer than the input (in code
 * points); to_json yields strictly valid JSON; parse(escape(s)) == (s, []); parse_utf8 agrees with parse.
 *
 * @param input code points; every element must be a scalar value.
 * @return empty string if all properties hold, otherwise a description of the first violated one.
 * @pre all elements of input are scalar values.
 * @post none.
 * @throws does not throw (exceptions of the code under test are reported in the result).
 */
std::string text_property_violation(const std::u32string& input) {
    try {
        const msgfmt::ParseResult r = msgfmt::parse(input);
        const std::vector<std::string> problems = ptest::find_invariant_violations(r);
        if (!problems.empty()) {
            return "invariant: " + problems.front() + " in " + ptest::describe(r);
        }
        const std::optional<std::u32string> plain = msgfmt::decode_utf8(r.text);
        if (!plain || plain->size() > input.size()) {
            return "plain text longer than input or undecodable";
        }
        if (!jsonv::is_valid_json(msgfmt::to_json(r))) {
            return "to_json is not valid JSON: " + msgfmt::to_json(r);
        }
        const msgfmt::ParseResult back = msgfmt::parse(msgfmt::escape_text(input));
        if (back.text != to_utf8(input) || !back.ranges.empty()) {
            return "round-trip parse(escape(s)) != (s, []): " + ptest::describe(back);
        }
        const std::optional<msgfmt::ParseResult> via_bytes = msgfmt::parse_utf8(to_utf8(input));
        if (!via_bytes || !(*via_bytes == r)) {
            return "parse_utf8 disagrees with parse";
        }
        return std::string();
    } catch (const std::exception& e) {
        return std::string("exception: ") + e.what();
    } catch (...) {
        return "non-standard exception";
    }
}

/**
 * @brief Checks every byte-level property of one byte string.
 *
 * Properties: parse_utf8 and decode_utf8 agree on validity; for valid input encode(decode(bytes)) == bytes and
 * the result satisfies the parser invariants and the JSON/escape properties; invalid input yields nullopt for
 * parse_utf8, escape_utf8 and a `{"error": "invalid_utf8"}` document.
 *
 * @param bytes arbitrary bytes.
 * @return empty string if all properties hold, otherwise a description of the first violated one.
 * @pre none.
 * @post none.
 * @throws does not throw (exceptions of the code under test are reported in the result).
 */
std::string byte_property_violation(const std::string& bytes) {
    try {
        const std::optional<std::u32string> decoded = msgfmt::decode_utf8(bytes);
        const std::optional<msgfmt::ParseResult> parsed = msgfmt::parse_utf8(bytes);
        const std::optional<std::string> escaped = msgfmt::escape_utf8(bytes);
        if (decoded.has_value() != parsed.has_value() || decoded.has_value() != escaped.has_value()) {
            return "decode_utf8, parse_utf8 and escape_utf8 disagree about validity";
        }
        if (!decoded) {
            return msgfmt::error_json("invalid_utf8") == "{\"error\": \"invalid_utf8\"}" ? std::string()
                                                                                       : "error_json changed";
        }
        if (to_utf8(*decoded) != bytes) {
            return "decode+encode does not give the original bytes";
        }
        return text_property_violation(*decoded);
    } catch (const std::exception& e) {
        return std::string("exception: ") + e.what();
    } catch (...) {
        return "non-standard exception";
    }
}

/**
 * @brief Reports a property violation together with the seed and the offending input.
 * @param what violation description from *_property_violation; empty means nothing to report.
 * @param iteration index of the iteration, for reproduction.
 * @param shown the offending input, as UTF-8 bytes or raw bytes.
 * @param file source file of the caller; non-null.
 * @param line source line of the caller.
 * @return true iff what is empty.
 * @pre file is not null.
 * @post one failure is recorded iff what is not empty.
 * @throws std::bad_alloc on allocation failure.
 */
bool report_violation(const std::string& what, std::size_t iteration, const std::string& shown, const char* file,
                      int line) {
    if (what.empty()) {
        return true;
    }
    tf::detail::report(file, line,
                       "fuzz violation (seed=" + std::to_string(kSeed) + ", iteration=" + std::to_string(iteration) +
                           ", input=\"" + shown + "\"): " + what);
    return false;
}

}  // namespace fuzz

// covers: to_utf8
TEST(fuzz_to_utf8_encodes_code_points) {
    CHECK_EQ(fuzz::to_utf8(U""), std::string());
    CHECK_EQ(fuzz::to_utf8(U"aб\U0001F680"), std::string("a\xD0\xB1\xF0\x9F\x9A\x80"));
    CHECK_THROWS(fuzz::to_utf8(std::u32string(1, 0xD800)), msgfmt::ContractViolation);
}

// covers: random_string
TEST(fuzz_random_string_respects_length_and_alphabet) {
    std::mt19937_64 rng(kSeed);
    bool saw_empty = false;
    bool saw_max = false;
    for (int i = 0; i < 2000; ++i) {
        const std::u32string s = fuzz::random_string(rng, 40);
        CHECK(s.size() <= 40);
        saw_empty = saw_empty || s.empty();
        saw_max = saw_max || s.size() == 40;
        for (const char32_t cp : s) {
            CHECK(kAlphabet.find(cp) != std::u32string::npos);
        }
    }
    CHECK(saw_empty);
    CHECK(saw_max);
    CHECK_EQ(fuzz::random_string(rng, 0), std::u32string());
}

// covers: random_string
TEST(fuzz_generator_is_deterministic_for_a_fixed_seed) {
    std::mt19937_64 a(kSeed);
    std::mt19937_64 b(kSeed);
    for (int i = 0; i < 100; ++i) {
        CHECK(fuzz::random_string(a, 40) == fuzz::random_string(b, 40));
    }
}

// covers: mutate
TEST(fuzz_mutate_changes_inputs_and_keeps_alphabet) {
    std::mt19937_64 rng(kSeed);
    std::size_t changed = 0;
    for (int i = 0; i < 500; ++i) {
        const std::u32string base = U"**a _b_ c**";
        const std::u32string m = fuzz::mutate(base, rng);
        changed += m != base ? 1 : 0;
        for (const char32_t cp : m) {
            CHECK(base.find(cp) != std::u32string::npos || kAlphabet.find(cp) != std::u32string::npos);
        }
    }
    CHECK(changed > 250);
    // Boundary: empty and single-character bases must not crash.
    for (int i = 0; i < 200; ++i) {
        (void)fuzz::mutate(U"", rng);
        (void)fuzz::mutate(U"x", rng);
    }
}

// covers: random_bytes
TEST(fuzz_random_bytes_respects_length_and_covers_both_validities) {
    std::mt19937_64 rng(kSeed);
    std::size_t valid = 0;
    std::size_t invalid = 0;
    for (int i = 0; i < 2000; ++i) {
        const std::string b = fuzz::random_bytes(rng, 12);
        CHECK(b.size() <= 12);
        (msgfmt::decode_utf8(b) ? valid : invalid)++;
    }
    CHECK(valid > 50);
    CHECK(invalid > 50);
    CHECK_EQ(fuzz::random_bytes(rng, 0), std::string());
}

// covers: text_property_violation
TEST(fuzz_text_property_accepts_appendix_inputs_and_edge_cases) {
    for (const std::u32string& s : kAppendixInputs) {
        CHECK_EQ(fuzz::text_property_violation(s), std::string());
    }
    CHECK_EQ(fuzz::text_property_violation(std::u32string(1, U'\\')), std::string());
    CHECK_EQ(fuzz::text_property_violation(std::u32string(40, U'*')), std::string());
    CHECK_EQ(fuzz::text_property_violation(std::u32string(1, 0)), std::string());
}

// covers: text_property_violation
TEST(fuzz_text_property_reports_a_non_scalar_input_as_a_violation) {
    // A lone surrogate is outside the documented precondition: the to_utf8 contract check surfaces as a report.
    CHECK(!fuzz::text_property_violation(std::u32string(1, 0xD800)).empty());
}

// covers: byte_property_violation
TEST(fuzz_byte_property_accepts_valid_invalid_and_empty_bytes) {
    CHECK_EQ(fuzz::byte_property_violation(""), std::string());
    CHECK_EQ(fuzz::byte_property_violation("**a**"), std::string());
    CHECK_EQ(fuzz::byte_property_violation("\xF0\x9F\x9A\x80"), std::string());
    CHECK_EQ(fuzz::byte_property_violation("\xC0\x80"), std::string());
    CHECK_EQ(fuzz::byte_property_violation("\xED\xA0\x80"), std::string());
    CHECK_EQ(fuzz::byte_property_violation("\xFF"), std::string());
    CHECK_EQ(fuzz::byte_property_violation(std::string("a\0b", 3)), std::string());
}

// covers: report_violation
TEST(fuzz_report_violation_prints_seed_and_input) {
    std::ostringstream sink;
    bool ok = true;
    const int failures = tf::run_case(
        [] {
            CHECK(fuzz::report_violation(std::string(), 1, "x", __FILE__, __LINE__));
            CHECK(!fuzz::report_violation("boom", 7, "in", __FILE__, __LINE__));
        },
        sink);
    CHECK_EQ(failures, 1);
    const std::string text = sink.str();
    ok = text.find("seed=" + std::to_string(kSeed)) != std::string::npos &&
         text.find("iteration=7") != std::string::npos && text.find("input=\"in\"") != std::string::npos &&
         text.find("boom") != std::string::npos;
    CHECK(ok);
}

// covers: parse, parse_utf8, to_json, escape_text, decode_utf8, encode_utf8_append
TEST(fuzz_random_strings_over_service_alphabet) {
    std::mt19937_64 rng(kSeed);
    std::size_t bad = 0;
    for (std::size_t i = 0; i < kIterations && bad < 5; ++i) {
        const std::u32string s = fuzz::random_string(rng, 40);
        bad += fuzz::report_violation(fuzz::text_property_violation(s), i, fuzz::to_utf8(s), __FILE__, __LINE__) ? 0 : 1;
    }
    CHECK_EQ(bad, std::size_t{0});
}

// covers: parse, parse_utf8, to_json, escape_text
TEST(fuzz_mutations_of_appendix_a_examples) {
    std::mt19937_64 rng(kSeed + 1);
    std::size_t bad = 0;
    for (std::size_t i = 0; i < kIterations && bad < 5; ++i) {
        const std::u32string& base = kAppendixInputs[i % kAppendixInputs.size()];
        const std::u32string s = fuzz::mutate(base, rng);
        bad += fuzz::report_violation(fuzz::text_property_violation(s), i, fuzz::to_utf8(s), __FILE__, __LINE__) ? 0 : 1;
    }
    CHECK_EQ(bad, std::size_t{0});
}

// covers: parse_utf8, decode_utf8, encode_utf8_append, escape_utf8, error_json
TEST(fuzz_random_bytes_through_utf8_layer) {
    std::mt19937_64 rng(kSeed + 2);
    std::size_t bad = 0;
    for (std::size_t i = 0; i < kIterations && bad < 5; ++i) {
        const std::string b = fuzz::random_bytes(rng, 16);
        bad += fuzz::report_violation(fuzz::byte_property_violation(b), i, b, __FILE__, __LINE__) ? 0 : 1;
    }
    CHECK_EQ(bad, std::size_t{0});
}

// covers: parse_utf8, decode_utf8
TEST(fuzz_valid_utf8_with_one_corrupted_byte) {
    std::mt19937_64 rng(kSeed + 3);
    std::size_t bad = 0;
    for (std::size_t i = 0; i < kIterations && bad < 5; ++i) {
        std::string b = fuzz::to_utf8(fuzz::random_string(rng, 20));
        if (!b.empty()) {
            b[std::uniform_int_distribution<std::size_t>(0, b.size() - 1)(rng)] =
                static_cast<char>(std::uniform_int_distribution<int>(0, 255)(rng));
        }
        bad += fuzz::report_violation(fuzz::byte_property_violation(b), i, b, __FILE__, __LINE__) ? 0 : 1;
    }
    CHECK_EQ(bad, std::size_t{0});
}
