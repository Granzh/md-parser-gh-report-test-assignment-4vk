"""Stand-in for the ghreport binary used to test the runner without the real service.

Behaviour is chosen by the FAKE_MODE environment variable: ``echo`` (default) prints the received
arguments and selected environment variables as JSON and exits with FAKE_EXIT; ``hang`` blocks
until it is killed.
"""

import json
import os
import sys
import threading


def main(argv):
    """Run the fake service.

    Args:
        argv: Command-line arguments without the program name.

    Returns:
        Exit code requested by FAKE_EXIT (default 0).

    Raises:
        None.
    """
    if os.environ.get("FAKE_MODE") == "hang":
        threading.Event().wait(600)
        return 0
    seen = {"argv": argv,
            "base_url": os.environ.get("GITHUB_API_BASE_URL"),
            "token": os.environ.get("GITHUB_TOKEN"),
            "extra": os.environ.get("FAKE_EXTRA")}
    sys.stdout.buffer.write(json.dumps(seen, ensure_ascii=False).encode("utf-8"))
    sys.stderr.buffer.write("диагностика".encode("utf-8"))
    if "--trace" in argv:
        with open(argv[argv.index("--trace") + 1], "w", encoding="utf-8") as handle:
            handle.write('{"status": 200}\n\n{"status": 304}\n')
    return int(os.environ.get("FAKE_EXIT", "0"))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
