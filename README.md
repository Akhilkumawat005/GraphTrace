# GraphTrace

**Deterministic Structural & Evolutionary Codebase Impact Analysis Engine**

[![CI](https://github.com/Akhilkumawat005/GraphTrace/actions/workflows/ci.yml/badge.svg)](https://github.com/Akhilkumawat005/GraphTrace/actions)
[![Live Demo](https://img.shields.io/badge/Live%20Demo-Vercel-000000?logo=vercel)](https://graph-trace.vercel.app)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-TypeScript-61DAFB?logo=react&logoColor=black)](https://react.dev/)

**Live Interactive Explorer:** [https://graph-trace.vercel.app](https://graph-trace.vercel.app)

GraphTrace parses Python and TypeScript/JavaScript repositories into a persistent **Structural + Evolutionary Dependency Graph** (Tree-sitter AST + SQLite WAL + `rustworkx`), enabling engineers and AI coding agents to compute exact transitive blast radius, select minimal test subsets from `git diff`, explore dependencies interactively in the browser, and query architectural impact with verifiable `file:line` citations.

---

## Architecture

~~~mermaid
flowchart LR
    Repo[Git Repository\n.py / .ts / .tsx / .git] --> Scanner[SHA-256 Incremental\nFile Scanner]
    Scanner --> Parser[Tree-sitter AST\nSymbol & Ref Extractor]
    Repo --> GitMiner[Git History Co-Change\n& Unified Diff Mapper]
    Parser --> Resolver[Tiered Cross-File\nSymbol Resolver]
    Resolver --> DB[(SQLite WAL\nGraph Store)]
    GitMiner --> DB
    DB --> RX[rustworkx PyDiGraph\nWeighted Reverse-BFS]
    RX --> CLI[Typer CLI\nindex / trace / impact]
    RX --> MCP[JSON-RPC 2.0\nstdio MCP Server]
    RX --> RAG[Subgraph Slicer +\nGemini API Synthesis]
    RX --> API[FastAPI REST API\nRender Docker]
    API --> UI[React + TypeScript UI\nVercel Cloud]
~~~

## Key Engineering Capabilities

1. **Incremental Content-Addressable Indexing:** Tracks per-file SHA-256 hashes and `mtime` timestamps in SQLite WAL mode, re-parsing only modified files and cascading stale edge cleanup.
2. **Tiered Cross-File Symbol Resolution:** Resolves relative imports, Python package paths, and `tsconfig.json` path aliases across four confidence tiers (`1.0` explicit import -> `0.65` project-wide symbol match).
3. **Hybrid Static + Evolutionary Coupling:** Fuses AST edges (`CALLS`, `INHERITS`, `IMPORTS`) with confidence-filtered Git co-change edges (`CO_CHANGES`) to catch implicit cross-boundary dependencies.
4. **Zero-Context `git diff` Hunk-to-Symbol Mapping:** Intersects `git diff --unified=0` line spans with AST symbol boundaries to compute downstream blast radius and emit minimal `pytest` / `vitest` commands.
5. **Subgraph-Grounded Synthesis & MCP Server:** Slices 2-hop upstream/downstream neighborhoods wrapped in prompt-injection-safe `<untrusted_code_context>` blocks for Gemini API synthesis (with deterministic subgraph fallback when no API key is configured) and exposes `get_blast_radius` and `get_impacted_tests` via stdio MCP.
6. **Full-Stack Cloud Explorer:** Interactive React + TypeScript + Tailwind frontend backed by a Dockerized FastAPI service capable of self-analyzing GraphTrace or cloning and indexing any public GitHub repository URL on demand.

---

## Project Structure

~~~text
GraphTrace/
├── src/graphtrace/
│   ├── api.py             # FastAPI REST endpoints, lifespan indexing & CORS configuration
│   ├── parser.py          # Tree-sitter AST symbol & reference extractor (.py / .ts / .tsx)
│   ├── resolver.py        # 4-tier cross-file symbol & import resolution engine
│   ├── blast_radius.py    # rustworkx PyDiGraph weighted reverse-BFS & test selector
│   ├── git_analyzer.py    # Git log co-change mining & zero-context unified diff mapper
│   ├── storage.py         # SQLite WAL persistence layer & incremental hash tracking
│   ├── subgraph_rag.py    # 2-hop subgraph slicer & prompt-safe Gemini synthesis
│   ├── mcp_server.py      # JSON-RPC 2.0 stdio Model Context Protocol (MCP) server
│   ├── cli.py             # Typer CLI entry points (`index`, `trace`, `impact`, `ask`, `mcp`)
│   └── models.py          # Pydantic schemas & dependency edge enumerations
├── web/                   # React + TypeScript + Vite + Tailwind frontend (deployed on Vercel)
├── tests/                 # 4-stage pytest suite (AST, Graph, Git Co-Change, RAG/MCP)
├── benchmarks/            # Reproducible recall & CI reduction benchmark runner
├── Dockerfile             # Production container image (deployed on Render)
└── pyproject.toml         # Package metadata, dependencies & CLI scripts
~~~

---

## Quickstart

### 1. CLI & Python Engine

~~~bash
git clone https://github.com/Akhilkumawat005/GraphTrace.git
cd GraphTrace

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"

# 1. Index a repository
graphtrace index .

# 2. Trace downstream blast radius of any symbol
graphtrace trace SymbolResolver

# 3. Analyze uncommitted git diff and get minimal test command
graphtrace impact

# 4. Ask a subgraph-grounded architectural question (optional GEMINI_API_KEY)
graphtrace ask "What breaks if I change the return structure of compute_blast_radius?"
~~~

### 2. Full-Stack Web App (Local Development)

Start the FastAPI backend:
~~~bash
uvicorn graphtrace.api:app --reload --port 8000
~~~

In a second terminal, start the React + Vite frontend:
~~~bash
cd web
npm install
npm run dev
~~~

Open `http://localhost:5173` in your browser.

---

## Environment Variables

### Backend (Render / Docker)
| Variable | Required | Default | Description |
| :--- | :--- | :--- | :--- |
| `CORS_ORIGINS` | Yes (Prod) | `*` | Allowed frontend origin(s), e.g. `https://graph-trace.vercel.app` |
| `GEMINI_API_KEY` | No | *(unset)* | Optional Google AI Studio key for LLM synthesis; falls back to deterministic subgraph context when omitted |
| `GRAPHTRACE_DATA_DIR` | No | `/tmp/graphtrace_repos` | Storage directory for cloned repositories and SQLite WAL indexes |

### Frontend (Vercel)
| Variable | Required | Default | Description |
| :--- | :--- | :--- | :--- |
| `VITE_API_BASE_URL` | Yes (Prod) | `http://localhost:8000` | Base URL of the deployed FastAPI backend (without trailing slash) |

---

## Benchmarks & Known Limitations

Run the test suite and reproducible benchmark suite:
~~~bash
pytest -v
python benchmarks/run_benchmark.py
~~~

- **Multi-Hop Recall:** Achieves **100% recall** across 4-hop call chains (`core -> helper -> service -> controller -> test`) where naive lexical search achieves **25% recall** (missing downstream callers that do not reference the leaf symbol by name).
- **Selective CI Reduction:** Cuts test execution scope by **98%** on single-module changes while retaining 100% recall on affected tests.
- **Known Limitations:**
  - Dynamic runtime dispatch (`getattr`, dependency-injection containers, reflection) is approximated via heuristic tier-4 matching (`confidence <= 0.65`) rather than full type-inference execution.
  - Currently supports Python and TypeScript/JavaScript; Go, Rust, and Java Tree-sitter grammars are planned extensions.