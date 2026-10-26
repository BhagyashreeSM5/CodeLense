import pytest

from app.ingest import IngestError
from app.github import parse_repo_url


@pytest.mark.parametrize("url", [
    "https://github.com/psf/requests",
    "https://github.com/psf/requests/",
    "https://github.com/psf/requests.git",
    "github.com/psf/requests",
    "  https://www.github.com/psf/requests  ",
])
def test_accepts_common_github_urls(url):
    assert parse_repo_url(url) == ("psf", "requests")


@pytest.mark.parametrize("url", [
    "https://gitlab.com/psf/requests",
    "https://github.com/psf",
    "not a url",
    "https://github.com/psf/requests/tree/main/src",
])
def test_rejects_other_urls(url):
    with pytest.raises(IngestError):
        parse_repo_url(url)
