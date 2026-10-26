# CodeLens v2

Paste a public GitHub repository URL, and CodeLens downloads it, lists its source
files, and shows the functions, classes, and imports in each Python file when you
click it. No AI in this version.

## Setup

    python -m venv .venv
    .venv\Scripts\activate          (Windows)
    source .venv/bin/activate       (macOS / Linux)
    pip install -r requirements.txt

## Run

    uvicorn app.main:app --reload

Open http://127.0.0.1:8000 in your browser.

## Test

    pytest

## How it works

1. The browser posts the URL to POST /repos. The URL is validated and a job is created.
2. A background task downloads the default branch as a zip from GitHub.
3. The zip is unpacked, and the single top-level folder is removed from the paths.
4. Each source file is recorded. Python files are parsed with the ast module.
5. The browser polls GET /repos/{id} until the status is done or failed.
6. Clicking a file calls GET /repos/{id}/files/{path}/symbols.

Status flow: queued, downloading, processing, done (or failed, with an error message).

## Limits

- Public repositories only. Private repositories return a clear error.
- Downloads the default branch, not a chosen branch or commit.
- Symbols are extracted from Python only. JavaScript and TypeScript files are listed but have no symbols yet.
- Archives over 50 MB and files over 500 KB are rejected or skipped.

## Layout

- app/main.py: API routes and the background job
- app/github.py: URL parsing and archive download
- app/ingest.py: unzip, filter files
- app/parse.py: extract symbols
- app/store.py: SQLite tables and queries
- app/static/index.html: the browser page
