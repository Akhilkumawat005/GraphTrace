from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from graphtrace.git_analyzer import GitAnalyzer
from graphtrace.graph_engine import DependencyGraph
from graphtrace.parser import CodeParser
from graphtrace.resolver import SymbolResolver
from graphtrace.scanner import scan_repository
from graphtrace.storage import GraphStorage


def _generate_synthetic_repo(root: Path, num_modules: int = 40) -> tuple[str, set[str], int]:
    """
    Generate a realistic multi-layered repository (core -> services -> controllers -> tests)
    with known ground-truth transitive dependents for `core_0.py::compute_core_0`.
    """
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir(parents=True)

    # Layer 0: Core modules
    for i in range(num_modules):
        (root / "src" / f"core_{i}.py").write_text(
            f"def compute_core_{i}(x: int) -> int:\n"
            f"    return x * {i + 2}\n\n"
            f"def helper_core_{i}(y: int) -> int:\n"
            f"    return compute_core_{i}(y) + 1\n",
            encoding="utf-8",
        )

    # Layer 1: Services importing core_i
    for i in range(num_modules):
        (root / "src" / f"service_{i}.py").write_text(
            f"from src.core_{i} import helper_core_{i}\n\n"
            f"def process_service_{i}(val: int) -> dict:\n"
            f"    res = helper_core_{i}(val)\n"
            f"    return {{'result': res}}\n",
            encoding="utf-8",
        )

    # Layer 2: Controllers importing service_i (does NOT mention compute_core_i by name)
    for i in range(num_modules):
        (root / "src" / f"controller_{i}.py").write_text(
            f"from src.service_{i} import process_service_{i}\n\n"
            f"def handle_endpoint_{i}(payload: int) -> int:\n"
            f"    out = process_service_{i}(payload)\n"
            f"    return out['result']\n",
            encoding="utf-8",
        )

    # Layer 3: Unit tests testing controller_i
    for i in range(num_modules):
        (root / "tests" / f"test_endpoint_{i}.py").write_text(
            f"from src.controller_{i} import handle_endpoint_{i}\n\n"
            f"def test_endpoint_{i}_ok():\n"
            f"    assert handle_endpoint_{i}(5) > 0\n",
            encoding="utf-8",
        )

    ground_truth_dependents = {
        "src/core_0.py::helper_core_0",
        "src/service_0.py::process_service_0",
        "src/controller_0.py::handle_endpoint_0",
        "tests/test_endpoint_0.py::test_endpoint_0_ok",
    }
    total_tests = num_modules
    return "src/core_0.py::compute_core_0", ground_truth_dependents, total_tests


def _naive_lexical_baseline(root: Path, symbol_name: str) -> set[str]:
    """Baseline: grep/keyword match for `symbol_name` across files (simulates lexical/single-hop lookup)."""
    matched_files: set[str] = set()
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if symbol_name in text:
            matched_files.add(path.relative_to(root).as_posix())
    return matched_files


def run_benchmarks() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        seed_id, ground_truth, total_tests = _generate_synthetic_repo(repo, num_modules=50)
        # 200 Python files (~1,200+ lines, 450+ symbols)

        db_path = repo / ".graphtrace" / "index.db"
        storage = GraphStorage(db_path)
        parser = CodeParser()

        # 1. Cold Index Latency
        t0 = time.perf_counter()
        discovered = scan_repository(repo)
        for abs_path, rec in discovered:
            parsed = parser.parse_file(abs_path, rec.rel_path, rec.language, rec.is_test)
            storage.upsert_file_parse(rec, parsed.symbols, parsed.references)
        resolver = SymbolResolver(repo, storage)
        storage.replace_structural_edges(resolver.resolve_all())
        cold_index_ms = (time.perf_counter() - t0) * 1000

        # 2. Incremental Re-index Latency (1 modified file out of 200)
        target_file = repo / "src" / "core_0.py"
        target_file.write_text(
            target_file.read_text(encoding="utf-8") + "\n# minor edit\n",
            encoding="utf-8",
        )
        t1 = time.perf_counter()
        hashes = storage.get_indexed_hashes()
        for abs_path, rec in scan_repository(repo):
            if hashes.get(rec.rel_path, (0, ""))[1] != rec.sha256:
                parsed = parser.parse_file(abs_path, rec.rel_path, rec.language, rec.is_test)
                storage.upsert_file_parse(rec, parsed.symbols, parsed.references)
        storage.replace_structural_edges(SymbolResolver(repo, storage).resolve_all())
        incremental_index_ms = (time.perf_counter() - t1) * 1000

        # 3. Blast-Radius Query Latency & Multi-Hop Recall
        t2 = time.perf_counter()
        dep_graph = DependencyGraph(storage)
        impacts = dep_graph.compute_blast_radius([seed_id], max_depth=5)
        query_ms = (time.perf_counter() - t2) * 1000

        retrieved_ids = {item.symbol_id for item in impacts}
        gt_hits = len(ground_truth & retrieved_ids)
        graphtrace_recall = gt_hits / len(ground_truth)

        # Compare against naive lexical baseline (only finds files mentioning `compute_core_0`)
        baseline_files = _naive_lexical_baseline(repo, "compute_core_0")
        gt_files = {sid.split("::")[0] for sid in ground_truth}
        baseline_recall = len(gt_files & baseline_files) / len(gt_files)

        # 4. Selective Test Reduction Ratio
        report = dep_graph.build_diff_report("BENCH", ["src/core_0.py"], [seed_id])
        selected_test_files = {t.rel_path for t in report.impacted_tests}
        test_reduction_pct = (1.0 - (len(selected_test_files) / total_tests)) * 100.0

        stats = storage.get_stats()
        db_size_kb = db_path.stat().st_size / 1024.0
        storage.close()

        return {
            "files_indexed": stats["files"],
            "symbols_extracted": stats["symbols"],
            "resolved_edges": stats["resolved_edges"],
            "sqlite_size_kb": round(db_size_kb, 1),
            "cold_index_ms": round(cold_index_ms, 2),
            "incremental_1file_ms": round(incremental_index_ms, 2),
            "blast_radius_query_ms": round(query_ms, 2),
            "graphtrace_multihop_recall": round(graphtrace_recall * 100, 1),
            "naive_lexical_recall": round(baseline_recall * 100, 1),
            "selected_tests_count": len(selected_test_files),
            "total_tests_count": total_tests,
            "ci_test_reduction_pct": round(test_reduction_pct, 1),
        }


if __name__ == "__main__":
    results = run_benchmarks()
    print(json.dumps(results, indent=2))