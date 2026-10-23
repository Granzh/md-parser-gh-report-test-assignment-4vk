#pragma once

#include <istream>
#include <ostream>
#include <string>
#include <vector>

namespace msgfmt {

constexpr int kExitOk = 0;           ///< success
constexpr int kExitInvalidUtf8 = 2;  ///< input is not valid UTF-8 (fixed by the specification)
constexpr int kExitUsage = 64;       ///< unknown or extra arguments (EX_USAGE; not defined by the specification)
constexpr int kExitInternal = 70;    ///< unexpected failure, e.g. I/O error or out of memory (EX_SOFTWARE)

/**
 * @brief Reads a stream to its end without altering the bytes.
 * @param in input stream; consumed until EOF. Byte-exact only if the stream is in binary mode.
 * @return all bytes read (\r, NUL and surrounding whitespace preserved); empty for an empty stream.
 * @pre none.
 * @post in is at EOF (or failed).
 * @throws std::bad_alloc on allocation failure; stream exceptions if enabled via in.exceptions().
 */
std::string read_all(std::istream& in);

/**
 * @brief Gives the usage message printed on argument errors.
 * @return multi-line text mentioning both modes (parse and `escape`), ending with a newline.
 * @pre none.
 * @post result is non-empty.
 * @throws std::bad_alloc on allocation failure.
 */
std::string usage_text();

/**
 * @brief Switches the process's stdin and stdout to binary mode where the platform translates line endings.
 * @return nothing.
 * @pre none.
 * @post on Windows no \r\n translation happens on stdin/stdout; on other platforms nothing changes.
 * @throws does not throw.
 */
void set_binary_stdio();

/**
 * @brief Runs the command line: parse (no arguments) or `escape`.
 *
 * Reads all of `in` as bytes. Parse mode prints the JSON of the parse result, escape mode prints the escaped
 * text; neither adds a trailing newline. Invalid UTF-8 prints {"error":"invalid_utf8"} and returns 2. Wrong
 * arguments print the usage to `err`, leave `out` empty, do not read `in` and return kExitUsage.
 *
 * @param args arguments without the program name: {} or {"escape"}.
 * @param in input stream with the message bytes.
 * @param out output stream for the result.
 * @param err output stream for diagnostics.
 * @return kExitOk, kExitInvalidUtf8, kExitUsage, or kExitInternal if an exception occurred.
 * @pre none.
 * @post deterministic: output and code depend only on args and the input bytes.
 * @throws does not throw (exceptions are reported to `err` and mapped to kExitInternal).
 */
int run(const std::vector<std::string>& args, std::istream& in, std::ostream& out, std::ostream& err);

}  // namespace msgfmt
