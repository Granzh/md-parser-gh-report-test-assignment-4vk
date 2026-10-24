"""Process runner for the ghreport binary: the only place that spawns the service."""

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

#: Marker meaning "keep GITHUB_TOKEN from the environment unchanged".
KEEP_TOKEN = object()

DEFAULT_TIMEOUT = 60.0


class RunnerError(AssertionError):
    """Raised when the service cannot be run as requested (missing binary, base URL, timeout)."""


class RunnerTimeout(RunnerError):
    """Raised when the service did not finish within the timeout; carries partial output."""

    def __init__(self, message, stdout="", stderr=""):
        """Create the error.

        Args:
            message: Human-readable description.
            stdout: Standard output captured before the kill (text).
            stderr: Standard error captured before the kill (text).

        Returns:
            None.

        Raises:
            None.
        """
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


@dataclass
class Result:
    """Outcome of one service run.

    Attributes:
        returncode: Process exit code.
        stdout: Captured standard output (UTF-8 text).
        stderr: Captured standard error (UTF-8 text).
        elapsed: Wall-clock seconds the process ran.
    """

    returncode: int
    stdout: str
    stderr: str
    elapsed: float


def default_timeout(environ=None):
    """Return the per-run timeout in seconds.

    Args:
        environ: Mapping to read GHREPORT_TIMEOUT from; defaults to os.environ.

    Returns:
        Positive float: GHREPORT_TIMEOUT when it parses as a positive number, else 60.

    Raises:
        None.
    """
    source = os.environ if environ is None else environ
    raw = source.get("GHREPORT_TIMEOUT")
    if raw:
        try:
            value = float(raw)
        except ValueError:
            return DEFAULT_TIMEOUT
        if value > 0:
            return value
    return DEFAULT_TIMEOUT


def build_env(base_url=None, token=KEEP_TOKEN, env_extra=None, environ=None):
    """Build the environment of the service process.

    Args:
        base_url: Value for GITHUB_API_BASE_URL; None keeps the value from ``environ``.
        token: KEEP_TOKEN keeps GITHUB_TOKEN from ``environ``, None removes it, a string sets it.
        env_extra: Optional mapping applied last; a None value removes the variable.
        environ: Base mapping; defaults to os.environ (it is copied, never modified).

    Returns:
        New dict suitable for ``subprocess.run(env=...)``.

    Raises:
        RunnerError: No base URL is available; without it the service would talk to the public
            GitHub, which the suite must never do.
    """
    env = dict(os.environ if environ is None else environ)
    if base_url is not None:
        env["GITHUB_API_BASE_URL"] = base_url
    if not env.get("GITHUB_API_BASE_URL"):
        raise RunnerError("GITHUB_API_BASE_URL is not set: refusing to run the service against the default API")
    if token is None:
        env.pop("GITHUB_TOKEN", None)
    elif token is not KEEP_TOKEN:
        env["GITHUB_TOKEN"] = token
    for name, value in (env_extra or {}).items():
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value
    return env


def build_command(binary, repos, cache_dir, args, trace=None):
    """Assemble the command line of one run.

    Args:
        binary: Path to the executable, or a sequence (executable + leading arguments).
        repos: Value of --repos (comma-separated owner/repo list).
        cache_dir: Value of --cache-dir (path-like or str).
        args: Extra command-line arguments (sequence of str); "--out" inside it replaces the default.
        trace: Optional path for --trace.

    Returns:
        List of str: ``[binary..., --repos, repos, (--out -), --cache-dir, dir, (--trace, path), *args]``.

    Raises:
        None.
    """
    prefix = [str(binary)] if isinstance(binary, (str, os.PathLike)) else [str(part) for part in binary]
    extra = [str(item) for item in args]
    command = prefix + ["--repos", repos]
    if "--out" not in extra:
        command += ["--out", "-"]
    command += ["--cache-dir", str(cache_dir)]
    if trace is not None:
        command += ["--trace", str(trace)]
    return command + extra


def run_ghreport(repos, cache_dir, *args, env_extra=None, base_url=None, token=KEEP_TOKEN,
                 trace=None, timeout=None, binary=None, cwd=None):
    """Run the service once and capture everything it produced.

    Args:
        repos: Value of --repos (str).
        cache_dir: Cache directory (path-like or str); passed as --cache-dir.
        args: Extra command-line arguments; pass "--out", <path> to avoid the default "--out -".
        env_extra: Extra environment variables (None value removes a variable).
        base_url: GITHUB_API_BASE_URL for the run; None keeps the environment value.
        token: KEEP_TOKEN (default) keeps GITHUB_TOKEN, None removes it, a str sets it.
        trace: Optional path for --trace.
        timeout: Seconds before the process is killed; None means GHREPORT_TIMEOUT or 60.
        binary: Executable (or command prefix) to run; None means GHREPORT_BIN.
        cwd: Working directory of the process; None inherits the caller's.

    Returns:
        Result with exit code, stdout, stderr (decoded as UTF-8) and elapsed seconds.

    Raises:
        RunnerError: No binary or base URL is configured.
        RunnerTimeout: The process did not finish in time (it is killed first).
    """
    command = build_command(_binary_or_env(binary), repos, cache_dir, args, trace)
    return _execute(command, base_url, token, env_extra, timeout, cwd)


def run_raw(argv, base_url=None, token=KEEP_TOKEN, env_extra=None, timeout=None, binary=None, cwd=None):
    """Run the service with a verbatim argument list (for argument-validation tests).

    Args:
        argv: Arguments after the executable, passed unchanged (no --repos/--out are added).
        base_url: GITHUB_API_BASE_URL for the run; None keeps the environment value.
        token: KEEP_TOKEN (default) keeps GITHUB_TOKEN, None removes it, a str sets it.
        env_extra: Extra environment variables (None value removes a variable).
        timeout: Seconds before the process is killed; None means GHREPORT_TIMEOUT or 60.
        binary: Executable (or command prefix) to run; None means GHREPORT_BIN.
        cwd: Working directory of the process; None inherits the caller's.

    Returns:
        Result with exit code, stdout, stderr and elapsed seconds.

    Raises:
        RunnerError: No binary or base URL is configured.
        RunnerTimeout: The process did not finish in time (it is killed first).
    """
    prefix = _binary_or_env(binary)
    prefix = [str(prefix)] if isinstance(prefix, (str, os.PathLike)) else [str(part) for part in prefix]
    return _execute(prefix + [str(item) for item in argv], base_url, token, env_extra, timeout, cwd)


def _binary_or_env(binary):
    """Resolve the executable to run.

    Args:
        binary: Explicit executable/command prefix, or None.

    Returns:
        ``binary`` or the value of GHREPORT_BIN.

    Raises:
        RunnerError: Neither is available.
    """
    if binary is None:
        binary = os.environ.get("GHREPORT_BIN")
        if not binary:
            raise RunnerError("GHREPORT_BIN is not set")
    return binary


def _execute(command, base_url, token, env_extra, timeout, cwd=None):
    """Run a prepared command line with a timeout and capture its output.

    Args:
        command: Complete argument vector.
        base_url: GITHUB_API_BASE_URL for the run, or None.
        token: KEEP_TOKEN, None or a token string.
        env_extra: Extra environment variables.
        timeout: Seconds before the kill, or None for the default.
        cwd: Working directory of the process, or None.

    Returns:
        Result with exit code, decoded output and elapsed seconds.

    Raises:
        RunnerError: No base URL is configured.
        RunnerTimeout: The process did not finish in time (it is killed first).
    """
    limit = default_timeout() if timeout is None else timeout
    env = build_env(base_url, token, env_extra)
    started = time.monotonic()
    try:
        completed = subprocess.run(command, capture_output=True, env=env, timeout=limit,
                                   stdin=subprocess.DEVNULL, cwd=cwd)
    except subprocess.TimeoutExpired as error:
        raise RunnerTimeout(
            f"ghreport did not finish within {limit} s: {command}",
            _decode(error.stdout), _decode(error.stderr),
        ) from None
    return Result(completed.returncode, _decode(completed.stdout), _decode(completed.stderr),
                  time.monotonic() - started)


def _decode(data):
    """Decode captured process output.

    Args:
        data: bytes, str or None.

    Returns:
        UTF-8 text with undecodable bytes replaced; empty string for None.

    Raises:
        None.
    """
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    return data.decode("utf-8", errors="replace")


def report_of(result, allowed=(0,)):
    """Parse the JSON report from a finished run.

    Args:
        result: Result returned by run_ghreport.
        allowed: Exit codes accepted as "a report was produced" (default only 0; use (0, 1) for partial failures).

    Returns:
        The decoded JSON object from stdout.

    Raises:
        AssertionError: The exit code is not allowed (message includes stderr) or stdout is not valid JSON.
    """
    assert result.returncode in allowed, (
        f"exit code {result.returncode} not in {tuple(allowed)}; stderr: {result.stderr}")
    try:
        return json.loads(result.stdout)
    except ValueError as error:
        raise AssertionError(f"stdout is not valid JSON ({error}): {result.stdout[:200]!r}") from None


def trace_of(path):
    """Read a --trace file (JSON Lines).

    Args:
        path: Path-like or str of the trace file.

    Returns:
        List of dicts, one per non-empty line, in file order.

    Raises:
        FileNotFoundError: The file does not exist.
        AssertionError: A line is not a JSON object.
    """
    records = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError as error:
            raise AssertionError(f"trace line {number} is not JSON ({error}): {line[:200]!r}") from None
        assert isinstance(record, dict), f"trace line {number} is not an object: {line[:200]!r}"
        records.append(record)
    return records
