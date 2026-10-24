"""Probes of the server under test: a failure here means the server, not ghreport, is at fault."""

from helpers import probes


# covers: check_server_contract
def test_server_honours_the_listing_contract(oracle, test_repos):
    found = []
    for repo in test_repos:
        found += probes.check_server_contract(oracle, repo)
    assert not found, "\n".join(found)


# covers: probe_unconditional_not_304
def test_server_never_answers_304_without_if_none_match(oracle, test_repos):
    found = []
    for repo in test_repos:
        found += probes.probe_unconditional_not_304(oracle, repo)
    assert not found, "\n".join(found)
