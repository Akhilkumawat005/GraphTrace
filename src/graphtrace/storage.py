from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from graphtrace.models import EdgeType, FileRecord, RawReference, ResolvedEdge, SymbolRecord


class GraphStorage:
    """SQLite WAL-mode persistent storage for GraphTrace symbols, references, and edges."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._configure_pragmas()
        self._init_schema()

    def _configure_pragmas(self) -> None:
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;")
        self.conn.execute("PRAGMA foreign_keys=ON;")

    def _init_schema(self) -> None:
        with self.conn:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS files (
                    rel_path TEXT PRIMARY KEY,
                    sha256 TEXT NOT NULL,
                    language TEXT NOT NULL,
                    is_test INTEGER NOT NULL DEFAULT 0,
                    mtime REAL NOT NULL DEFAULT 0.0,
                    indexed_at REAL NOT NULL DEFAULT (unixepoch('now'))
                );

                CREATE TABLE IF NOT EXISTS symbols (
                    id TEXT PRIMARY KEY,
                    rel_path TEXT NOT NULL,
                    name TEXT NOT NULL,
                    qualified_name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    start_line INTEGER NOT NULL,
                    end_line INTEGER NOT NULL,
                    is_test INTEGER NOT NULL DEFAULT 0,
                    signature TEXT DEFAULT '',
                    FOREIGN KEY (rel_path) REFERENCES files(rel_path) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS raw_references (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_symbol_id TEXT NOT NULL,
                    source_file TEXT NOT NULL,
                    target_name TEXT NOT NULL,
                    edge_type TEXT NOT NULL,
                    module_hint TEXT,
                    line_no INTEGER NOT NULL DEFAULT 1,
                    FOREIGN KEY (source_file) REFERENCES files(rel_path) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS edges (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    edge_type TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 1.0,
                    weight REAL NOT NULL DEFAULT 1.0,
                    line_no INTEGER
                );

                CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(rel_path);
                CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
                CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
                CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
                """
            )
        self._ensure_columns()

    def _ensure_columns(self) -> None:
        """Backfill missing columns if an older SQLite index file is opened."""
        file_cols = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(files)").fetchall()
        }
        ref_cols = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(raw_references)").fetchall()
        }
        edge_cols = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(edges)").fetchall()
        }
        with self.conn:
            if "mtime" not in file_cols:
                self.conn.execute(
                    "ALTER TABLE files ADD COLUMN mtime REAL NOT NULL DEFAULT 0.0"
                )
            if "module_hint" not in ref_cols:
                self.conn.execute(
                    "ALTER TABLE raw_references ADD COLUMN module_hint TEXT"
                )
            if "weight" not in edge_cols:
                self.conn.execute(
                    "ALTER TABLE edges ADD COLUMN weight REAL NOT NULL DEFAULT 1.0"
                )
            if "line_no" not in edge_cols:
                self.conn.execute("ALTER TABLE edges ADD COLUMN line_no INTEGER")

    def get_file_hash(self, rel_path: str) -> str | None:
        row = self.conn.execute(
            "SELECT sha256 FROM files WHERE rel_path = ?", (rel_path,)
        ).fetchone()
        return str(row["sha256"]) if row else None

    def get_indexed_hashes(self) -> dict[str, tuple[float, str]]:
        rows = self.conn.execute("SELECT rel_path, mtime, sha256 FROM files").fetchall()
        return {
            str(r["rel_path"]): (float(r["mtime"] or 0.0), str(r["sha256"]))
            for r in rows
        }

    def get_indexed_files(self) -> dict[str, str]:
        rows = self.conn.execute("SELECT rel_path, sha256 FROM files").fetchall()
        return {str(r["rel_path"]): str(r["sha256"]) for r in rows}

    def fetch_all_files(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT rel_path, rel_path AS file_path, sha256, language, is_test, mtime, indexed_at
            FROM files
            ORDER BY rel_path
            """
        ).fetchall()

    def delete_file(self, rel_path: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM files WHERE rel_path = ?", (rel_path,))

    def upsert_file_parse(
        self,
        file_rec: FileRecord,
        symbols: list[SymbolRecord],
        references: list[RawReference],
    ) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM files WHERE rel_path = ?", (file_rec.rel_path,))
            self.conn.execute(
                """
                INSERT INTO files (rel_path, sha256, language, is_test, mtime)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    file_rec.rel_path,
                    file_rec.sha256,
                    file_rec.language,
                    1 if file_rec.is_test else 0,
                    float(getattr(file_rec, "mtime", 0.0) or 0.0),
                ),
            )

            if symbols:
                self.conn.executemany(
                    """
                    INSERT OR REPLACE INTO symbols (
                        id, rel_path, name, qualified_name, kind,
                        start_line, end_line, is_test, signature
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                            1 if getattr(s, "is_test", file_rec.is_test) else 0,
                            getattr(s, "signature", "") or "",
                        )
                        for s in symbols
                    ],
                )

            if references:
                self.conn.executemany(
                    """
                    INSERT INTO raw_references (
                        source_symbol_id, source_file, target_name, edge_type, module_hint, line_no
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            r.source_symbol_id,
                            getattr(r, "source_file", None) or getattr(r, "source_rel_path", file_rec.rel_path),
                            r.target_name,
                            r.edge_type.value if hasattr(r.edge_type, "value") else str(r.edge_type),
                            getattr(r, "module_hint", None),
                            getattr(r, "line_no", 1),
                        )
                        for r in references
                    ],
                )

    def replace_structural_edges(self, edges: list[ResolvedEdge]) -> int:
        with self.conn:
            self.conn.execute(
                "DELETE FROM edges WHERE edge_type != ?", (EdgeType.CO_CHANGES.value,)
            )
            if edges:
                self.conn.executemany(
                    """
                    INSERT INTO edges (
                        source_id, target_id, edge_type, confidence, weight, line_no
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            e.source_id,
                            e.target_id,
                            e.edge_type.value if hasattr(e.edge_type, "value") else str(e.edge_type),
                            float(e.confidence),
                            float(getattr(e, "weight", e.confidence)),
                            getattr(e, "line_no", None),
                        )
                        for e in edges
                    ],
                )
        return len(edges)

    def replace_co_change_edges(self, edges: list[ResolvedEdge]) -> int:
        with self.conn:
            self.conn.execute(
                "DELETE FROM edges WHERE edge_type = ?", (EdgeType.CO_CHANGES.value,)
            )
            if edges:
                self.conn.executemany(
                    """
                    INSERT INTO edges (
                        source_id, target_id, edge_type, confidence, weight, line_no
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            e.source_id,
                            e.target_id,
                            EdgeType.CO_CHANGES.value,
                            float(e.confidence),
                            float(getattr(e, "weight", e.confidence)),
                            getattr(e, "line_no", None),
                        )
                        for e in edges
                    ],
                )
        return len(edges)

    def fetch_all_symbols(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT id, rel_path, rel_path AS file_path, name, qualified_name, kind,
                   start_line, end_line, is_test, signature
            FROM symbols
            ORDER BY rel_path, start_line
            """
        ).fetchall()

    def fetch_all_raw_references(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT id, source_symbol_id, source_file, source_file AS source_rel_path,
                   target_name, edge_type, module_hint, line_no
            FROM raw_references
            """
        ).fetchall()

    def fetch_all_references(self) -> list[sqlite3.Row]:
        return self.fetch_all_raw_references()

    def fetch_all_edges(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT id, source_id, target_id, edge_type,
                   confidence, COALESCE(weight, confidence, 1.0) AS weight, line_no
            FROM edges
            """
        ).fetchall()

    def fetch_symbol_by_id(self, symbol_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT id, rel_path, rel_path AS file_path, name, qualified_name, kind,
                   start_line, end_line, is_test, signature
            FROM symbols
            WHERE id = ?
            """,
            (symbol_id,),
        ).fetchone()

    def find_symbols_by_name(self, query: str) -> list[sqlite3.Row]:
        exact = self.conn.execute(
            """
            SELECT id, rel_path, rel_path AS file_path, name, qualified_name, kind,
                   start_line, end_line, is_test, signature
            FROM symbols
            WHERE id = ? OR qualified_name = ? OR name = ?
            """,
            (query, query, query),
        ).fetchall()
        if exact:
            return exact

        like_pattern = f"%{query}%"
        return self.conn.execute(
            """
            SELECT id, rel_path, rel_path AS file_path, name, qualified_name, kind,
                   start_line, end_line, is_test, signature
            FROM symbols
            WHERE name LIKE ? OR qualified_name LIKE ? OR id LIKE ?
            ORDER BY length(name) ASC
            """,
            (like_pattern, like_pattern, like_pattern),
        ).fetchall()

    def find_symbols_by_query(self, query: str) -> list[sqlite3.Row]:
        return self.find_symbols_by_name(query)

    def find_symbols_overlapping_lines(
        self, rel_path: str, start_line: int, end_line: int
    ) -> list[sqlite3.Row]:
        specific = self.conn.execute(
            """
            SELECT id, rel_path, rel_path AS file_path, name, qualified_name, kind,
                   start_line, end_line, is_test, signature
            FROM symbols
            WHERE rel_path = ?
              AND kind != 'module'
              AND start_line <= ?
              AND end_line >= ?
            ORDER BY (end_line - start_line) ASC, start_line ASC
            """,
            (rel_path, end_line, start_line),
        ).fetchall()
        if specific:
            return specific

        return self.conn.execute(
            """
            SELECT id, rel_path, rel_path AS file_path, name, qualified_name, kind,
                   start_line, end_line, is_test, signature
            FROM symbols
            WHERE rel_path = ?
              AND start_line <= ?
              AND end_line >= ?
            ORDER BY start_line ASC
            """,
            (rel_path, end_line, start_line),
        ).fetchall()

    def search_symbols_by_tokens(
        self, tokens: list[str], limit: int = 4
    ) -> list[sqlite3.Row]:
        cleaned = [t.strip() for t in tokens if t and t.strip()]
        if not cleaned:
            return []

        scored: dict[str, tuple[int, sqlite3.Row]] = {}
        for tok in cleaned:
            like_pattern = f"%{tok}%"
            rows = self.conn.execute(
                """
                SELECT id, rel_path, rel_path AS file_path, name, qualified_name, kind,
                       start_line, end_line, is_test, signature
                FROM symbols
                WHERE name LIKE ? OR qualified_name LIKE ? OR rel_path LIKE ?
                """,
                (like_pattern, like_pattern, like_pattern),
            ).fetchall()
            for r in rows:
                sid = str(r["id"])
                score = 1
                if str(r["name"]).lower() == tok.lower():
                    score += 5
                elif tok.lower() in str(r["name"]).lower():
                    score += 2
                if str(r["kind"]) != "module":
                    score += 1
                prev_score = scored[sid][0] if sid in scored else 0
                scored[sid] = (prev_score + score, r)

        ranked = sorted(scored.values(), key=lambda item: (-item[0], len(str(item[1]["name"]))))
        return [row for _, row in ranked[:limit]]

    def get_stats(self) -> dict[str, Any]:
        files_count = self.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        symbols_count = self.conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        edges_count = self.conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        tests_count = self.conn.execute(
            "SELECT COUNT(*) FROM symbols WHERE is_test = 1"
        ).fetchone()[0]
        return {
            "files": int(files_count),
            "symbols": int(symbols_count),
            "edges": int(edges_count),
            "test_symbols": int(tests_count),
        }

    def close(self) -> None:
        self.conn.close()