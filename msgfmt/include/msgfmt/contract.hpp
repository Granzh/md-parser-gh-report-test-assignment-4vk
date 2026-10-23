#pragma once

#include <stdexcept>
#include <string>

namespace msgfmt {

/// Thrown when a documented precondition of a function is violated.
class ContractViolation : public std::logic_error {
public:
    /**
     * @brief Creates the exception with a ready message.
     * @param what human-readable description; copied.
     * @pre none.
     * @post what() returns the given text.
     * @throws std::bad_alloc if the message cannot be copied.
     */
    explicit ContractViolation(const std::string& what) : std::logic_error(what) {}
};

namespace detail {

/**
 * @brief Throws ContractViolation describing a failed MSGFMT_EXPECTS check.
 * @param condition text of the violated condition; non-null, NUL-terminated.
 * @param file source file name; non-null, NUL-terminated.
 * @param line source line number.
 * @pre condition and file are not null.
 * @post never returns normally.
 * @throws ContractViolation always, with message "contract violation: <cond> (<file>:<line>)".
 */
[[noreturn]] inline void contract_failed(const char* condition, const char* file, int line) {
    throw ContractViolation(std::string("contract violation: ") + condition + " (" + file + ":" +
                            std::to_string(line) + ")");
}

}  // namespace detail
}  // namespace msgfmt

/// Checks a precondition in every build type (not removed by NDEBUG); throws ContractViolation.
#define MSGFMT_EXPECTS(cond) \
    do {                     \
        if (!(cond)) {       \
            ::msgfmt::detail::contract_failed(#cond, __FILE__, __LINE__); \
        }                    \
    } while (false)
