import os
import shutil
import subprocess
from pathlib import Path

import pytest

WRAPPER = Path(__file__).resolve().parents[1] / "ghreport-docker.sh"
SH = shutil.which("sh")

pytestmark = pytest.mark.skipif(SH is None, reason="no POSIX sh on PATH")


def run_wrapper(tmp_path, args, extra_env=None):
    """Run the wrapper with a fake ``docker`` that records its arguments, one per line.

    Args:
        tmp_path: Working directory of the run; also holds the fake docker and its log.
        args: Wrapper arguments.
        extra_env: Additional environment variables.

    Returns:
        ``(completed_process, recorded_argument_list, working_directory_as_seen_by_sh)``.

    Raises:
        None.
    """
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "docker.log"
    fake = bin_dir / "docker"
    fake.write_text('#!/bin/sh\nfor a in "$@"; do printf "%s\n" "$a" >> "$FAKE_DOCKER_LOG"; done\nexit 7\n', newline="\n")
    fake.chmod(0o755)
    env = dict(os.environ, FAKE_DOCKER_LOG=str(log), PATH=str(bin_dir) + os.pathsep + os.environ["PATH"])
    env.update(extra_env or {})
    done = subprocess.run([SH, str(WRAPPER), *args], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    cwd = subprocess.run([SH, "-c", "pwd"], cwd=tmp_path, capture_output=True, text=True, timeout=60).stdout.strip()
    recorded = log.read_text().splitlines() if log.exists() else []
    return done, recorded, cwd


# covers: ghreport-docker.sh
def test_wrapper_forwards_arguments_user_network_and_env_names(tmp_path):
    done, recorded, _ = run_wrapper(tmp_path, ["--repos", "a/b", "--out", "-"], {"GITHUB_TOKEN": "test-token-DO-NOT-USE"})
    assert done.returncode == 7  # the exit status of docker is the exit status of the wrapper
    assert recorded[:5] == ["run", "--rm", "-i", "--network", "host"]
    assert "--user" in recorded
    assert recorded[recorded.index("ghreport") + 1:] == ["--repos", "a/b", "--out", "-"]
    assert "-v" not in recorded
    assert recorded.count("-e") == 2 and "GITHUB_TOKEN" in recorded and "GITHUB_API_BASE_URL" in recorded
    assert "test-token-DO-NOT-USE" not in " ".join(recorded)  # only the variable NAME is passed


# covers: ghreport-docker.sh
def test_wrapper_mounts_cache_trace_and_out_directories_at_same_path(tmp_path):
    args = ["--repos", "a/b", "--cache-dir", "cache", "--trace=tr/trace.jsonl", "--out", "o/report.json"]
    _, recorded, cwd = run_wrapper(tmp_path, args)
    volumes = [recorded[i + 1] for i, item in enumerate(recorded) if item == "-v"]
    assert sorted(volumes) == sorted(f"{cwd}/{d}:{cwd}/{d}" for d in ("cache", "tr", "o"))
    for directory in ("cache", "tr", "o"):
        assert (tmp_path / directory).is_dir()
    assert recorded[recorded.index("ghreport") + 1:] == args


# covers: ghreport-docker.sh
def test_wrapper_mounts_a_shared_directory_once_and_honours_image_variable(tmp_path):
    args = ["--trace", "d/t.jsonl", "--out=d/r.json", "--cache-dir=c"]
    _, recorded, _ = run_wrapper(tmp_path, args, {"GHREPORT_IMAGE": "other:tag"})
    assert recorded.count("-v") == 2
    assert "other:tag" in recorded and "ghreport" not in recorded
