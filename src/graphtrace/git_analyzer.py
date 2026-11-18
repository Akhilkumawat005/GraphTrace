from __future__ import annotations

import re
import subprocess
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

from graphtrace.models import EdgeType, ResolvedEdge
from graphtrace.resolver import EDGE_BASE_WEIGHTS
from graphtrace.storage import GraphStorage

HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


class GitAnalyzer:
    """Mines evolutionary co-change coupling and maps unified git diffs to AST symbols."""

    def __init__(self, repo_root: Path, storage: GraphStorage) -> None:
        self.repo_root = repo_root.resolve()
        self.storage = storage

    def _run_git(self, args: list[str]) -> str | None:
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            if proc.returncode != 0:
                return None
            return proc.stdout
        except OSError:
            return None

    def is_git_repo(self) -> bool:
        out = self._run_git(["rev-parse", "--is-inside-work-tree"])
        return out is not None and out.strip() == "true"

    def mine_co_changes(
        self,
        max_commits: int = 500,
        max_files_per_commit: int = 15,
        min_co_occurrences: int = 2,
        min_confidence: float = 0.25,
    ) -> list[ResolvedEdge]:
        """Extract historical co-change edges between indexed files from git log."""
        if not self.is_git_repo():
            return []

        log_out = self._run_git(
            [
                "log",
                f"-n{max_commits}",
                "--no-merges",
                "--pretty=format:---COMMIT---",
                "--name-only",
            ]
        )
        if not log_out:
            return []

        indexed_files = {row["rel_path"] for row in self.storage.fetch_all_files()}
        if not indexed_files:
            return []

        file_commit_counts: Counter[str] = Counter()
        pair_counts: Counter[tuple[str, str]] = Counter()

        for block in log_out.split("---COMMIT---"):
            changed = sorted(
                {
                    line.strip().replace("\\", "/")
                    for line in block.splitlines()
                    if line.strip() and line.strip().replace("\\", "/") in indexed_files
                }
            )
            if not changed or len(changed) > max_files_per_commit:
                continue

            for f in changed:
                file_commit_counts[f] += 1

            for file_a, file_b in combinations(changed, 2):
                pair_counts[(file_a, file_b)] += 1

        edges: list[ResolvedEdge] = []
        for (file_a, file_b), shared in pair_counts.items():
            if shared < min_co_occurrences:
                continue

            conf_a_to_b = shared / max(1, file_commit_counts[file_a])
            conf_b_to_a = shared / max(1, file_commit_counts[file_b])

            # If B frequently changes when A changes, B has an evolutionary dependency on A
            if conf_a_to_b >= min_confidence:
                edges.append(
                    ResolvedEdge(
                        source_id=f"{file_b}::__module__",
                        target_id=f"{file_a}::__module__",
                        edge_type=EdgeType.CO_CHANGES,
                        weight=EDGE_BASE_WEIGHTS[EdgeType.CO_CHANGES],
                        confidence=round(min(1.0, conf_a_to_b), 3),
                        line_no=0,
                    )
                )
            if conf_b_to_a >= min_confidence:
                edges.append(
                    ResolvedEdge(
                        source_id=f"{file_a}::__module__",
                        target_id=f"{file_b}::__module__",
                        edge_type=EdgeType.CO_CHANGES,
                        weight=EDGE_BASE_WEIGHTS[EdgeType.CO_CHANGES],
                        confidence=round(min(1.0, conf_b_to_a), 3),
                        line_no=0,
                    )
                )

        return edges

    def extract_diff_line_ranges(self, diff_spec: str | None = None) -> dict[str, list[tuple[int, int]]]:
        """
        Parse zero-context git diff output into {rel_path: [(start_line, end_line), ...]}.
        If diff_spec is None, diffs working tree + staged changes against HEAD (or unstaged if no HEAD).
        """
        if not self.is_git_repo():
            return {}

        args = ["diff", "--unified=0", "--no-color"]
        if diff_spec:
            args.append(diff_spec)
        else:
            has_head = self._run_git(["rev-parse", "--verify", "HEAD"]) is not None
            if has_head:
                args.append("HEAD")

        diff_out = self._run_git(args)
        if not diff_out:
            return {}

        ranges_by_file: dict[str, list[tuple[int, int]]] = defaultdict(list)
        current_file: str | None = None

        for line in diff_out.splitlines():
            if line.startswith("+++ b/"):
                current_file = line[6:].strip().replace("\\", "/")
            elif line.startswith("+++ /dev/null"):
                current_file = None
            elif line.startswith("@@ ") and current_file:
                match = HUNK_HEADER_RE.match(line)
                if match:
                    start = int(match.group(1))
                    count = int(match.group(2)) if match.group(2) is not None else 1
                    if count == 0:
                        # Pure deletion: map to the surrounding line in the new file
                        anchor = max(1, start)
                        ranges_by_file[current_file].append((anchor, anchor))
                    else:
                        ranges_by_file[current_file].append((start, start + count - 1))

        return dict(ranges_by_file)

    def map_diff_to_symbols(self, diff_spec: str | None = None) -> tuple[list[str], list[str]]:
        """Return (modified_rel_paths, mutated_symbol_ids) for the requested git diff."""
        ranges_by_file = self.extract_diff_line_ranges(diff_spec=diff_spec)
        indexed_files = {row["rel_path"] for row in self.storage.fetch_all_files()}

        modified_files: list[str] = []
        mutated_symbols: list[str] = []
        seen_symbols: set[str] = set()

        for rel_path, hunks in ranges_by_file.items():
            if rel_path not in indexed_files:
                continue
            modified_files.append(rel_path)

            for start_line, end_line in hunks:
                matched = self.storage.find_symbols_overlapping_lines(
                    rel_path, start_line, end_line
                )
                # Prefer non-class container if a concrete method/function inside it matched
                non_class = [row for row in matched if row["kind"] != "class"]
                chosen = non_class if non_class else matched
                for row in chosen:
                    sid = row["id"]
                    if sid not in seen_symbols:
                        seen_symbols.add(sid)
                        mutated_symbols.append(sid)

            # Also include module node if any symbol in the file changed (captures module-level imports/co-changes)
            mod_id = f"{rel_path}::__module__"
            if mod_id not in seen_symbols:
                seen_symbols.add(mod_id)
                mutated_symbols.append(mod_id)

        return sorted(modified_files), mutated_symbols