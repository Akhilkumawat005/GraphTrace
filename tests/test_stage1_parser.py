from pathlib import Path
from graphtrace.parser import CodeParser
from graphtrace.scanner import scan_repository
from graphtrace.storage import GraphStorage
from graphtrace.models import EdgeType, SymbolKind


def test_incremental_python_and_ts_indexing(tmp_path: Path) -> None:
    # 1. Create sample Python & TypeScript files
    py_file = tmp_path / "auth.py"
    py_file.write_text(
        "from db import fetch_user\n\n"
        "class TokenManager:\n"
        "    def verify_token(self, token: str) -> bool:\n"
        "        user = fetch_user(token)\n"
        "        return user is not None\n",
        encoding="utf-8",
    )

    ts_test = tmp_path / "auth.test.ts"
    ts_test.write_text(
        "import { verifyToken } from './auth';\n"
        "test('validates token', () => {\n"
        "  verifyToken('abc');\n"
        "});\n",
        encoding="utf-8",
    )

    db_path = tmp_path / ".graphtrace" / "index.db"
    storage = GraphStorage(db_path)
    parser = CodeParser()

    # Initial cold index
    discovered = scan_repository(tmp_path)
    assert len(discovered) == 2

    for abs_path, rec in discovered:
        res = parser.parse_file(abs_path, rec.rel_path, rec.language, rec.is_test)
        storage.upsert_file_parse(rec, res.symbols, res.references)

    stats = storage.get_stats()
    assert stats["files"] == 2
    assert stats["symbols"] >= 5  # 2 modules + TokenManager + verify_token + test_L2

    # Verify method symbol & call reference extraction
    method_row = storage.conn.execute(
        "SELECT * FROM symbols WHERE id = 'auth.py::TokenManager.verify_token'"
    ).fetchone()
    assert method_row is not None
    assert method_row["kind"] == SymbolKind.METHOD.value
    assert method_row["start_line"] == 4

    call_row = storage.conn.execute(
        "SELECT * FROM raw_references WHERE source_symbol_id = 'auth.py::TokenManager.verify_token' AND target_name = 'fetch_user'"
    ).fetchone()
    assert call_row is not None
    assert call_row["edge_type"] == EdgeType.CALLS.value

    # Verify cache hit on unchanged re-scan
    hashes = storage.get_indexed_hashes()
    discovered_2 = scan_repository(tmp_path)
    re_parsed = [rec for _, rec in discovered_2 if hashes.get(rec.rel_path, (0, ""))[1] != rec.sha256]
    assert len(re_parsed) == 0

    storage.close()