"""Classification of repositories by what the server's data allows a test to check."""

from dataclasses import dataclass, field

from helpers.oracle import eligible_items, parse_timestamp


@dataclass
class RepoProfile:
    """Facts about one repository derived from the oracle's view of the listing.

    Attributes:
        repo: ``owner/repo``.
        status: Status of the first page (200 for an existing repository).
        pages: Number of listing pages.
        items: Total items over all pages, duplicates and pull requests included.
        pull_requests: Items carrying the ``pull_request`` key.
        duplicates: Items repeating an earlier ``number``.
        eligible: Distinct non-PR issues.
        first_page_eligible: Eligible issues that appear on the first page.
        non_utc: Eligible issues whose created_at has a non-zero UTC offset.
        dates: Sorted distinct UTC dates (``YYYY-MM-DD``) of the eligible issues.
        labelled: Eligible issues with at least one label.
        non_ascii_titles: Eligible issues whose title contains non-ASCII characters.
    """

    repo: str
    status: int = 200
    pages: int = 0
    items: int = 0
    pull_requests: int = 0
    duplicates: int = 0
    eligible: int = 0
    first_page_eligible: int = 0
    non_utc: int = 0
    dates: list = field(default_factory=list)
    labelled: int = 0
    non_ascii_titles: int = 0

    @property
    def multi_page(self):
        """Tell whether the listing spans several pages.

        Returns:
            True when there are at least two pages.

        Raises:
            None.
        """
        return self.pages >= 2

    @property
    def has_pull_requests(self):
        """Tell whether the listing contains pull requests.

        Returns:
            True when at least one item carries ``pull_request``.

        Raises:
            None.
        """
        return self.pull_requests > 0


def profile_repo(repo, pages):
    """Summarise a listing for test selection.

    Args:
        repo: ``owner/repo``.
        pages: Page dicts from OracleClient.fetch_pages.

    Returns:
        RepoProfile; for a non-200 first page only ``repo`` and ``status`` are meaningful.

    Raises:
        ServerViolation: An item lacks number or created_at.
    """
    profile = RepoProfile(repo, pages[0]["status"] if pages else 0, len(pages))
    if profile.status != 200:
        return profile
    seen = set()
    for page in pages:
        for item in page["items"]:
            profile.items += 1
            if "pull_request" in item:
                profile.pull_requests += 1
            if item.get("number") in seen:
                profile.duplicates += 1
            seen.add(item.get("number"))
    chosen = eligible_items(pages)
    profile.eligible = len(chosen)
    first_numbers = {item.get("number") for item in pages[0]["items"] if "pull_request" not in item}
    profile.first_page_eligible = len([item for item in chosen if item["number"] in first_numbers])
    dates = set()
    for item in chosen:
        moment = parse_timestamp(item["created_at"])
        dates.add(moment.strftime("%Y-%m-%d"))
        if not item["created_at"].endswith(("Z", "z", "+00:00", "-00:00")):
            profile.non_utc += 1
        if item.get("labels"):
            profile.labelled += 1
        if any(ord(char) > 127 for char in str(item.get("title", ""))):
            profile.non_ascii_titles += 1
    profile.dates = sorted(dates)
    return profile


def pick_since(profile):
    """Choose a --since date that splits the eligible issues into "before" and "from".

    Args:
        profile: RepoProfile of the repository.

    Returns:
        ``YYYY-MM-DD`` (the median of the distinct creation dates, so earlier issues are excluded
        and later ones are kept), or None when fewer than two distinct dates exist.

    Raises:
        None.
    """
    if len(profile.dates) < 2:
        return None
    return profile.dates[len(profile.dates) // 2]


def pick_limit(profile):
    """Choose a --limit that forces the service to cross a page boundary.

    Args:
        profile: RepoProfile of the repository.

    Returns:
        ``first_page_eligible + 1`` when the repository has more eligible issues than that, else None.

    Raises:
        None.
    """
    wanted = profile.first_page_eligible + 1
    if profile.multi_page and profile.eligible >= wanted and profile.first_page_eligible > 0:
        return wanted
    return None


class ProfileCache:
    """Per-session cache of repository profiles, computed from the oracle on first use."""

    def __init__(self, oracle):
        """Create an empty cache.

        Args:
            oracle: OracleClient used to read listings.

        Returns:
            None.

        Raises:
            None.
        """
        self._oracle = oracle
        self._profiles = {}
        self._pages = {}

    def pages(self, repo):
        """Return the cached pages of a repository, fetching them once.

        Args:
            repo: ``owner/repo``.

        Returns:
            Page dicts from OracleClient.fetch_pages.

        Raises:
            ServerViolation: The server broke the contract while being read.
            OracleError: Network failure.
        """
        if repo not in self._pages:
            self._pages[repo] = self._oracle.fetch_pages(repo)
        return self._pages[repo]

    def get(self, repo):
        """Return the cached profile of a repository.

        Args:
            repo: ``owner/repo``.

        Returns:
            RepoProfile.

        Raises:
            ServerViolation: The server broke the contract while being read.
            OracleError: Network failure.
        """
        if repo not in self._profiles:
            self._profiles[repo] = profile_repo(repo, self.pages(repo))
        return self._profiles[repo]

    def find(self, repos, predicate):
        """Find the first repository whose profile satisfies a condition.

        Args:
            repos: Iterable of ``owner/repo``.
            predicate: Callable taking a RepoProfile and returning bool.

        Returns:
            The first matching RepoProfile in the order of ``repos``, or None.

        Raises:
            ServerViolation: The server broke the contract while being read.
            OracleError: Network failure.
        """
        for repo in repos:
            profile = self.get(repo)
            if profile.status == 200 and predicate(profile):
                return profile
        return None
