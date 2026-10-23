#include <iostream>
#include <string>
#include <vector>

#include "msgfmt/cli.hpp"

/**
 * @brief Process entry point: a thin wrapper over msgfmt::run on the standard streams.
 * @param argc number of command-line arguments.
 * @param argv arguments; argv[0] is the program name and is not passed on.
 * @return the exit code of msgfmt::run.
 * @pre argv has argc valid entries.
 * @post stdin and stdout are in binary mode on Windows.
 * @throws does not throw.
 */
int main(int argc, char** argv) {
    msgfmt::set_binary_stdio();
    const std::vector<std::string> args(argv + 1, argv + argc);
    return msgfmt::run(args, std::cin, std::cout, std::cerr);
}
