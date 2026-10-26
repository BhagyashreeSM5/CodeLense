import io
import zipfile

from fastapi.testclient import TestClient

from app import github
from app.ingest import IngestError
from app.main import app

client = TestClient(app)


def make_archive(files: dict[str, str]) -> bytes:
    """Build a zip shaped like a GitHub archive: one top-level 'repo-main/' folder."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_submit_github_url_then_browse_symbols(monkeypatch):
    archive = make_archive({"demo-main/app/util.py": "def add(a, b):\n    return a + b\n"})
    monkeypatch.setattr(github, "download_archive", lambda owner, repo: archive)

    resp = client.post("/repos", json={"url": "https://github.com/acme/demo", "force_refresh": True})
    assert resp.status_code in (200, 202)
    repo_id = resp.json()["id"]

    # TestClient runs background tasks before returning, so the repo is finished here.
    repo = client.get(f"/repos/{repo_id}").json()
    assert repo["status"] == "done"
    assert repo["name"] == "acme/demo"
    assert repo["file_count"] == 1

    files = client.get(f"/repos/{repo_id}/files").json()
    assert files[0]["path"] == "app/util.py"

    symbols = client.get(f"/repos/{repo_id}/files/app/util.py/symbols").json()
    assert symbols["symbols"][0]["name"] == "add"
    assert symbols["symbols"][0]["kind"] == "function"


def test_download_failure_marks_repo_failed(monkeypatch):
    def fail(owner, repo):
        raise IngestError("repository not found, or it is private")

    monkeypatch.setattr(github, "download_archive", fail)
    repo_id = client.post("/repos", json={"url": "https://github.com/acme/private"}).json()["id"]
    repo = client.get(f"/repos/{repo_id}").json()
    assert repo["status"] == "failed"
    assert "private" in repo["error"]


def test_invalid_url_returns_400():
    resp = client.post("/repos", json={"url": "https://example.com/nope"})
    assert resp.status_code == 400


def test_index_page_is_served():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "CodeLens" in resp.text


def test_unknown_repo_returns_404():
    assert client.get("/repos/doesnotexist").status_code == 404


def test_file_details_and_not_found(monkeypatch):
    archive = make_archive({"demo-main/calc.py": "def add(a, b):\n    return a + b\n"})
    monkeypatch.setattr(github, "download_archive", lambda owner, repo: archive)

    resp = client.post("/repos", json={"url": "https://github.com/acme/calc", "force_refresh": True})
    assert resp.status_code in (200, 202)
    repo_id = resp.json()["id"]

    details = client.get(f"/repos/{repo_id}/files/calc.py/details").json()
    assert details["name"] == "calc.py"
    assert "add" in [p["name"] for p in details["key_parts"]]
    assert client.get(f"/repos/{repo_id}/files/missing.py/details").status_code == 404


def test_submit_existing_repo_reuses_cloud_analysis(monkeypatch):
    archive = make_archive({"reuse-main/reuse.py": "def test_fn(): pass\n"})
    monkeypatch.setattr(github, "download_archive", lambda owner, repo: archive)

    resp1 = client.post("/repos", json={"url": "https://github.com/acme/reuse-test", "force_refresh": True})
    assert resp1.status_code in (200, 202)
    repo_id_1 = resp1.json()["id"]

    called = False
    def should_not_be_called(owner, repo):
        nonlocal called
        called = True
        return archive

    monkeypatch.setattr(github, "download_archive", should_not_be_called)
    resp2 = client.post("/repos", json={"url": "https://github.com/acme/reuse-test"})
    assert resp2.status_code == 200
    assert resp2.json()["id"] == repo_id_1
    assert resp2.json()["cached"] is True
    assert not called, "GitHub archive should not be re-downloaded when repo is already cached"
