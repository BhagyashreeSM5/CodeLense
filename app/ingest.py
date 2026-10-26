"""Unpack a zip archive and select the source files worth parsing."""
import io
import os
import zipfile
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", "vendor", "dist", "build", "__pycache__", ".venv"}
LANGUAGES = {".py": "python", ".js": "javascript", ".ts": "typescript"}
MAX_BYTES = 500_000


class IngestError(Exception):
    """An error the user should see. The message is returned to the client."""


def unpack_zip(data: bytes, dest: Path) -> Path:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise IngestError("not a valid zip file") from exc

    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    for member in zf.infolist():
        target = (dest / member.filename).resolve()
        # Reject zip-slip: entries that would write outside the destination.
        if target != root and root not in target.parents:
            raise IngestError(f"unsafe path in archive: {member.filename}")
    zf.extractall(dest)
    return dest


def read_zip_source_files(data: bytes, max_bytes: int = MAX_BYTES) -> dict[str, str]:
    """Read source files in-memory without saving anything to the local disk."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise IngestError("not a valid zip file") from exc

    # Identify single top-level folder added by GitHub (e.g. 'repo-main/')
    names = zf.namelist()
    top_level = ""
    if names and "/" in names[0]:
        prefix = names[0].split("/")[0] + "/"
        if all(n.startswith(prefix) or not n for n in names):
            top_level = prefix

    results: dict[str, str] = {}
    for member in zf.infolist():
        if member.is_dir():
            continue
        filename = member.filename
        rel_path = filename[len(top_level):] if top_level and filename.startswith(top_level) else filename

        # Reject path traversal / unsafe paths
        if rel_path.startswith("/") or ".." in rel_path.split("/"):
            raise IngestError(f"unsafe path in archive: {member.filename}")

        # Skip unwanted directories
        parts = rel_path.split("/")
        if any(p in SKIP_DIRS for p in parts):
            continue

        ext = Path(rel_path).suffix.lower()
        if ext not in LANGUAGES:
            continue
        if member.file_size > max_bytes:
            continue

        content = zf.read(member)
        if b"\x00" in content[:8192]:  # skip binary files
            continue

        results[rel_path] = content.decode("utf-8", errors="replace")

    return results


def content_root(dest: Path) -> Path:
    """GitHub archives contain one folder like 'repo-main/'. Return that folder."""
    entries = list(dest.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return dest


def is_binary(path: Path) -> bool:
    with path.open("rb") as f:
        return b"\x00" in f.read(8192)


def list_source_files(root: Path, max_bytes: int = MAX_BYTES) -> list[Path]:
    """Return paths relative to root for files we should record."""
    results: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            full = Path(dirpath) / name
            if full.suffix not in LANGUAGES:
                continue
            if full.stat().st_size > max_bytes:
                continue
            if is_binary(full):
                continue
            results.append(full.relative_to(root))
    return sorted(results)


def language_for(rel_path: Path) -> str:
    return LANGUAGES[rel_path.suffix]
