#include "msgfmt/cli.hpp"

#include <cstdio>
#include <exception>
#include <iterator>
#include <optional>

#include "msgfmt/escape.hpp"
#include "msgfmt/json.hpp"
#include "msgfmt/parser.hpp"

#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

namespace msgfmt {

std::string read_all(std::istream& in) {  // NOLINT(contract) documented in cli.hpp
    return std::string(std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>());
}

std::string usage_text() {  // NOLINT(contract) documented in cli.hpp
    return "usage: msgfmt            read markup from stdin, print JSON {\"text\", \"ranges\"}\n"
           "       msgfmt escape     read text from stdin, print it with service characters escaped\n"
           "exit codes: 0 ok, 2 invalid UTF-8, 64 usage error, 70 internal error\n";
}

void set_binary_stdio() {  // NOLINT(contract) documented in cli.hpp
#ifdef _WIN32
    _setmode(_fileno(stdin), _O_BINARY);
    _setmode(_fileno(stdout), _O_BINARY);
#endif
}

int run(const std::vector<std::string>& args, std::istream& in, std::ostream& out,  // NOLINT(contract)
        std::ostream& err) {
    try {
        const bool escape_mode = args.size() == 1 && args[0] == "escape";
        if (!args.empty() && !escape_mode) {
            err << usage_text();
            return kExitUsage;
        }
        const std::string bytes = read_all(in);
        std::string output;
        int code = kExitOk;
        if (escape_mode) {
            const std::optional<std::string> escaped = escape_utf8(bytes);
            if (escaped) {
                output = *escaped;
            } else {
                output = error_json("invalid_utf8");
                code = kExitInvalidUtf8;
            }
        } else {
            const std::optional<ParseResult> parsed = parse_utf8(bytes);
            if (parsed) {
                output = to_json(*parsed);
            } else {
                output = error_json("invalid_utf8");
                code = kExitInvalidUtf8;
            }
        }
        out.write(output.data(), static_cast<std::streamsize>(output.size()));
        out.flush();
        return code;
    } catch (const std::exception& e) {
        err << "msgfmt: internal error: " << e.what() << "\n";
        return kExitInternal;
    }
}

}  // namespace msgfmt
