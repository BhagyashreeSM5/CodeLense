"""Analyze codebase architecture: summaries, dependency routes, key parts, and routing chains."""
from collections import defaultdict, deque
from dataclasses import asdict
from pathlib import Path
import re

from app.parse import ParsedModule, Symbol, parse_python_module

JS_IMPORT_RE = re.compile(
    r"""(?:import\s+(?:(?:\{[^}]*\}|\*\s+as\s+\w+|\w+)\s+from\s+)?['"]([^'"]+)['"]|require\s*\(\s*['"]([^'"]+)['"]\s*\))"""
)


def _resolve_relative(source_path: Path, target_str: str, all_paths: set[str]) -> str | None:
    """Resolve a relative import target against existing repository paths."""
    base_dir = source_path.parent
    candidate = (base_dir / target_str).resolve()
    # Normalize relative to workspace root (simulated)
    # We use posix string matching
    parts = list(source_path.parent.parts)
    target_parts = target_str.replace("\\", "/").split("/")
    
    for p in target_parts:
        if p == ".":
            continue
        elif p == "..":
            if parts:
                parts.pop()
        else:
            parts.append(p)
    
    candidate_posix = "/".join(parts)
    for ext in ["", ".py", ".js", ".ts", "/index.js", "/index.ts", "/__init__.py"]:
        test_path = candidate_posix + ext
        if test_path in all_paths:
            return test_path
    return None


def _resolve_python_import(
    source_path_str: str,
    module: str | None,
    names: list[str],
    level: int,
    all_paths: set[str],
) -> list[tuple[str, list[str]]]:
    """
    Resolve a Python import to repository files and specific symbols.
    Returns list of (target_file_path, used_symbols).
    """
    results: list[tuple[str, list[str]]] = []
    source_path = Path(source_path_str)

    # 1. Relative import (e.g. from . import store or from ..utils import helper)
    if level > 0:
        parts = list(source_path.parent.parts)
        for _ in range(level - 1):
            if parts:
                parts.pop()
        base_dir_posix = "/".join(parts)

        # Case A: from .store import Store
        if module:
            mod_posix = module.replace(".", "/")
            candidate = f"{base_dir_posix}/{mod_posix}" if base_dir_posix else mod_posix
            for ext in [".py", "/__init__.py"]:
                if candidate + ext in all_paths:
                    return [(candidate + ext, names)]
        
        # Case B: from . import store, utils
        for name in names:
            candidate = f"{base_dir_posix}/{name}.py" if base_dir_posix else f"{name}.py"
            if candidate in all_paths:
                results.append((candidate, []))
            init_candidate = f"{base_dir_posix}/{name}/__init__.py" if base_dir_posix else f"{name}/__init__.py"
            if init_candidate in all_paths:
                results.append((init_candidate, []))
        if results:
            return results

    # 2. Absolute import (e.g. from app.store import Store or import app.github)
    if module:
        mod_posix = module.replace(".", "/")
        
        # Check directly e.g. app/store.py or app/store/__init__.py
        for ext in [".py", "/__init__.py"]:
            if mod_posix + ext in all_paths:
                return [(mod_posix + ext, names)]

        # Check in sibling directory (e.g. import store inside app/main.py)
        sibling = f"{source_path.parent.as_posix()}/{mod_posix}" if source_path.parent.as_posix() != "." else mod_posix
        for ext in [".py", "/__init__.py"]:
            if sibling + ext in all_paths:
                return [(sibling + ext, names)]

        # Check inside src/ prefix if repo has src layout
        for ext in [".py", "/__init__.py"]:
            if f"src/{mod_posix}{ext}" in all_paths:
                return [(f"src/{mod_posix}{ext}", names)]

        # Case: from app import store (where module='app' and names=['store'])
        for name in names:
            cand = f"{mod_posix}/{name}.py"
            if cand in all_paths:
                results.append((cand, []))
            cand_init = f"{mod_posix}/{name}/__init__.py"
            if cand_init in all_paths:
                results.append((cand_init, []))
        if results:
            return results

    return []


def generate_summary(
    path: str,
    docstring: str | None,
    symbols: list[Symbol],
    outgoing_routes: list[dict],
    incoming_routes: list[dict],
    external_deps: list[str],
    how_routed: dict,
) -> str:
    """Generate a 2-4 sentence plain language summary grounded strictly in the code."""
    sentences: list[str] = []

    # Sentence 1: Primary responsibility
    if docstring and docstring.strip():
        first_line = docstring.strip().split("\n")[0].strip()
        first_line = first_line.rstrip(".")
        if first_line.lower().startswith("this file"):
            sentences.append(f"{first_line}.")
        elif first_line.lower().startswith("extract ") or first_line.lower().startswith("unpack ") or first_line.lower().startswith("turn "):
            # Natural action sentence
            first_line = first_line[0].lower() + first_line[1:]
            sentences.append(f"This file is responsible for {first_line}.")
        else:
            sentences.append(f"This file provides {first_line}.")
    else:
        classes = [s.name for s in symbols if s.kind == "class"]
        funcs = [s.name for s in symbols if s.kind == "function"]
        if classes:
            c_str = ", ".join(classes[:3]) + (" and other classes" if len(classes) > 3 else "")
            sentences.append(f"This file defines the {c_str} class implementation.")
        elif funcs:
            f_str = ", ".join(funcs[:3]) + (" and other functions" if len(funcs) > 3 else "")
            sentences.append(f"This file provides utility functions including {f_str}.")
        else:
            sentences.append("This file is a repository source module without top-level classes or functions.")

    # Sentence 2: Key components
    classes = [s.name for s in symbols if s.kind == "class"]
    funcs = [s.name for s in symbols if s.kind == "function"]
    methods = [s.name for s in symbols if s.kind == "method"]
    parts_desc = []
    if classes:
        parts_desc.append(f"{len(classes)} class{'es' if len(classes) > 1 else ''} ({', '.join(classes[:2])})")
    if funcs:
        parts_desc.append(f"{len(funcs)} function{'s' if len(funcs) > 1 else ''} ({', '.join(funcs[:2])})")
    if methods and not classes:
        parts_desc.append(f"{len(methods)} method{'s' if len(methods) > 1 else ''}")

    if parts_desc:
        sentences.append(f"It implements {' and '.join(parts_desc)}.")
    else:
        sentences.append("Specific code components are not determined from symbols.")

    # Sentence 3: Repository Connections (Outgoing / Incoming)
    outgoing_files = [r["path"] for r in outgoing_routes]
    incoming_files = [r["path"] for r in incoming_routes]

    if outgoing_files and incoming_files:
        out_names = ", ".join(outgoing_files[:2]) + (" and others" if len(outgoing_files) > 2 else "")
        in_names = ", ".join(incoming_files[:2]) + (" and others" if len(incoming_files) > 2 else "")
        sentences.append(f"It connects to {out_names} and is routed to by {in_names}.")
    elif outgoing_files:
        out_names = ", ".join(outgoing_files[:2]) + (" and others" if len(outgoing_files) > 2 else "")
        sentences.append(f"It routes to {out_names} to perform its tasks.")
    elif incoming_files:
        in_names = ", ".join(incoming_files[:2]) + (" and others" if len(incoming_files) > 2 else "")
        sentences.append(f"It is routed to and used by {in_names}.")
    else:
        sentences.append("It does not connect to other repository files.")

    # Sentence 4: Route chain context or external dependencies
    if len(sentences) < 4:
        if how_routed.get("chain") and len(how_routed["chain"]) > 1:
            entry = how_routed["chain"][0]
            sentences.append(f"Execution reaches this file from {entry}.")
        elif external_deps:
            ext_str = ", ".join(external_deps[:3])
            sentences.append(f"It relies on external dependencies including {ext_str}.")

    return " ".join(sentences[:4])


def analyze_repository(root: Path | dict[str, str], file_paths: list[Path] | None = None) -> dict[str, dict]:
    """
    Comprehensive repository analysis:
    Supports in-memory dict[posix_path, source_code] (zero-disk usage) or root Path with list_source_files.
    """
    modules: dict[str, ParsedModule] = {}
    file_sizes: dict[str, int] = {}
    languages: dict[str, str] = {}

    if isinstance(root, dict):
        all_paths = set(root.keys())
        for posix_path, source in root.items():
            file_sizes[posix_path] = len(source.encode("utf-8"))
            suffix = Path(posix_path).suffix.lower()
            if suffix == ".py":
                languages[posix_path] = "python"
                modules[posix_path] = parse_python_module(posix_path, source)
            else:
                lang = "javascript" if suffix == ".js" else "typescript" if suffix == ".ts" else "other"
                languages[posix_path] = lang
                modules[posix_path] = ParsedModule(
                    path=posix_path,
                    docstring=None,
                    symbols=[],
                    imports=[],
                    referenced_names=set(),
                    has_main_block=False,
                )
    else:
        file_paths = file_paths or []
        all_paths = {p.as_posix() for p in file_paths}
        for rel in file_paths:
            posix_path = rel.as_posix()
            full_path = root / rel
            file_sizes[posix_path] = full_path.stat().st_size
            suffix = rel.suffix.lower()
            if suffix == ".py":
                languages[posix_path] = "python"
                source = full_path.read_text(encoding="utf-8", errors="replace")
                modules[posix_path] = parse_python_module(posix_path, source)
            else:
                lang = "javascript" if suffix == ".js" else "typescript" if suffix == ".ts" else "other"
                languages[posix_path] = lang
                source = full_path.read_text(encoding="utf-8", errors="replace")
                modules[posix_path] = ParsedModule(
                    path=posix_path,
                    docstring=None,
                    symbols=[],
                    imports=[],
                    referenced_names=set(),
                    has_main_block=False,
                )

    # Step 2: Resolve Outgoing Routes & External Dependencies
    outgoing_routes: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    external_dependencies: dict[str, set[str]] = defaultdict(set)

    for posix_path, mod in modules.items():
        if languages[posix_path] == "python":
            for imp in mod.imports:
                resolved = _resolve_python_import(posix_path, imp.module, imp.names, imp.level, all_paths)
                if resolved:
                    for target_file, sym_names in resolved:
                        if target_file == posix_path:
                            continue
                        # Determine symbols used: check which symbols in target_file are used or imported
                        target_symbols = {s.name for s in modules.get(target_file, mod).symbols}
                        used_symbols = [s for s in sym_names if s in target_symbols or s != "*"]
                        # Also check referenced names
                        for s_name in target_symbols:
                            if s_name in mod.referenced_names and s_name not in used_symbols:
                                used_symbols.append(s_name)
                        if not used_symbols:
                            # Use imported module name or file import indicator
                            used_symbols = [imp.module.split(".")[-1]] if imp.module else ["(file import)"]
                        outgoing_routes[posix_path][target_file].update(used_symbols)
                else:
                    if imp.module:
                        root_pkg = imp.module.split(".")[0]
                        if root_pkg and root_pkg not in ("__future__",):
                            external_dependencies[posix_path].add(root_pkg)

    # Step 3: Build Incoming Routes and Symbol Usage
    incoming_routes: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for src_path, targets in outgoing_routes.items():
        for tgt_path, syms in targets.items():
            incoming_routes[tgt_path][src_path].update(syms)
            # Mark symbols in tgt_path as called_by src_path
            tgt_mod = modules.get(tgt_path)
            if tgt_mod:
                for sym in tgt_mod.symbols:
                    if sym.name in syms and src_path not in sym.called_by:
                        sym.called_by.append(src_path)

    # Step 4: Identify Entry Points
    scores: dict[str, int] = defaultdict(int)
    for posix_path in all_paths:
        base_name = Path(posix_path).name.lower()
        if base_name in ("main.py", "app.py", "server.py", "run.py", "manage.py", "cli.py"):
            scores[posix_path] += 100
        elif "main" in base_name:
            scores[posix_path] += 60
        mod = modules.get(posix_path)
        if mod and mod.has_main_block:
            scores[posix_path] += 50
        if mod and any(w in mod.referenced_names for w in ("FastAPI", "Flask", "Starlette", "Django")):
            scores[posix_path] += 40
        # Files with 0 incoming routes (entry point callers)
        if len(incoming_routes[posix_path]) == 0:
            scores[posix_path] += 20
        # Prefer files with outgoing routes
        scores[posix_path] += len(outgoing_routes[posix_path]) * 5

    # Select primary entry point
    primary_entry_point = max(all_paths, key=lambda p: (scores[p], -len(p)), default=None)

    # Step 5: Compute Shortest Route Chains (BFS)
    graph: dict[str, list[str]] = defaultdict(list)
    for src_path, targets in outgoing_routes.items():
        for tgt_path in targets:
            graph[src_path].append(tgt_path)

    def find_shortest_chain(start: str, goal: str) -> list[str] | None:
        if start == goal:
            return [start]
        visited = {start}
        queue = deque([[start]])
        while queue:
            path = queue.popleft()
            node = path[-1]
            for neighbor in graph.get(node, []):
                if neighbor == goal:
                    return path + [neighbor]
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(path + [neighbor])
        return None

    # Step 6: Generate Full Analysis per File
    analysis_results: dict[str, dict] = {}

    for posix_path in all_paths:
        mod = modules.get(posix_path)
        out_list = [
            {"path": tgt, "symbols": sorted(list(syms))}
            for tgt, syms in sorted(outgoing_routes[posix_path].items())
        ]
        in_list = [
            {"path": src, "symbols": sorted(list(syms))}
            for src, syms in sorted(incoming_routes[posix_path].items())
        ]
        ext_list = sorted(list(external_dependencies[posix_path]))

        # Routing explanation
        if posix_path == primary_entry_point:
            how_routed = {
                "entry_point": posix_path,
                "chain": [posix_path],
                "explanation": f"This file ({posix_path}) is the primary entry point for the application.",
            }
        else:
            chain = find_shortest_chain(primary_entry_point, posix_path) if primary_entry_point else None
            if chain:
                chain_str = " -> ".join(chain)
                how_routed = {
                    "entry_point": primary_entry_point,
                    "chain": chain,
                    "explanation": f"This file is reached from {primary_entry_point}, which is the entry point for the application. Route chain: {chain_str}.",
                }
            else:
                # Check other zero in-degree files
                zero_in = [p for p in all_paths if len(incoming_routes[p]) == 0 and p != posix_path]
                alt_chain = None
                for candidate_entry in zero_in:
                    c = find_shortest_chain(candidate_entry, posix_path)
                    if c:
                        alt_chain = c
                        break
                if alt_chain:
                    how_routed = {
                        "entry_point": alt_chain[0],
                        "chain": alt_chain,
                        "explanation": f"This file is reached from secondary entry point {alt_chain[0]} (route chain: {' -> '.join(alt_chain)}).",
                    }
                else:
                    how_routed = {
                        "entry_point": None,
                        "chain": [],
                        "explanation": "This file is not directly reached from the primary entry point (it may be a standalone script, utility, or test module).",
                    }

        symbols_list = mod.symbols if mod else []
        summary = generate_summary(
            path=posix_path,
            docstring=mod.docstring if mod else None,
            symbols=symbols_list,
            outgoing_routes=out_list,
            incoming_routes=in_list,
            external_deps=ext_list,
            how_routed=how_routed,
        )

        analysis_results[posix_path] = {
            "path": posix_path,
            "name": Path(posix_path).name,
            "language": languages.get(posix_path, "unknown"),
            "size": file_sizes.get(posix_path, 0),
            "summary": summary,
            "outgoing_routes": out_list,
            "incoming_routes": in_list,
            "external_dependencies": ext_list,
            "how_routed": how_routed,
            "key_parts": symbols_list,
        }

    return analysis_results
