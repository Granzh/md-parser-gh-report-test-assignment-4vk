#include "test_framework.hpp"

/**
 * @brief Entry point shared by all test executables.
 * @param argc number of command-line arguments.
 * @param argv arguments; argv[1], if present, is a substring filter for test names.
 * @return 0 if all selected tests pass, 1 otherwise.
 * @pre argv has argc valid entries.
 * @post every selected test has been run once.
 * @throws does not throw (test exceptions are caught by the framework).
 */
int main(int argc, char** argv) {
    return tf::run_all(argc, argv);
}
