"""HTTP API: submit GitHub URLs, authenticate users, and manage cloud codebase intelligence."""
import os
from pathlib import Path
import uuid

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Response, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr

# Load .env if present
env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from app import auth, github
from app.analyze import analyze_repository
from app.ingest import IngestError, read_zip_source_files
from app.store import Store

BASE_DIR = Path(__file__).resolve().parent
DATABASE_URL = os.getenv("DATABASE_URL") or "data/codelens.db"

app = FastAPI(title="CodeLens v2 &bull; Cloud Intelligence")
store = Store(DATABASE_URL)


class RepoRequest(BaseModel):
    url: str
    force_refresh: bool = False


class AuthRequest(BaseModel):
    email: EmailStr
    password: str


def run_ingestion(repo_id: str, owner: str, repo: str) -> None:
    """Download and analyze repository entirely in-memory with zero local disk usage."""
    try:
        store.set_status(repo_id, "downloading")
        data = github.download_archive(owner, repo)

        store.set_status(repo_id, "processing")
        # Extract files in-memory without saving anything to the local PC
        file_sources = read_zip_source_files(data)
        analysis_map = analyze_repository(file_sources)

        for posix_path, content in file_sources.items():
            ext = Path(posix_path).suffix.lower()
            lang = "python" if ext == ".py" else "javascript" if ext == ".js" else "typescript" if ext == ".ts" else "other"
            file_size = len(content.encode("utf-8"))
            file_id = store.add_file(repo_id, posix_path, lang, file_size, content=content)

            analysis = analysis_map.get(posix_path)
            if analysis:
                store.update_file_analysis(
                    repo_id=repo_id,
                    path=posix_path,
                    summary=analysis["summary"],
                    outgoing_routes=analysis["outgoing_routes"],
                    incoming_routes=analysis["incoming_routes"],
                    external_dependencies=analysis["external_dependencies"],
                    how_routed=analysis["how_routed"],
                )
                store.add_symbols(file_id, analysis["key_parts"])
            else:
                store.update_file_analysis(
                    repo_id=repo_id,
                    path=posix_path,
                    summary="Summary not determined.",
                    outgoing_routes=[],
                    incoming_routes=[],
                    external_dependencies=[],
                    how_routed={
                        "entry_point": None,
                        "chain": [],
                        "explanation": "Route not determined.",
                    },
                )

        store.set_status(repo_id, "done")
    except IngestError as exc:
        store.set_status(repo_id, "failed", str(exc))
    except Exception as exc:  # never leave a repo stuck in a running state
        store.set_status(repo_id, "failed", f"unexpected error: {exc}")


@app.get("/")
def index():
    return FileResponse(BASE_DIR / "static" / "index.html")


# --- Authentication Routes ---
@app.post("/auth/signup", status_code=201)
def signup(body: AuthRequest):
    existing = store.get_user_by_email(body.email)
    if existing:
        raise HTTPException(status_code=400, detail="User with this email already exists")
    if len(body.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    hashed = auth.hash_password(body.password)
    user = store.create_user(body.email, hashed)
    token = auth.create_access_token({"sub": user["email"], "user_id": user["id"]})
    return {"token": token, "user": {"id": user["id"], "email": user["email"]}}


@app.post("/auth/login")
def login(body: AuthRequest):
    user = store.get_user_by_email(body.email)
    if not user or not auth.verify_password(body.password, user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
    token = auth.create_access_token({"sub": user["email"], "user_id": user["id"]})
    return {"token": token, "user": {"id": user["id"], "email": user["email"]}}


@app.get("/auth/me")
def me(current_user: dict = Depends(auth.get_current_user)):
    return current_user


# --- Repository Endpoints ---
@app.post("/repos", status_code=202)
def submit_repo(
    body: RepoRequest,
    background: BackgroundTasks,
    response: Response,
    current_user: dict | None = Depends(auth.get_optional_current_user),
):
    try:
        owner, repo = github.parse_repo_url(body.url)
    except IngestError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    repo_name = f"{owner}/{repo}"
    source_url = f"https://github.com/{owner}/{repo}"
    user_id = current_user.get("id") if current_user else None

    # Check if this repository was already analyzed and stored in Cloud DB
    existing = store.find_existing_repo(repo_name, source_url)
    if existing and not body.force_refresh:
        # Link to current user if logged in so it shows in "My Cloud Repositories"
        if user_id:
            store.link_repo_to_user(existing["id"], user_id)

        # If already done with analyzed files, return immediately with 200 OK (no re-downloading)
        if existing.get("status") == "done" and existing.get("file_count", 0) > 0:
            response.status_code = status.HTTP_200_OK
            return {
                "id": existing["id"],
                "name": existing["name"],
                "status": "done",
                "file_count": existing["file_count"],
                "cached": True,
            }
        # If currently being downloaded or analyzed, attach to the running task
        elif existing.get("status") in ("queued", "downloading", "processing"):
            return {
                "id": existing["id"],
                "name": existing["name"],
                "status": existing["status"],
                "cached": True,
            }

    # Only create a new ingestion job if not yet analyzed or forced
    repo_id = uuid.uuid4().hex
    store.create_repo(
        repo_id,
        name=repo_name,
        source_url=source_url,
        status="queued",
        user_id=user_id,
    )
    background.add_task(run_ingestion, repo_id, owner, repo)
    return {"id": repo_id, "name": repo_name, "status": "queued", "cached": False}


@app.get("/repos/my-repos")
def list_my_repos(current_user: dict = Depends(auth.get_current_user)):
    return store.list_user_repos(current_user["id"])


@app.get("/repos/{repo_id}")
def get_repo(repo_id: str):
    repo = store.get_repo(repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="repo not found")
    return repo


@app.get("/repos/{repo_id}/files")
def list_files(repo_id: str):
    if store.get_repo(repo_id) is None:
        raise HTTPException(status_code=404, detail="repo not found")
    return store.list_files(repo_id)


@app.get("/repos/{repo_id}/files/{path:path}/symbols")
def file_symbols(repo_id: str, path: str):
    symbols = store.get_file_symbols(repo_id, path)
    if symbols is None:
        raise HTTPException(status_code=404, detail="file not found")
    return {"path": path, "symbols": symbols}


@app.get("/repos/{repo_id}/files/{path:path}/details")
def file_details(repo_id: str, path: str):
    details = store.get_file_details(repo_id, path)
    if details is None:
        raise HTTPException(status_code=404, detail="file not found")
    return details
