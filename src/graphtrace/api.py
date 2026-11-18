from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from graphtrace.cli import _refresh_index
from graphtrace.graph_engine import DependencyGraph
from graphtrace.storage import GraphStorage
from graphtrace.subgraph_rag import SubgraphRAG

WORKSPACE_ROOT = Path(
    os.environ.get("GRAPHTRACE_WORKSPACES", Path(tempfile.gettempdir()) / "graphtrace_workspaces")
)
GITHUB_URL_RE = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")
SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")


class IndexRepoRequest(BaseModel):
    repo_url: str = Field(..., description="Public GitHub HTTPS URL, e.g. https://github.com/owner/repo")
    force: bool = Field(default=False, description="Force full re-index")


class AskRepoRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=1000)
    symbol: str | None = Field(default=None)
    model: str = Field(default="gemini-4-hydrogen")


def _slug_from_url(url: str) -> tuple[str, str]:
    match = GITHUB_URL_RE.match(url.strip())
    if not match:
        raise HTTPException(
            status_code=400,
            detail="Only public HTTPS GitHub repository URLs (https://github.com/<owner>/<repo>) are supported.",
        )
    owner, repo = match.group(1), match.group(2)
    clean_url = f"https://github.com/{owner}/{repo}.git"
    slug = f"{owner}__{repo}"
    return slug, clean_url


def _get_workspace_dir(slug: str) -> Path:
    if slug == "demo":
        local_src = Path(__file__).resolve().parents[2]
        if (local_src / "src" / "graphtrace").is_dir():
            return local_src
        demo_ws = (WORKSPACE_ROOT / "demo").resolve()
        pkg_dir = Path(__file__).resolve().parent
        if not (demo_ws / "graphtrace").is_dir():
            demo_ws.mkdir(parents=True, exist_ok=True)
            shutil.copytree(pkg_dir, demo_ws / "graphtrace", dirs_exist_ok=True)
        return demo_ws

    if not SLUG_RE.match(slug):
        raise HTTPException(status_code=400, detail="Invalid workspace slug.")
    target = (WORKSPACE_ROOT / slug).resolve()
    if not str(target).startswith(str(WORKSPACE_ROOT.resolve())) or not target.is_dir():
        raise HTTPException(status_code=404, detail=f"Workspace '{slug}' not found. Index the repository first.")
    return target


def _open_storage(repo_dir: Path, auto_index: bool = False) -> GraphStorage:
    db_path = repo_dir / ".graphtrace" / "index.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    storage = GraphStorage(db_path)
    if auto_index and storage.get_stats()["files"] == 0:
        _refresh_index(repo_dir, storage, force=False)
    return storage


@asynccontextmanager
async def lifespan(app: FastAPI):
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    demo_dir = _get_workspace_dir("demo")
    storage = _open_storage(demo_dir, auto_index=True)
    storage.close()
    yield


app = FastAPI(
    title="GraphTrace Cloud API",
    description="Structural & Evolutionary Codebase Dependency and Impact Analysis Engine",
    version="0.2.0",
    lifespan=lifespan,
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content={"error": type(exc).__name__, "detail": str(exc)},
    )


allowed_origins = os.environ.get("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in allowed_origins],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root() -> dict[str, str]:
    """Root landing endpoint for Render service health and discovery."""
    return {
        "service": "GraphTrace Cloud API",
        "status": "running",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health")
def health_check() -> dict[str, str]:
    """Render health check endpoint."""
    return {"status": "ok", "service": "graphtrace-api"}


@app.get("/api/repos")
def list_workspaces() -> dict[str, Any]:
    """List available indexed workspaces including the built-in demo."""
    items = []
    demo_dir = _get_workspace_dir("demo")
    st = _open_storage(demo_dir, auto_index=True)
    try:
        items.append({"slug": "demo", "label": "GraphTrace (Built-in Self-Analysis)", "stats": st.get_stats()})
    finally:
        st.close()

    if WORKSPACE_ROOT.is_dir():
        for entry in sorted(WORKSPACE_ROOT.iterdir()):
            if entry.is_dir() and (entry / ".graphtrace" / "index.db").is_file():
                s = _open_storage(entry, auto_index=False)
                try:
                    items.append({"slug": entry.name, "label": entry.name.replace("__", "/"), "stats": s.get_stats()})
                finally:
                    s.close()
    return {"workspaces": items}


@app.post("/api/repos/index")
def index_github_repo(payload: IndexRepoRequest) -> dict[str, Any]:
    """Shallow-clone a public GitHub repository and build its structural + co-change graph."""
    slug, clone_url = _slug_from_url(payload.repo_url)
    target_dir = WORKSPACE_ROOT / slug

    if payload.force and target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)

    if not target_dir.exists():
        try:
            subprocess.run(
                ["git", "clone", "--depth=25", clone_url, str(target_dir)],
                check=True,
                capture_output=True,
                text=True,
                timeout=45,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
        except subprocess.TimeoutExpired as exc:
            shutil.rmtree(target_dir, ignore_errors=True)
            raise HTTPException(status_code=504, detail="Repository clone timed out (45s limit).") from exc
        except subprocess.CalledProcessError as exc:
            shutil.rmtree(target_dir, ignore_errors=True)
            raise HTTPException(
                status_code=400,
                detail=f"Failed to clone repository. Ensure it is public: {exc.stderr.strip()}",
            ) from exc

    storage = _open_storage(target_dir, auto_index=False)
    try:
        parsed, skipped, pruned = _refresh_index(target_dir, storage, force=payload.force)
        stats = storage.get_stats()
        return {
            "slug": slug,
            "repo_url": payload.repo_url,
            "parsed_files": parsed,
            "cached_files": skipped,
            "pruned_files": pruned,
            "stats": stats,
        }
    finally:
        storage.close()


@app.get("/api/repos/{slug}/graph")
def get_repo_graph(
    slug: str,
    max_nodes: int = Query(default=150, ge=10, le=500),
) -> dict[str, Any]:
    """Return nodes and directed edges formatted for interactive frontend graph visualization."""
    repo_dir = _get_workspace_dir(slug)
    storage = _open_storage(repo_dir, auto_index=(slug == "demo"))
    try:
        dep_graph = DependencyGraph(storage)
        nodes = []
        included_ids: set[str] = set()

        for idx, meta in dep_graph.idx_to_meta.items():
            if meta["kind"] == "module":
                continue
            nodes.append(
                {
                    "id": meta["id"],
                    "name": meta["name"],
                    "kind": meta["kind"],
                    "rel_path": meta["rel_path"],
                    "start_line": meta["start_line"],
                    "end_line": meta["end_line"],
                    "is_test": bool(meta["is_test"]),
                }
            )
            included_ids.add(meta["id"])
            if len(nodes) >= max_nodes:
                break

        edges = []
        for u_idx, v_idx, edge_data in dep_graph.graph.weighted_edge_list():
            src_id = dep_graph.idx_to_meta[u_idx]["id"]
            dst_id = dep_graph.idx_to_meta[v_idx]["id"]
            if src_id in included_ids and dst_id in included_ids:
                edges.append(
                    {
                        "source": src_id,
                        "target": dst_id,
                        "edge_type": edge_data.get("edge_type", "CALLS"),
                        "weight": round(float(edge_data.get("weight", 1.0)), 2),
                    }
                )

        return {
            "slug": slug,
            "stats": storage.get_stats(),
            "nodes": nodes,
            "edges": edges,
        }
    finally:
        storage.close()


@app.get("/api/repos/{slug}/trace")
def trace_symbol_impact(
    slug: str,
    symbol: str = Query(..., min_length=1),
    depth: int = Query(default=5, ge=1, le=10),
) -> dict[str, Any]:
    """Compute transitive downstream blast radius and minimal impacted test command for a symbol."""
    repo_dir = _get_workspace_dir(slug)
    storage = _open_storage(repo_dir, auto_index=(slug == "demo"))
    try:
        matches = storage.find_symbols_by_query(symbol)
        if not matches:
            raise HTTPException(status_code=404, detail=f"No symbol matched '{symbol}' in '{slug}'.")

        seed_ids = [row["id"] for row in matches]
        dep_graph = DependencyGraph(storage)
        impacts = dep_graph.compute_blast_radius(seed_ids, max_depth=depth)
        impacted_tests = [
            item
            for item in impacts
            if (
                "tests/" in item.rel_path
                or Path(item.rel_path).name.startswith("test_")
                or ".test." in Path(item.rel_path).name
                or ".spec." in Path(item.rel_path).name
            )
        ]

        test_files = sorted({t.rel_path for t in impacted_tests if t.rel_path.endswith(".py")})
        suggested_cmd = f"pytest {' '.join(test_files)}" if test_files else None

        return {
            "slug": slug,
            "query": symbol,
            "seed_symbols": seed_ids,
            "total_impacted": len(impacts),
            "impacted_symbols": [item.model_dump() for item in impacts],
            "impacted_tests": [item.model_dump() for item in impacted_tests],
            "suggested_test_command": suggested_cmd,
        }
    finally:
        storage.close()


@app.post("/api/repos/{slug}/ask")
def ask_subgraph_question(slug: str, payload: AskRepoRequest) -> dict[str, Any]:
    """Synthesize a citation-backed architectural answer strictly from a verified dependency subgraph."""
    repo_dir = _get_workspace_dir(slug)
    storage = _open_storage(repo_dir, auto_index=(slug == "demo"))
    try:
        rag = SubgraphRAG(repo_dir, storage)
        response = rag.ask(
            question=payload.question,
            explicit_symbol=payload.symbol,
            model=payload.model,
        )
        return response.model_dump()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        storage.close()