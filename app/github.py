"""Turn a GitHub URL into owner/repo and download the default branch as a zip."""
import re
import urllib.error
import urllib.request

from app.ingest import IngestError

MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
_REPO_URL = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)


def parse_repo_url(url: str) -> tuple[str, str]:
    match = _REPO_URL.match(url.strip())
    if not match:
        raise IngestError("expected a GitHub URL like https://github.com/owner/repo")
    return match.group(1), match.group(2)


def download_archive(owner: str, repo: str, timeout: int = 60) -> bytes:
    """Download the default branch. Works for public repositories only."""
    url = f"https://github.com/{owner}/{repo}/archive/HEAD.zip"
    request = urllib.request.Request(url, headers={"User-Agent": "codelens-v2"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(MAX_DOWNLOAD_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise IngestError("repository not found, or it is private") from exc
        raise IngestError(f"GitHub returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise IngestError(f"could not reach GitHub: {exc.reason}") from exc
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise IngestError("repository archive is larger than 50 MB")
    return data
