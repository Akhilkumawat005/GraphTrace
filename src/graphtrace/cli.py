from __future__ import annotations

import time
from pathlib import Path
import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from graphtrace.git_analyzer import GitAnalyzer
from graphtrace.graph_engine import DependencyGraph
from graphtrace.mcp_server import GraphTraceMCPServer
from graphtrace.parser import CodeParser
from graphtrace.resolver import SymbolResolver
from graphtrace.scanner import scan_repository
from graphtrace.storage import GraphStorage
from graphtrace.subgraph_rag import SubgraphRAG

app = typer.Typer(help="GraphTrace: Structural & Evolutionary Codebase Impact Engine")
console = Console()


def _refresh_index(repo_root: Path, storage: GraphStorage, force: bool = False) -> tuple[int, int, int]:
    parser = CodeParser()
    discovered = scan_repository(repo_root)
    active_paths = {rec.rel_path for _, rec in discovered}
    pruned_count = storage.prune_deleted_files(active_paths)

    existing_hashes = {} if force else storage.get_indexed_hashes()
    parsed_count = 0
    skipped_count = 0

    for abs_path, file_rec in discovered:
        cached = existing_hashes.get(file_rec.rel_path)
        if cached and cached[1] == file_rec.sha256:
            skipped_count += 1
            continue

        result = parser.parse_file(
            abs_path=abs_path,
            rel_path=file_rec.rel_path,
            lang=file_rec.language,
            is_test_file=file_rec.is_test,
        )
        storage.upsert_file_parse(file_rec, result.symbols, result.references)
        parsed_count += 1

    resolver = SymbolResolver(repo_root, storage)
    resolved_edges = resolver.resolve_all()
    storage.replace_structural_edges(resolved_edges)

    git_analyzer = GitAnalyzer(repo_root, storage)
    co_changes = git_analyzer.mine_co_changes()
    storage.replace_co_change_edges(co_changes)

    return parsed_count, skipped_count, pruned_count


@app.command()
def index(
    repo_path: Path = typer.Argument(Path("."), help="Path to target Git repository"),
    force: bool = typer.Option(False, "--force", "-f", help="Force full re-index ignoring cached hashes"),
) -> None:
    """Incrementally index files, resolve structural dependencies, and mine Git co-changes."""
    repo_root = repo_path.resolve()
    if not repo_root.is_dir():
        console.print(f"[red]Error:[/red] Directory not found: {repo_root}")
        raise typer.Exit(code=1)

    t0 = time.perf_counter()
    db_path = repo_root / ".graphtrace" / "index.db"
    storage = GraphStorage(db_path)

    try:
        parsed_count, skipped_count, pruned_count = _refresh_index(repo_root, storage, force=force)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        stats = storage.get_stats()

        table = Table(title=f"GraphTrace Index & Graph Summary ({elapsed_ms:.1f} ms)")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="bold green")
        table.add_row("Repository", str(repo_root))
        table.add_row("Files Parsed (New/Modified)", str(parsed_count))
        table.add_row("Files Unchanged (Cache Hit)", str(skipped_count))
        table.add_row("Files Pruned (Deleted)", str(pruned_count))
        table.add_row("Total Indexed Files", str(stats["files"]))
        table.add_row("Total Extracted Symbols", str(stats["symbols"]))
        table.add_row("Total Resolved Edges", str(stats["resolved_edges"]))
        table.add_row("Git Co-Change Edges", str(stats["co_change_edges"]))
        console.print(table)
    finally:
        storage.close()


@app.command()
def trace(
    symbol: str = typer.Argument(..., help="Symbol name or ID (e.g. 'parse_file')"),
    repo_path: Path = typer.Option(Path("."), "--repo", "-r", help="Path to indexed repository"),
    max_depth: int = typer.Option(5, "--depth", "-d", help="Maximum reverse dependency hop depth"),
) -> None:
    """Trace all downstream symbols and tests impacted by modifying a target symbol."""
    repo_root = repo_path.resolve()
    db_path = repo_root / ".graphtrace" / "index.db"
    if not db_path.is_file():
        console.print("[red]Error:[/red] No index found. Run `graphtrace index` first.")
        raise typer.Exit(code=1)

    storage = GraphStorage(db_path)
    try:
        matches = storage.find_symbols_by_query(symbol)
        if not matches:
            console.print(f"[yellow]No symbols matched:[/yellow] {symbol}")
            raise typer.Exit(code=1)

        seed_ids = [row["id"] for row in matches]
        console.print(f"[bold cyan]Seed Symbols ({len(seed_ids)}):[/bold cyan] {', '.join(seed_ids)}")

        t0 = time.perf_counter()
        dep_graph = DependencyGraph(storage)
        impacts = dep_graph.compute_blast_radius(seed_ids, max_depth=max_depth)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        if not impacts:
            console.print("[green]No downstream dependents found.[/green]")
            return

        table = Table(title=f"Downstream Blast Radius ({len(impacts)} impacted in {elapsed_ms:.2f} ms)")
        table.add_column("Score", style="bold magenta", justify="right")
        table.add_column("Hop", justify="center")
        table.add_column("Kind", style="cyan")
        table.add_column("Impacted Symbol ID", style="bold")
        table.add_column("Dependency Chain", style="dim")

        for item in impacts[:25]:
            table.add_row(
                f"{item.score:.1f}",
                str(item.hop_distance),
                item.kind,
                item.symbol_id,
                " -> ".join(item.via_path),
            )
        console.print(table)
    finally:
        storage.close()


@app.command()
def impact(
    diff: str | None = typer.Option(None, "--diff", help="Git diff spec (e.g. 'HEAD~1'). Defaults to uncommitted changes."),
    repo_path: Path = typer.Option(Path("."), "--repo", "-r", help="Path to Git repository"),
    max_depth: int = typer.Option(5, "--depth", "-d", help="Maximum reverse dependency hop depth"),
    json_out: bool = typer.Option(False, "--json", help="Emit machine-readable JSON report"),
) -> None:
    """Analyze a Git diff, map hunks to mutated AST symbols, and select minimal tests to run."""
    repo_root = repo_path.resolve()
    db_path = repo_root / ".graphtrace" / "index.db"
    storage = GraphStorage(db_path)

    try:
        _refresh_index(repo_root, storage, force=False)

        git_analyzer = GitAnalyzer(repo_root, storage)
        if not git_analyzer.is_git_repo():
            console.print(f"[red]Error:[/red] Not a Git repository: {repo_root}")
            raise typer.Exit(code=1)

        t0 = time.perf_counter()
        modified_files, mutated_symbols = git_analyzer.map_diff_to_symbols(diff_spec=diff)
        dep_graph = DependencyGraph(storage)
        report = dep_graph.build_diff_report(
            diff_target=diff or "WORKING_TREE",
            modified_files=modified_files,
            mutated_symbols=mutated_symbols,
            max_depth=max_depth,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000

        if json_out:
            console.print(report.model_dump_json(indent=2))
            return

        if not report.modified_files:
            console.print("[green]No indexed files modified in the specified diff.[/green]")
            return

        console.print(
            Panel(
                f"[bold]Diff Target:[/bold] {report.diff_target}\n"
                f"[bold]Modified Files ({len(report.modified_files)}):[/bold] {', '.join(report.modified_files)}\n"
                f"[bold]Mutated Symbols ({len(report.mutated_symbols)}):[/bold] {', '.join(s for s in report.mutated_symbols if not s.endswith('::__module__')) or 'module-level only'}",
                title=f"Git Diff Impact Summary ({elapsed_ms:.2f} ms)",
                border_style="cyan",
            )
        )

        if report.impacted_symbols:
            table = Table(title="Downstream Impacted Symbols")
            table.add_column("Score", style="bold magenta", justify="right")
            table.add_column("Hop", justify="center")
            table.add_column("Kind", style="cyan")
            table.add_column("Symbol ID", style="bold")
            table.add_column("Chain", style="dim")

            for item in report.impacted_symbols[:20]:
                table.add_row(
                    f"{item.score:.1f}",
                    str(item.hop_distance),
                    item.kind,
                    item.symbol_id,
                    " -> ".join(item.via_path),
                )
            console.print(table)

        if report.suggested_test_command:
            console.print(
                Panel(
                    f"[bold green]{report.suggested_test_command}[/bold green]",
                    title=f"Minimal Selective Test Command ({len(report.impacted_tests)} test targets)",
                    border_style="green",
                )
            )
        else:
            console.print("[yellow]No impacted test targets detected in graph.[/yellow]")
    finally:
        storage.close()


@app.command()
def ask(
    question: str = typer.Argument(..., help="Architectural or impact question about the codebase"),
    symbol: str | None = typer.Option(None, "--symbol", "-s", help="Optional anchor symbol to center the subgraph"),
    model: str = typer.Option("gemini-4-hydrogen", "--model", "-m", help="Gemini model name"),
    repo_path: Path = typer.Option(Path("."), "--repo", "-r", help="Path to indexed repository"),
) -> None:
    """Ask a natural-language question answered strictly from a verified dependency subgraph via Gemini."""
    repo_root = repo_path.resolve()
    db_path = repo_root / ".graphtrace" / "index.db"
    if not db_path.is_file():
        console.print("[red]Error:[/red] No index found. Run `graphtrace index` first.")
        raise typer.Exit(code=1)

    storage = GraphStorage(db_path)
    try:
        rag = SubgraphRAG(repo_root, storage)
        response = rag.ask(question=question, explicit_symbol=symbol, model=model)
        console.print(
            Panel(
                Markdown(response.answer),
                title=f"GraphTrace Subgraph Synthesis ({response.model} | {len(response.cited_slices)} slices)",
                border_style="blue",
            )
        )
    except RuntimeError as exc:
        console.print(f"[red]Configuration Error:[/red] {exc}")
        raise typer.Exit(code=1)
    finally:
        storage.close()


@app.command()
def mcp(
    repo_path: Path = typer.Option(Path("."), "--repo", "-r", help="Path to indexed repository"),
) -> None:
    """Start the GraphTrace stdio Model Context Protocol (MCP) server for AI coding agents."""
    server = GraphTraceMCPServer(repo_path)
    server.serve_stdio()


if __name__ == "__main__":
    app()