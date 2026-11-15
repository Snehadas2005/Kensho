import json
import subprocess
import sys
from pathlib import Path
import typer
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.markdown import Markdown

from core import __version__
from core.errors import friendly_error
from core.repo_indexer import build_initial_map, sync_repo_map
from core.github_client import fetch_github_item
from core.git_context import detect_repo_context
from core.stack_detector import StackSpec, detect_stack, ensure_sandbox_image
from core.graph import build_triage_graph


app = typer.Typer(help=f"Kensho (v{__version__}) — Autonomous PR Triage & Bug Reproduction Agent")
console = Console()

# Resolve paths from this file so the command works from any directory
PROJECT_ROOT = Path(__file__).resolve().parent
WORKSPACE_DIR = PROJECT_ROOT / "workspace"
DEFAULT_REPO_DIR = WORKSPACE_DIR / "pipelines"
DEFAULT_MAP_PATH = WORKSPACE_DIR / "repo_map.json"
LAST_RUN_PATH = WORKSPACE_DIR / "last_run.json"
PENDING_RUN_PATH = WORKSPACE_DIR / "pending_run.json"

NODE_LABELS = {
    "triage": "Triage & localization",
    "explorer_coder": "Writing reproduction test",
    "sandbox": "Running in Docker sandbox",
    "judge": "Judging result",
}


def _fail(exc: Exception) -> None:
    console.print(Panel(friendly_error(exc), title="Error", border_style="red"))
    raise typer.Exit(code=1)

@app.command()
def init(
    rebuild_image: bool = typer.Option(False, "--rebuild-image", help="Force rebuild of the cached repo Docker image"),
):
    """Detects fork/upstream remotes, indexes repo architecture, and provisions a cached Docker sandbox."""
    ctx = detect_repo_context()
    fork_badge = f"fork [cyan]{ctx.origin_repo}[/cyan] -> upstream [green]{ctx.upstream_repo}[/green]" if ctx.is_fork else f"[green]{ctx.upstream_repo}[/green]"
    console.print(Panel(
        f"[bold]Repository:[/bold] {fork_badge}\n"
        f"[bold]Contributor:[/bold] @{ctx.github_username}\n"
        f"[bold]State Dir:[/bold] {ctx.kensho_dir}",
        title="Kensho v2 Context Detected",
    ))

    with console.status(f"[bold cyan]Indexing {ctx.upstream_repo} architecture...[/bold cyan]"):
        repo_map = build_initial_map(ctx.upstream_repo, ctx.repo_root, ctx.map_path)

    with console.status("[bold cyan]Detecting tech stack & provisioning cached Docker sandbox...[/bold cyan]"):
        spec = detect_stack(ctx.repo_root, ctx.upstream_repo)
        ctx.stack_path.write_text(spec.model_dump_json(indent=2), encoding="utf-8")
        (ctx.kensho_dir / "Dockerfile.sandbox").write_text(spec.dockerfile_content, encoding="utf-8")
        image_tag = ensure_sandbox_image(ctx.repo_root, spec, force_rebuild=rebuild_image)

    console.print(Panel(
        f"[bold green]Indexed {len(repo_map.modules)} modules[/bold green] (SHA [yellow]{repo_map.last_indexed_sha[:7]}[/yellow])\n"
        f"[bold]Detected Stack:[/bold] {', '.join(spec.detected_languages)}\n"
        f"[bold]Cached Sandbox Image:[/bold] [cyan]{image_tag}[/cyan]",
        title="Repository Ready",
    ))

@app.command()
def index(
    repo: str = typer.Option("kubeflow/pipelines", help="GitHub owner/repo"),
    repo_dir: Path = typer.Option(DEFAULT_REPO_DIR, help="Local clone path"),
    map_path: Path = typer.Option(DEFAULT_MAP_PATH, help="Output repo_map.json path"),
):
    """Builds the initial Repo Architecture Map from docs and folder layout."""
    try:
        with console.status(f"[bold cyan]Indexing {repo} architecture...[/bold cyan]"):
            repo_map = build_initial_map(repo, repo_dir, map_path)
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
    console.print(Panel(
        f"[bold green]Indexed {len(repo_map.modules)} modules[/bold green] at SHA [yellow]{repo_map.last_indexed_sha[:7]}[/yellow]\n"
        f"Saved to: {map_path}",
        title="Repo Architecture Map Ready",
    ))


@app.command()
def sync(
    repo_dir: Path = typer.Option(DEFAULT_REPO_DIR, help="Local clone path"),
    map_path: Path = typer.Option(DEFAULT_MAP_PATH, help="Existing repo_map.json path"),
):
    """Pulls the latest commits and incrementally updates repo_map.json from the git diff."""
    try:
        with console.status("[bold cyan]Syncing repo map with latest merged PRs...[/bold cyan]"):
            updated = sync_repo_map(repo_dir, map_path)
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
    console.print(f"[bold green]Synced![/bold green] Latest SHA: [yellow]{updated.last_indexed_sha[:7]}[/yellow]")


def _require_map(map_path: Path) -> None:
    if not map_path.exists():
        console.print("[bold red]repo_map.json not found.[/bold red] Run `kensho index` first.")
        raise typer.Exit(code=1)


def _launch_ui(repo: str, number: int, item_type: str, repo_dir: Path, map_path: Path, max_retries: int):
    """Hands the request to the Streamlit app, which opens in the browser and runs it automatically."""
    _require_map(map_path)
    PENDING_RUN_PATH.parent.mkdir(parents=True, exist_ok=True)
    PENDING_RUN_PATH.write_text(
        json.dumps({
            "repo_name": repo,
            "item_type": item_type,
            "item_number": number,
            "max_retries": max_retries,
            "repo_dir": str(repo_dir),
            "map_path": str(map_path),
        }),
        encoding="utf-8",
    )
    console.print(f"[bold cyan]Opening Kensho UI for {item_type} #{number}...[/bold cyan] (Ctrl+C to stop)")
    try:
        subprocess.run(
            [sys.executable, "-m", "streamlit", "run", str(PROJECT_ROOT / "app.py")],
            cwd=PROJECT_ROOT,
            check=False,
        )
    except KeyboardInterrupt:
        console.print("\n[dim]UI stopped.[/dim]")


def _run(repo: str, number: int, item_type: str, repo_dir: Path, map_path: Path, max_retries: int, cli: bool):
    if cli:
        _execute_agent(repo, number, item_type, repo_dir, map_path, max_retries)
    else:
        _launch_ui(repo, number, item_type, repo_dir, map_path, max_retries)


def _execute_agent(repo: str, number: int, item_type: str, repo_dir: Path, map_path: Path, max_retries: int):
    _require_map(map_path)

    repo_map_json = map_path.read_text(encoding="utf-8")

    try:
        with console.status(f"[bold cyan]Fetching {item_type.upper()} #{number} from {repo}...[/bold cyan]"):
            work_item = fetch_github_item(repo, number, item_type=item_type)

        console.print(Panel(f"[bold]{work_item.title}[/bold]", title=f"{repo} #{number}"))

        graph = build_triage_graph(repo_dir)
        initial_state = {
            "repo_name": repo,
            "item_type": item_type,
            "item_number": number,
            "title": work_item.title,
            "body": work_item.body,
            "diff_summary": work_item.diff_summary,
            "repo_map_json": repo_map_json,
            "retry_count": 0,
            "max_retries": max_retries,
        }

        final_state = dict(initial_state)
        with console.status("[bold cyan]Starting agent loop...[/bold cyan]") as status:
            for update in graph.stream(initial_state, stream_mode="updates"):
                for node, delta in update.items():
                    final_state.update(delta or {})
                    console.print(f"[green]✓[/green] {NODE_LABELS.get(node, node)}")
                    status.update(f"[bold cyan]{NODE_LABELS.get(node, node)} done, continuing...[/bold cyan]")
    except Exception as exc:  # noqa: BLE001
        _fail(exc)

    # Save trace for Streamlit viewer
    LAST_RUN_PATH.parent.mkdir(parents=True, exist_ok=True)
    LAST_RUN_PATH.write_text(json.dumps(final_state, indent=2, default=str), encoding="utf-8")

    verdict = final_state.get("verdict", {})
    status_text = verdict.get("status", "UNKNOWN")
    color = "green" if status_text == "VERIFIED" else ("yellow" if status_text == "NOT_REPRODUCED" else "red")

    console.print(Panel(
        f"[bold {color}]STATUS: {status_text}[/bold {color}]  |  "
        f"Runtime: [cyan]{final_state.get('target_language')}[/cyan]  |  "
        f"Attempts: {final_state.get('retry_count')}\n\n"
        f"[bold]Suspect Files:[/bold] {', '.join(final_state.get('suspect_files', []))}\n"
        f"[bold]Root Cause:[/bold] {verdict.get('root_cause_summary', '')}\n"
        f"[bold]Fix Direction:[/bold] {verdict.get('suggested_fix_direction', '')}",
        title="Agent Verdict",
    ))

    console.print("\n[bold cyan]Generated Reproduction Test:[/bold cyan]")
    console.print(Syntax(final_state.get("test_code", ""), final_state.get("target_language", "python"), theme="monokai", line_numbers=True))

    console.print("\n[bold cyan]Ready-to-Post GitHub Markdown Report:[/bold cyan]")
    console.print(Panel(Markdown(verdict.get("github_markdown_report", "")), title="GitHub Comment Draft"))


@app.command()
def issue(
    number: int = typer.Argument(..., help="GitHub Issue number"),
    repo: str = typer.Option("kubeflow/pipelines", help="GitHub owner/repo"),
    repo_dir: Path = typer.Option(DEFAULT_REPO_DIR),
    map_path: Path = typer.Option(DEFAULT_MAP_PATH),
    max_retries: int = typer.Option(2, help="Max self-correction retries on test syntax/import errors"),
    cli: bool = typer.Option(False, "--cli", help="Terminal-only output (default opens the Streamlit UI)"),
):
    """Triages and reproduces a GitHub Issue. Opens the UI by default; use --cli for terminal output."""
    _run(repo, number, "issue", repo_dir, map_path, max_retries, cli)


@app.command()
def pr(
    number: int = typer.Argument(..., help="GitHub Pull Request number"),
    repo: str = typer.Option("kubeflow/pipelines", help="GitHub owner/repo"),
    repo_dir: Path = typer.Option(DEFAULT_REPO_DIR),
    map_path: Path = typer.Option(DEFAULT_MAP_PATH),
    max_retries: int = typer.Option(2, help="Max self-correction retries"),
    cli: bool = typer.Option(False, "--cli", help="Terminal-only output (default opens the Streamlit UI)"),
):
    """Triages a GitHub Pull Request diff. Opens the UI by default; use --cli for terminal output."""
    _run(repo, number, "pr", repo_dir, map_path, max_retries, cli)


if __name__ == "__main__":
    app()
