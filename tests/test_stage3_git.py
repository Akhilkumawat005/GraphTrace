from pathlib import Path
import subprocess
from graphtrace.git_analyzer import GitAnalyzer
from graphtrace.graph_engine import DependencyGraph
from graphtrace.parser import CodeParser
from graphtrace.resolver import SymbolResolver
from graphtrace.scanner import scan_repository
from graphtrace.storage import GraphStorage


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


def test_git_co_change_mining_and_diff_impact(tmp_path: Path) -> None:
    # Initialize a real Git repo in tmp_path
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "dev@example.com")
    _git(tmp_path, "config", "user.name", "Test Dev")

    # Commit 1: Initial files (pricing.py + checkout.py + test_checkout.py + unrelated_config.py)
    (tmp_path / "pricing.py").write_text(
        "def calculate_tax(amount: float) -> float:\n"
        "    return amount * 0.18\n",
        encoding="utf-8",
    )
    (tmp_path / "checkout.py").write_text(
        "from pricing import calculate_tax\n\n"
        "def finalize_order(subtotal: float) -> float:\n"
        "    return subtotal + calculate_tax(subtotal)\n",
        encoding="utf-8",
    )
    (tmp_path / "test_checkout.py").write_text(
        "from checkout import finalize_order\n\n"
        "def test_finalize_order():\n"
        "    assert finalize_order(100.0) == 118.0\n",
        encoding="utf-8",
    )
    (tmp_path / "billing_rules.py").write_text("TAX_VERSION = 1\n", encoding="utf-8")

    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "Initial commit")

    # Commit 2 & 3: Co-change pricing.py and billing_rules.py twice (no direct AST import between them)
    for ver in (2, 3):
        (tmp_path / "pricing.py").write_text(
            f"def calculate_tax(amount: float) -> float:\n"
            f"    # v{ver}\n"
            f"    return amount * 0.18\n",
            encoding="utf-8",
        )
        (tmp_path / "billing_rules.py").write_text(f"TAX_VERSION = {ver}\n", encoding="utf-8")
        _git(tmp_path, "add", "pricing.py", "billing_rules.py")
        _git(tmp_path, "commit", "-m", f"Update tax version {ver}")

    # Working-tree modification in pricing.py (uncommitted change)
    (tmp_path / "pricing.py").write_text(
        "def calculate_tax(amount: float) -> float:\n"
        "    # uncommitted GST tweak\n"
        "    return amount * 0.20\n",
        encoding="utf-8",
    )

    db_path = tmp_path / ".graphtrace" / "index.db"
    storage = GraphStorage(db_path)
    parser = CodeParser()

    for abs_path, rec in scan_repository(tmp_path):
        parsed = parser.parse_file(abs_path, rec.rel_path, rec.language, rec.is_test)
        storage.upsert_file_parse(rec, parsed.symbols, parsed.references)

    resolver = SymbolResolver(tmp_path, storage)
    storage.replace_structural_edges(resolver.resolve_all())

    git_analyzer = GitAnalyzer(tmp_path, storage)
    co_changes = git_analyzer.mine_co_changes(min_co_occurrences=2, min_confidence=0.5)
    assert len(co_changes) >= 2
    storage.replace_co_change_edges(co_changes)

    modified_files, mutated_symbols = git_analyzer.map_diff_to_symbols()
    assert modified_files == ["pricing.py"]
    assert "pricing.py::calculate_tax" in mutated_symbols

    dep_graph = DependencyGraph(storage)
    report = dep_graph.build_diff_report("WORKING_TREE", modified_files, mutated_symbols)

    impacted_ids = {item.symbol_id for item in report.impacted_symbols}
    # Structural transitive propagation
    assert "checkout.py::finalize_order" in impacted_ids
    assert "test_checkout.py::test_finalize_order" in impacted_ids
    # Evolutionary Git co-change propagation
    assert "billing_rules.py::__module__" in impacted_ids
    # Minimal test command generation
    assert report.suggested_test_command == "pytest test_checkout.py"

    storage.close()