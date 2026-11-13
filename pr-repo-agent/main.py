import json
from pathlib import Path
import typer
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.markdown import Markdown

from core.repo_indexer import build_initial_map, sync_repo_map
from core.github_client import fetch_github_item
from core.graph import build_triage_graph

app = typer.Typer(help="Kensho (v1) — Autonomous PR Triage & Bug Reproduction Agent")
console = Console()

WORKSPACE_DIR = Path("workspace")
DEFAULT_REPO_DIR = WORKSPACE_DIR / "pipelines"
DEFAULT_MAP_PATH = WORKSPACE_DIR / "repo_map.json"
LAST_RUN_PATH = WORKSPACE_DIR / "last_run.json"


@app.command()
def index(
    repo: str = typer.Option("kubeflow/pipelines", help="GitHub owner/repo"),
    repo_dir: Path = typer.Option(DEFAULT_REPO_DIR, help="Local clone path"),
    map_path: Path = typer.Option(DEFAULT_MAP_PATH, help="Output repo_map.json path"),
):
    """Builds the initial Repo Architecture Map from docs and folder layout."""
    with console.status(f"[bold cyan]Indexing {repo} architecture...[/bold cyan]"):
        repo_map = build_initial_map(repo, repo_dir, map_path)
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
    """Incrementally updates repo_map.json using git diffs since the last indexed commit."""
    with console.status("[bold cyan]Syncing repo map with latest merged PRs...[/bold cyan]"):
        updated = sync_repo_map(repo_dir, map_path)
    console.print(f"[bold green]Synced![/bold green] Latest SHA: [yellow]{updated.last_indexed_sha[:7]}[/yellow]")


def _execute_agent(repo: str, number: int, item_type: str, repo_dir: Path, map_path: Path, max_retries: int):
    if not map_path.exists():
        console.print("[bold red]repo_map.json not found.[/bold red] Run `python main.py index` first.")
        raise typer.Exit(code=1)

    repo_map_json = map_path.read_text(encoding="utf-8")

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

    with console.status("[bold cyan]Running Triage -> Explorer -> Docker Sandbox -> Judge loop...[/bold cyan]"):
        final_state = graph.invoke(initial_state)

    # Save trace for Streamlit viewer
    LAST_RUN_PATH.write_text(json.dumps(final_state, indent=2), encoding="utf-8")

    verdict = final_state.get("verdict", {})
    status = verdict.get("status", "UNKNOWN")
    color = "green" if status == "VERIFIED" else ("yellow" if status == "NOT_REPRODUCED" else "red")

    console.print(Panel(
        f"[bold {color}]STATUS: {status}[/bold {color}]  |  "
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
):
    """Triages and reproduces a GitHub Issue inside an isolated Docker sandbox."""
    _execute_agent(repo, number, "issue", repo_dir, map_path, max_retries)


@app.command()
def pr(
    number: int = typer.Argument(..., help="GitHub Pull Request number"),
    repo: str = typer.Option("kubeflow/pipelines", help="GitHub owner/repo"),
    repo_dir: Path = typer.Option(DEFAULT_REPO_DIR),
    map_path: Path = typer.Option(DEFAULT_MAP_PATH),
    max_retries: int = typer.Option(2, help="Max self-correction retries"),
):
    """Triages a GitHub Pull Request diff and runs sandbox verification."""
    _execute_agent(repo, number, "pr", repo_dir, map_path, max_retries)


if __name__ == "__main__":
    app()