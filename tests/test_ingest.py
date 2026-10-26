import io
import zipfile
from pathlib import Path

import pytest

from app.ingest import IngestError, content_root, list_source_files, unpack_zip


def make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_skips_node_modules_and_binaries(tmp_path: Path):
    data = make_zip({
        "main.py": b"print('hi')\n",
        "node_modules/lib/index.js": b"module.exports = 1;\n",
        "image.py": b"\x00\x01binary",
    })
    root = unpack_zip(data, tmp_path / "src")
    assert [p.as_posix() for p in list_source_files(root)] == ["main.py"]


def test_skips_files_over_size_limit(tmp_path: Path):
    data = make_zip({"big.py": b"x = 1\n" * 1000, "small.py": b"x = 1\n"})
    root = unpack_zip(data, tmp_path / "src")
    assert [p.as_posix() for p in list_source_files(root, max_bytes=100)] == ["small.py"]


def test_content_root_strips_single_top_folder(tmp_path: Path):
    data = make_zip({"demo-main/app/util.py": b"x = 1\n"})
    dest = unpack_zip(data, tmp_path / "src")
    root = content_root(dest)
    assert root.name == "demo-main"
    assert [p.as_posix() for p in list_source_files(root)] == ["app/util.py"]


def test_rejects_invalid_zip(tmp_path: Path):
    with pytest.raises(IngestError):
        unpack_zip(b"not a zip", tmp_path / "src")


def test_rejects_zip_slip(tmp_path: Path):
    data = make_zip({"../evil.py": b"x = 1\n"})
    with pytest.raises(IngestError):
        unpack_zip(data, tmp_path / "src")
