import io
import zipfile
from fastapi.testclient import TestClient

from app import github
from app.main import app

client = TestClient(app)


def make_archive(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_deep_file_analysis_and_routing(monkeypatch):
    sample_files = {
        "demo-main/app/main.py": (
            '"""Main web application entry point."""\n'
            "import fastapi\n"
            "from app.store import Store\n"
            "from app.parse import parse_file\n\n"
            "app = fastapi.FastAPI()\n\n"
            "def start():\n"
            "    s = Store()\n"
            "    return parse_file('test.py')\n"
        ),
        "demo-main/app/store.py": (
            '"""SQLite database persistence module."""\n'
            "from app.parse import Symbol\n\n"
            "class Store:\n"
            "    def __init__(self):\n"
            "        pass\n"
            "    def get_symbol(self):\n"
            "        return Symbol('foo')\n"
        ),
        "demo-main/app/parse.py": (
            '"""Parser module for extracting symbols."""\n\n'
            "class Symbol:\n"
            "    def __init__(self, name):\n"
            "        self.name = name\n\n"
            "def parse_file(path):\n"
            "    return []\n"
        ),
    }

    archive = make_archive(sample_files)
    monkeypatch.setattr(github, "download_archive", lambda owner, repo: archive)

    resp = client.post("/repos", json={"url": "https://github.com/acme/codelens-demo"})
    assert resp.status_code == 202
    repo_id = resp.json()["id"]

    repo = client.get(f"/repos/{repo_id}").json()
    assert repo["status"] == "done"
    assert repo["file_count"] == 3

    # Test GET /repos/{repo_id}/files/{path}/details for main.py
    main_details = client.get(f"/repos/{repo_id}/files/app/main.py/details").json()
    assert main_details["path"] == "app/main.py"
    assert main_details["name"] == "main.py"
    assert main_details["language"] == "python"

    # Outgoing routes from main.py
    outgoing_paths = {r["path"]: r["symbols"] for r in main_details["outgoing_routes"]}
    assert "app/store.py" in outgoing_paths
    assert "Store" in outgoing_paths["app/store.py"]
    assert "app/parse.py" in outgoing_paths
    assert "parse_file" in outgoing_paths["app/parse.py"]

    # External dependencies
    assert "fastapi" in main_details["external_dependencies"]

    # Entry point & routing
    assert main_details["how_routed"]["entry_point"] == "app/main.py"
    assert "primary entry point" in main_details["how_routed"]["explanation"].lower()

    # Summary
    summary = main_details["summary"]
    assert len(summary) > 20
    # Must be between 2 and 4 sentences
    sentences = [s.strip() for s in summary.split(". ") if s.strip()]
    assert 2 <= len(sentences) <= 4

    # Test GET /repos/{repo_id}/files/{path}/details for store.py
    store_details = client.get(f"/repos/{repo_id}/files/app/store.py/details").json()
    assert store_details["path"] == "app/store.py"

    # Incoming routes to store.py from main.py
    incoming_paths = {r["path"]: r["symbols"] for r in store_details["incoming_routes"]}
    assert "app/main.py" in incoming_paths
    assert "Store" in incoming_paths["app/main.py"]

    # How store.py is reached from main.py
    assert store_details["how_routed"]["entry_point"] == "app/main.py"
    assert store_details["how_routed"]["chain"] == ["app/main.py", "app/store.py"]

    # Key parts: Store class should show called_by including app/main.py
    store_class = next(k for k in store_details["key_parts"] if k["name"] == "Store")
    assert "app/main.py" in store_class["called_by"]
