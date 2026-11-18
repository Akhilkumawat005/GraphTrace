from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

from graphtrace.models import CoChangeEdge, FileRecord, RawReference, ResolvedEdge, SymbolRecord

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA mmap_size = 67108864;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS files (
    rel_path TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL,
    language TEXT NOT NULL,
    is_test INTEGER NOT NULL DEFAULT 0,
    line_count INTEGER NOT NULL DEFAULT 0,
    indexed_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS symbols (
    id TEXT PRIMARY KEY,
    rel_path TEXT NOT NULL REFERENCES files(rel_path) ON DELETE CASCADE,
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    kind TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    signature TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS raw_references (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_symbol_id TEXT NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    source_file TEXT NOT NULL REFERENCES files(rel_path) ON DELETE CASCADE,
    source_rel_path TEXT NOT NULL REFERENCES files(rel_path) ON DELETE CASCADE,
    target_name TEXT NOT NULL,
    module_hint TEXT,
    edge_type TEXT NOT NULL,
    line_no INTEGER NOT NULL,
    line_number INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS resolved_edges (
    source_id TEXT NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    target_id TEXT NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    edge_type TEXT NOT NULL,
    confidence REAL NOT NULL,
    PRIMARY KEY (source_id, target_id, edge_type)
);

CREATE TABLE IF NOT EXISTS co_change_edges (
    file_a TEXT NOT NULL,
    file_b TEXT NOT NULL,
    co_change_count INTEGER NOT NULL,
    confidence REAL NOT NULL,
    PRIMARY KEY (file_a, file_b)
);

CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(rel_path);
CREATE INDEX IF NOT EXISTS idx_raw_refs_file ON raw_references(source_rel_path);
CREATE INDEX IF NOT EXISTS idx_edges_target ON resolved_edges(target_id);
CREATE INDEX IF NOT EXISTS idx_edges_source ON resolved_edges(source_id);
"""


class GraphStorage:
    """SQLite WAL-backed persistence layer for AST symbols, structural edges, and Git co-changes."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA_SQL)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def get_indexed_hashes(self) -> dict[str, tuple[int, str]]:
        """Return mapping of rel_path -> (1, sha256) for incremental change detection."""
        rows = self.conn.execute("SELECT rel_path, sha256 FROM files").fetchall()
        return {row["rel_path"]: (1, row["sha256"]) for row in rows}

    def prune_deleted_files(self, active_paths: set[str]) -> int:
        existing = {row["rel_path"] for row in self.conn.execute("SELECT rel_path FROM files").fetchall()}
        deleted = existing - active_paths
        if not deleted:
            return 0
        with self.conn:
            self.conn.executemany("DELETE FROM files WHERE rel_path = ?", [(p,) for p in deleted])
        return len(deleted)

    def upsert_file_parse(
        self,
        file_rec: FileRecord,
        symbols: Iterable[SymbolRecord],
        references: Iterable[RawReference],
    ) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM files WHERE rel_path = ?", (file_rec.rel_path,))
            self.conn.execute(
                """
                INSERT INTO files (rel_path, sha256, language, is_test, line_count, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    file_rec.rel_path,
                    file_rec.sha256,
                    file_rec.language,
                    int(file_rec.is_test),
                    file_rec.line_count,
                    file_rec.indexed_at,
                ),
            )
            self.conn.executemany(
                """
                INSERT INTO symbols (id, rel_path, name, qualified_name, kind, start_line, end_line, signature)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        s.id,
                        s.rel_path,
                        s.name,
                        s.qualified_name,
                        s.kind.value if hasattr(s.kind, "value") else str(s.kind),
                        s.start_line,
                        s.end_line,
                        s.signature,
                    )
                    for s in symbols
                ],
            )
            self.conn.executemany(
                """
                INSERT INTO raw_references (
                    source_symbol_id, source_file, source_rel_path, target_name, module_hint, edge_type, line_no, line_number
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        r.source_symbol_id,
                        r.source_file or file_rec.rel_path,
                        r.source_file or file_rec.rel_path,
                        r.target_name,
                        r.module_hint,
                        r.edge_type.value if hasattr(r.edge_type, "value") else str(r.edge_type),
                        r.line_no or r.line_number,
                        r.line_number or r.line_no,
                    )
                    for r in references
                ],
            )

    def replace_structural_edges(self, edges: Iterable[ResolvedEdge]) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM resolved_edges")
            self.conn.executemany(
                """
                INSERT OR REPLACE INTO resolved_edges (source_id, target_id, edge_type, confidence)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        e.source_id,
                        e.target_id,
                        e.edge_type.value if hasattr(e.edge_type, "value") else str(e.edge_type),
                        e.confidence,
                    )
                    for e in edges
                ],
            )

    def replace_co_change_edges(self, edges: Iterable[CoChangeEdge]) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM co_change_edges")
            self.conn.executemany(
                """
                INSERT OR REPLACE INTO co_change_edges (file_a, file_b, co_change_count, confidence)
                VALUES (?, ?, ?, ?)
                """,
                [(e.file_a, e.file_b, e.co_change_count, e.confidence) for e in edges],
            )

    def get_all_files(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM files").fetchall()

    def get_all_symbols(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT s.*, f.is_test
            FROM symbols s
            JOIN files f ON s.rel_path = f.rel_path
            """
        ).fetchall()

    def get_all_raw_references(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM raw_references").fetchall()

    def get_all_resolved_edges(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM resolved_edges").fetchall()

    def get_all_co_change_edges(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM co_change_edges").fetchall()

    # Compatibility aliases for resolver / git_analyzer / graph_engine
    fetch_all_files = get_all_files
    fetch_all_symbols = get_all_symbols
    fetch_all_raw_references = get_all_raw_references
    fetch_all_resolved_edges = get_all_resolved_edges
    fetch_all_edges = get_all_resolved_edges
    fetch_all_co_change_edges = get_all_co_change_edges

    def get_symbols_in_file(self, rel_path: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM symbols WHERE rel_path = ? ORDER BY start_line ASC",
            (rel_path,),
        ).fetchall()

    def find_symbols_by_query(self, query: str) -> list[sqlite3.Row]:
        q = query.strip()
        exact = self.conn.execute("SELECT * FROM symbols WHERE id = ?", (q,)).fetchall()
        if exact:
            return exact
        by_name = self.conn.execute(
            """
            SELECT * FROM symbols
            WHERE (name = ? OR qualified_name = ?) AND kind != 'module'
            """,
            (q, q),
        ).fetchall()
        if by_name:
            return by_name
        return self.conn.execute(
            """
            SELECT * FROM symbols
            WHERE (id LIKE ? OR qualified_name LIKE ?) AND kind != 'module'
            LIMIT 10
            """,
            (f"%{q}", f"%{q}"),
        ).fetchall()

    def search_symbols_by_tokens(self, tokens: list[str], limit: int = 4) -> list[sqlite3.Row]:
        results: list[sqlite3.Row] = []
        seen: set[str] = set()
        for tok in tokens:
            rows = self.conn.execute(
                """
                SELECT * FROM symbols
                WHERE (name = ? OR qualified_name LIKE ?) AND kind != 'module'
                LIMIT ?
                """,
                (tok, f"%{tok}%", limit),
            ).fetchall()
            for r in rows:
                if r["id"] not in seen:
                    seen.add(r["id"])
                    results.append(r)
                    if len(results) >= limit:
                        return results
        return results

    def get_stats(self) -> dict[str, int]:
        return {
            "files": self.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0],
            "symbols": self.conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0],
            "resolved_edges": self.conn.execute("SELECT COUNT(*) FROM resolved_edges").fetchone()[0],
            "co_change_edges": self.conn.execute("SELECT COUNT(*) FROM co_change_edges").fetchone()[0],
        }