"""Fixtures of the selfcheck meta-tests: the suite's own fixtures are pointed at the stub server."""

import pytest

from helpers.env import Env
from stub_server import StubServer
from support import FAKE_BINARY


@pytest.fixture(scope="session")
def stub():
    """Compliant stub server shared by the whole selfcheck session.

    Returns:
        A running stub_server.StubServer without violation flags.

    Raises:
        None.
    """
    with StubServer() as server:
        yield server


@pytest.fixture(scope="session")
def env(stub):
    """Override of the suite's ``env`` fixture: the stub server and the fake binary.

    Args:
        stub: Running stub server.

    Returns:
        helpers.env.Env whose binary is the fake_ghreport.py command prefix.

    Raises:
        None.
    """
    return Env(FAKE_BINARY, stub.url, None)
