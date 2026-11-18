from __future__ import annotations

from collections import deque
import rustworkx as rx

from graphtrace.models import DiffImpactReport, ImpactItem
from graphtrace.storage import GraphStorage


class DependencyGraph:
    """In-memory rustworkx directed graph for reverse blast-radius, upstream trace & test selection."""

    def __init__(self, storage: GraphStorage) -> None:
        self.storage = storage
        self.graph: rx.PyDiGraph = rx.PyDiGraph()
        self.id_to_idx: dict[str, int] = {}
        self.idx_to_meta: dict[int, dict] = {}
        self._build()

    def _build(self) -> None:
        for row in self.storage.fetch_all_symbols():
            meta = dict(row)
            idx = self.graph.add_node(meta["id"])
            self.id_to_idx[meta["id"]] = idx
            self.idx_to_meta[idx] = meta

        for edge in self.storage.fetch_all_edges():
            src_idx = self.id_to_idx.get(edge["source_id"])
            tgt_idx = self.id_to_idx.get(edge["target_id"])
            if src_idx is not None and tgt_idx is not None and src_idx != tgt_idx:
                self.graph.add_edge(
                    src_idx,
                    tgt_idx,
                    {
                        "edge_type": edge["edge_type"],
                        "weight": float(edge["weight"]),
                        "confidence": float(edge["confidence"]),
                        "line_no": edge["line_no"],
                    },
                )

    def compute_blast_radius(
        self,
        seed_symbol_ids: list[str],
        max_depth: int = 5,
        decay: float = 0.65,
    ) -> list[ImpactItem]:
        """Compute weighted reverse-reachability (downstream dependents affected if seeds change)."""
        best_scores: dict[int, float] = {}
        best_hops: dict[int, int] = {}
        best_paths: dict[int, list[str]] = {}

        queue: deque[tuple[int, int, float, list[str]]] = deque()

        for sid in seed_symbol_ids:
            idx = self.id_to_idx.get(sid)
            if idx is not None:
                best_scores[idx] = 1.0
                best_hops[idx] = 0
                best_paths[idx] = [sid]
                queue.append((idx, 0, 1.0, [sid]))

        while queue:
            curr_idx, depth, curr_score, path = queue.popleft()
            if depth >= max_depth:
                continue

            for caller_idx, _, edge_data in self.graph.in_edges(curr_idx):
                edge_factor = edge_data["weight"] * edge_data["confidence"] * decay
                next_score = curr_score * edge_factor
                if next_score < 0.02:
                    continue

                prev_best = best_scores.get(caller_idx, 0.0)
                if next_score > prev_best:
                    caller_id = self.idx_to_meta[caller_idx]["id"]
                    new_path = path + [caller_id]
                    best_scores[caller_idx] = next_score
                    best_hops[caller_idx] = depth + 1
                    best_paths[caller_idx] = new_path
                    queue.append((caller_idx, depth + 1, next_score, new_path))

        results: list[ImpactItem] = []
        for idx, raw_score in best_scores.items():
            hop = best_hops[idx]
            if hop == 0:
                continue
            meta = self.idx_to_meta[idx]
            results.append(
                ImpactItem(
                    symbol_id=meta["id"],
                    rel_path=meta["rel_path"],
                    name=meta["name"],
                    kind=meta["kind"],
                    hop_distance=hop,
                    score=round(min(100.0, raw_score * 100.0), 2),
                    via_path=best_paths[idx],
                )
            )

        results.sort(key=lambda item: (-item.score, item.hop_distance, item.symbol_id))
        return results

    def get_upstream_dependencies(
        self,
        seed_symbol_ids: list[str],
        max_depth: int = 2,
    ) -> list[ImpactItem]:
        """Traverse outgoing edges (callees/imports that the seed symbols depend on)."""
        visited_hop: dict[int, int] = {}
        visited_path: dict[int, list[str]] = {}
        queue: deque[tuple[int, int, list[str]]] = deque()

        for sid in seed_symbol_ids:
            idx = self.id_to_idx.get(sid)
            if idx is not None:
                visited_hop[idx] = 0
                visited_path[idx] = [sid]
                queue.append((idx, 0, [sid]))

        while queue:
            curr_idx, depth, path = queue.popleft()
            if depth >= max_depth:
                continue

            for _, callee_idx, _ in self.graph.out_edges(curr_idx):
                if callee_idx not in visited_hop:
                    callee_id = self.idx_to_meta[callee_idx]["id"]
                    new_path = path + [callee_id]
                    visited_hop[callee_idx] = depth + 1
                    visited_path[callee_idx] = new_path
                    queue.append((callee_idx, depth + 1, new_path))

        results: list[ImpactItem] = []
        for idx, hop in visited_hop.items():
            if hop == 0:
                continue
            meta = self.idx_to_meta[idx]
            results.append(
                ImpactItem(
                    symbol_id=meta["id"],
                    rel_path=meta["rel_path"],
                    name=meta["name"],
                    kind=meta["kind"],
                    hop_distance=hop,
                    score=round(100.0 / hop, 2),
                    via_path=visited_path[idx],
                )
            )
        results.sort(key=lambda item: (item.hop_distance, item.symbol_id))
        return results

    def build_diff_report(
        self,
        diff_target: str,
        modified_files: list[str],
        mutated_symbols: list[str],
        max_depth: int = 5,
    ) -> DiffImpactReport:
        impacts = self.compute_blast_radius(mutated_symbols, max_depth=max_depth)

        test_items: list[ImpactItem] = []
        seen_test_ids: set[str] = set()

        for sid in mutated_symbols:
            idx = self.id_to_idx.get(sid)
            if idx is None:
                continue
            meta = self.idx_to_meta[idx]
            if meta["kind"] == "test" or bool(meta["is_test"]):
                seen_test_ids.add(sid)
                test_items.append(
                    ImpactItem(
                        symbol_id=sid,
                        rel_path=meta["rel_path"],
                        name=meta["name"],
                        kind=meta["kind"],
                        hop_distance=0,
                        score=100.0,
                        via_path=[sid],
                    )
                )

        for item in impacts:
            if item.symbol_id in seen_test_ids:
                continue
            idx = self.id_to_idx.get(item.symbol_id)
            meta = self.idx_to_meta[idx] if idx is not None else {}
            if item.kind == "test" or bool(meta.get("is_test")):
                seen_test_ids.add(item.symbol_id)
                test_items.append(item)

        py_test_files = sorted(
            {t.rel_path for t in test_items if t.rel_path.endswith(".py")}
        )
        ts_test_files = sorted(
            {t.rel_path for t in test_items if not t.rel_path.endswith(".py")}
        )

        cmds: list[str] = []
        if py_test_files:
            cmds.append(f"pytest {' '.join(py_test_files)}")
        if ts_test_files:
            cmds.append(f"npx vitest run {' '.join(ts_test_files)}")

        return DiffImpactReport(
            diff_target=diff_target,
            modified_files=modified_files,
            mutated_symbols=mutated_symbols,
            impacted_symbols=impacts,
            impacted_tests=test_items,
            suggested_test_command=" && ".join(cmds) if cmds else None,
        )