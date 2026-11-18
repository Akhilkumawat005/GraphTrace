from pathlib import Path
from graphtrace.graph_engine import DependencyGraph
from graphtrace.parser import CodeParser
from graphtrace.resolver import SymbolResolver
from graphtrace.scanner import scan_repository
from graphtrace.storage import GraphStorage


def test_cross_file_resolution_and_blast_radius(tmp_path: Path) -> None:
    # Layer 0: Core utility
    (tmp_path / "crypto.py").write_text(
        "def decode_jwt(raw: str) -> dict:\n"
        "    return {'sub': raw}\n",
        encoding="utf-8",
    )

    # Layer 1: Service importing Layer 0
    (tmp_path / "auth_service.py").write_text(
        "from crypto import decode_jwt\n\n"
        "def authenticate_request(header: str) -> str:\n"
        "    claims = decode_jwt(header)\n"
        "    return claims['sub']\n",
        encoding="utf-8",
    )

    # Layer 2: API route importing Layer 1
    (tmp_path / "routes.py").write_text(
        "from auth_service import authenticate_request\n\n"
        "def get_profile(auth_header: str) -> dict:\n"
        "    user_id = authenticate_request(auth_header)\n"
        "    return {'id': user_id}\n",
        encoding="utf-8",
    )

    # Layer 3: Test file testing Layer 2
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_routes.py").write_text(
        "from routes import get_profile\n\n"
        "def test_get_profile_endpoint():\n"
        "    res = get_profile('user_123')\n"
        "    assert res['id'] == 'user_123'\n",
        encoding="utf-8",
    )

    db_path = tmp_path / ".graphtrace" / "index.db"
    storage = GraphStorage(db_path)
    parser = CodeParser()

    for abs_path, rec in scan_repository(tmp_path):
        parsed = parser.parse_file(abs_path, rec.rel_path, rec.language, rec.is_test)
        storage.upsert_file_parse(rec, parsed.symbols, parsed.references)

    resolver = SymbolResolver(tmp_path, storage)
    edges = resolver.resolve_all()
    edge_count = storage.replace_structural_edges(edges)
    assert edge_count >= 6

    dep_graph = DependencyGraph(storage)
    impacts = dep_graph.compute_blast_radius(["crypto.py::decode_jwt"], max_depth=5)

    impacted_ids = [i.symbol_id for i in impacts]
    assert "auth_service.py::authenticate_request" in impacted_ids
    assert "routes.py::get_profile" in impacted_ids
    assert "tests/test_routes.py::test_get_profile_endpoint" in impacted_ids

    # Verify hop ordering: hop 1 (auth_service) > hop 2 (routes) > hop 3 (test)
    scores = {i.symbol_id: i.score for i in impacts}
    assert scores["auth_service.py::authenticate_request"] > scores["routes.py::get_profile"]
    assert scores["routes.py::get_profile"] > scores["tests/test_routes.py::test_get_profile_endpoint"]

    storage.close()