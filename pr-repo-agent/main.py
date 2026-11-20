import json
import os
import subprocess
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from core import __version__
from core.errors import friendly_error
from core.git_context import detect_repo_context
from core.github_client import fetch_github_item
from core.graph import build_triage_graph
from core.repo_indexer import build_initial_map, sync_repo_map
from core.stack_detector import StackSpec, detect_stack, ensure_sandbox_image
from core.sandbox import PolyglotSandbox
from memory.profiler import build_contributor_profile, load_profile
from memory.updater import sync_profile_from_merged_prs
from memory.recommender import recommend_issues

app = typer.Typer(
    help="Kensho (v2) — Fork-Aware Autonomous PR Triage & Sandboxed Bug Reproduction CLI",
    context_settings={"help_option_names": ["-h", "--help"]},
)
console = Console()

def quiet_status(message: str):
    """console.status() without stdout/stderr redirection.

    Rich's redirect swaps sys.stdout for a FileProxy; if any library (logging handler,
    background thread) keeps a reference, Python prints
    'Exception ignored ... FileProxy ... sys.meta_path is None' on exit.
    """
    from rich.status import Status
    status = Status(message, console=console)
    status._live._redirect_stdout = False
    status._live._redirect_stderr = False
    return status

# Resolve paths from this file so the command works from any directory
PROJECT_ROOT = Path(__file__).resolve().parent
WORKSPACE_DIR = PROJECT_ROOT / "workspace"
FALLBACK_REPO = "Snehadas2005/Kensho"
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


def _resolve_target(
    repo: str | None,
    repo_dir: Path | None,
    map_path: Path | None,
) -> tuple[str, Path, Path, Path | None, Path]:
    """
    Auto-detects the upstream repo, repo root, map, stack_spec, and last_run path
    from the current git repository. Falls back to Snehadas2005/Kensho with a clear
    error message if no upstream/forked repo can be detected.
    """
    try:
        ctx = detect_repo_context()
        return (
            repo or ctx.upstream_repo,
            repo_dir or ctx.repo_root,
            map_path or ctx.map_path,
            ctx.stack_path if ctx.stack_path.exists() else None,
            ctx.last_run_path,
        )
    except Exception as exc:  # noqa: BLE001
        console.print(
            Panel(
                f"[bold red]Could not detect an upstream forked repository from {Path.cwd()}.[/bold red]\n"
                f"Reason: {exc}\n\n"
                f"Falling back to [cyan]{FALLBACK_REPO}[/cyan].\n"
                "To target your fork/upstream repo, `cd` into your cloned repository and run "
                "[cyan]git kensho init[/cyan] first, or contact the maintainer at "
                f"[underline]https://github.com/{FALLBACK_REPO}[/underline].",
                title="Upstream Repo Not Detected",
                border_style="red",
            )
        )
        fallback_root = repo_dir or PROJECT_ROOT.parent
        fallback_kensho = fallback_root / ".kensho"
        return (
            repo or FALLBACK_REPO,
            fallback_root,
            map_path or (fallback_kensho / "repo_map.json"),
            (fallback_kensho / "stack_spec.json") if (fallback_kensho / "stack_spec.json").exists() else None,
            fallback_kensho / "last_run.json",
        )


@app.command()
def config(
    gemini_key: str = typer.Option(..., prompt="Enter your GEMINI_API_KEY", hide_input=True),
    zen_key: str = typer.Option("", prompt="Enter your OpenCode Zen API Key (optional, press Enter to skip)", hide_input=True),
    github_token: str = typer.Option("", prompt="Enter your GitHub Token (optional, press Enter to skip)", hide_input=True),
):
    """Saves your API keys globally to ~/.kensho/.env."""
    kensho_home = Path.home() / ".kensho"
    kensho_home.mkdir(parents=True, exist_ok=True)
    env_path = kensho_home / ".env"

    lines = [
        f"GEMINI_API_KEY={gemini_key.strip()}",
        f"OPENCODE_ZEN_API_KEY={zen_key.strip()}",
        f"GITHUB_TOKEN={github_token.strip()}",
    ]
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    console.print(f"[bold green]✓ Configuration saved successfully to {env_path}[/bold green]")


@app.command()
def init(
    rebuild_image: bool = typer.Option(False, "--rebuild-image", help="Force rebuild of cached repo Docker image"),
):
    """Detects fork/upstream remotes, indexes architecture, and provisions cached Docker sandbox."""
    _ensure_api_keys()
    ctx = detect_repo_context()
    fork_badge = (
        f"fork [cyan]{ctx.origin_repo}[/cyan] -> upstream [green]{ctx.upstream_repo}[/green]"
        if ctx.is_fork
        else f"[green]{ctx.upstream_repo}[/green]"
    )
    console.print(
        Panel(
            f"[bold]Repository:[/bold] {fork_badge}\n"
            f"[bold]Contributor:[/bold] @{ctx.github_username}\n"
            f"[bold]State Dir:[/bold] {ctx.kensho_dir}",
            title="Kensho v2 Context Detected",
        )
    )

    with quiet_status(f"[bold cyan]Indexing {ctx.upstream_repo} architecture...[/bold cyan]"):
        repo_map = build_initial_map(ctx.upstream_repo, ctx.repo_root, ctx.map_path)

    with quiet_status("[bold cyan]Detecting tech stack & provisioning cached Docker sandbox...[/bold cyan]"):
        spec = detect_stack(ctx.repo_root, ctx.upstream_repo)
        ctx.stack_path.write_text(spec.model_dump_json(indent=2), encoding="utf-8")
        (ctx.kensho_dir / "Dockerfile.sandbox").write_text(spec.dockerfile_content, encoding="utf-8")
        image_tag = ensure_sandbox_image(ctx.repo_root, spec, force_rebuild=rebuild_image)

    if "go" in spec.detected_languages:
        with quiet_status("[bold cyan]Pre-compiling Go dependencies (one-time, cached; later runs take seconds)...[/bold cyan]"):
            go_warm_error = PolyglotSandbox(ctx.repo_root, stack_spec=spec).warm_go_cache(image_tag)
        if go_warm_error:
            console.print(Panel(go_warm_error, title="Go pre-compile did not finish (run `git kensho init` again to resume)", border_style="yellow"))

    console.print(
        Panel(
            f"[bold green]Indexed {len(repo_map.modules)} modules[/bold green] (SHA [yellow]{repo_map.last_indexed_sha[:7]}[/yellow])\n"
            f"[bold]Detected Stack:[/bold] {', '.join(spec.detected_languages)}\n"
            f"[bold]Cached Sandbox Image:[/bold] [cyan]{image_tag}[/cyan]",
            title="Repository Ready",
        )
    )


@app.command()
def index(
    repo: str | None = typer.Option(None, help="GitHub owner/repo (auto-detected if omitted)"),
    repo_dir: Path | None = typer.Option(None, help="Local clone path"),
    map_path: Path | None = typer.Option(None, help="Output repo_map.json path"),
):
    """Builds the initial Repo Architecture Map from docs and folder layout."""
    repo, repo_dir, map_path, _, _ = _resolve_target(repo, repo_dir, map_path)
    try:
        with quiet_status(f"[bold cyan]Indexing {repo} architecture...[/bold cyan]"):
            repo_map = build_initial_map(repo, repo_dir, map_path)
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
    console.print(
        Panel(
            f"[bold green]Indexed {len(repo_map.modules)} modules[/bold green] at SHA [yellow]{repo_map.last_indexed_sha[:7]}[/yellow]\n"
            f"Saved to: {map_path}",
            title="Repo Architecture Map Ready",
        )
    )


@app.command()
def sync(
    repo_dir: Path | None = typer.Option(None, help="Local clone path"),
    map_path: Path | None = typer.Option(None, help="Existing repo_map.json path"),
):
    """Pulls the latest commits and incrementally updates repo_map.json from the git diff."""
    _, repo_dir, map_path, _, _ = _resolve_target(None, repo_dir, map_path)
    try:
        with quiet_status("[bold cyan]Syncing repo map with latest merged PRs...[/bold cyan]"):
            updated = sync_repo_map(repo_dir, map_path)
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
    console.print(f"[bold green]Synced![/bold green] Latest SHA: [yellow]{updated.last_indexed_sha[:7]}[/yellow]")


def _require_map(map_path: Path) -> None:
    if not map_path.exists():
        console.print(
            f"[bold red]repo_map.json not found at {map_path}.[/bold red] "
            "Run [cyan]git kensho init[/cyan] inside your cloned repository first."
        )
        raise typer.Exit(code=1)


def _launch_ui(
    repo: str,
    number: int,
    item_type: str,
    repo_dir: Path,
    map_path: Path,
    max_retries: int,
    stack_path: Path | None,
    last_run_path: Path,
):
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
            "stack_path": str(stack_path) if stack_path else "",
            "last_run_path": str(last_run_path),
        }),
        encoding="utf-8",
    )
    console.print(f"[bold cyan]Opening Kensho UI for {repo} {item_type} #{number}...[/bold cyan] (Ctrl+C to stop)")
    try:
        subprocess.run(
            [sys.executable, "-m", "streamlit", "run", str(PROJECT_ROOT / "app.py")],
            cwd=PROJECT_ROOT,
            check=False,
        )
    except KeyboardInterrupt:
        console.print("\n[dim]UI stopped.[/dim]")


def _ensure_api_keys() -> None:
    """Pre-flight check: prompts or errors if GEMINI_API_KEY is missing."""
    if not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or os.getenv("OPENCODE_ZEN_API_KEY")):
        console.print("[bold red]❌ No API keys detected![/bold red]")
        console.print("Please run [cyan]git kensho config[/cyan] first to set up your keys.")
        raise typer.Exit(code=1)


def _run(
    repo: str | None,
    number: int,
    item_type: str,
    repo_dir: Path | None,
    map_path: Path | None,
    max_retries: int,
    cli: bool,
):
    resolved_repo, resolved_dir, resolved_map, stack_path, last_run_path = _resolve_target(repo, repo_dir, map_path)
    if cli:
        _execute_agent(resolved_repo, number, item_type, resolved_dir, resolved_map, max_retries, stack_path, last_run_path)
    else:
        _launch_ui(resolved_repo, number, item_type, resolved_dir, resolved_map, max_retries, stack_path, last_run_path)


def _execute_agent(
    repo: str,
    number: int,
    item_type: str,
    repo_dir: Path,
    map_path: Path,
    max_retries: int,
    stack_path: Path | None,
    last_run_path: Path,
):
    _require_map(map_path)
    repo_map_json = map_path.read_text(encoding="utf-8")
    stack_spec = (
        StackSpec.model_validate_json(stack_path.read_text(encoding="utf-8"))
        if stack_path and stack_path.exists()
        else None
    )

    try:
        with quiet_status(f"[bold cyan]Fetching {item_type.upper()} #{number} from {repo}...[/bold cyan]"):
            work_item = fetch_github_item(repo, number, item_type=item_type)

        console.print(Panel(f"[bold]{work_item.title}[/bold]", title=f"{repo} #{number}"))

        graph = build_triage_graph(repo_dir, stack_spec=stack_spec)
        initial_state = {
            "repo_name": repo,
            "repo_root": str(repo_dir),
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
        with quiet_status("[bold cyan]Starting agent loop...[/bold cyan]") as status:
            for update in graph.stream(initial_state, stream_mode="updates"):
                for node, delta in update.items():
                    final_state.update(delta or {})
                    console.print(f"[green]✓[/green] {NODE_LABELS.get(node, node)}")
                    status.update(f"[bold cyan]{NODE_LABELS.get(node, node)} done, continuing...[/bold cyan]")
    except Exception as exc:  # noqa: BLE001
        _fail(exc)

    last_run_path.parent.mkdir(parents=True, exist_ok=True)
    last_run_path.write_text(json.dumps(final_state, indent=2, default=str), encoding="utf-8")

    verdict = final_state.get("verdict", {})
    status_text = verdict.get("status", "UNKNOWN")
    color = "green" if status_text == "VERIFIED" else ("yellow" if status_text == "NOT_REPRODUCED" else "red")

    console.print(
        Panel(
            f"[bold {color}]STATUS: {status_text}[/bold {color}]  |  "
            f"Runtime: [cyan]{final_state.get('target_language')}[/cyan]  |  "
            f"Attempts: {final_state.get('retry_count')}\n\n"
            f"[bold]Suspect Files:[/bold] {', '.join(final_state.get('suspect_files', []))}\n"
            f"[bold]Root Cause:[/bold] {verdict.get('root_cause_summary', '')}\n"
            f"[bold]Fix Direction:[/bold] {verdict.get('suggested_fix_direction', '')}",
            title="Agent Verdict",
        )
    )

    console.print("\n[bold cyan]Generated Reproduction Test:[/bold cyan]")
    console.print(
        Syntax(
            final_state.get("test_code", ""),
            final_state.get("target_language", "python"),
            theme="monokai",
            line_numbers=True,
        )
    )

    console.print("\n[bold cyan]Ready-to-Post GitHub Markdown Report:[/bold cyan]")
    console.print(Panel(Markdown(verdict.get("github_markdown_report", "")), title="GitHub Comment Draft"))


@app.command()
def onboard():
    """Runs a 3-question interactive CLI onboarding interview to build your contributor profile."""
    ctx = detect_repo_context()
    profile = build_contributor_profile(ctx, console)
    console.print(
        Panel(
            f"[bold green]Profile saved to {ctx.kensho_dir / 'profile.json'}[/bold green]\n"
            f"[bold]Focus Areas:[/bold] {', '.join(profile.focus_areas)}\n"
            f"[bold]Preferred Subdirs:[/bold] {', '.join(profile.favorite_subdirs)}\n"
            f"[italic]{profile.summary_bio}[/italic]",
            title=f"Contributor Profile: @{profile.github_username}",
        )
    )


@app.command()
def profile(
    as_json: bool = typer.Option(False, "--json", help="Print the raw profile.json"),
    sync: bool = typer.Option(False, "--sync", help="Learn from your newly merged upstream PRs (files, review comments, commits)"),
    reset: bool = typer.Option(False, "--reset", help="Delete the saved profile (run `kensho onboard` to recreate it)"),
):
    """Shows (or syncs / resets) your saved contributor profile (.kensho/profile.json)."""
    ctx = detect_repo_context()
    profile_path = ctx.kensho_dir / "profile.json"

    if reset:
        if not profile_path.exists():
            console.print("[yellow]No profile to reset.[/yellow]")
            return
        if typer.confirm(f"Delete {profile_path}?", default=False):
            profile_path.unlink()
            console.print("[green]Profile deleted. Run `git kensho onboard` to create a new one.[/green]")
        return

    if load_profile(ctx.kensho_dir) is None:
        console.print("[bold red]No profile found. Run `git kensho onboard` first.[/bold red]")
        raise typer.Exit(code=1)

    if sync:
        try:
            with quiet_status("[bold cyan]Reading your newly merged PRs...[/bold cyan]"):
                _, new_prs = sync_profile_from_merged_prs(ctx)
        except Exception as exc:  # noqa: BLE001
            _fail(exc)
        console.print(f"[green]Profile updated from {new_prs} new merged PR(s).[/green]" if new_prs else "[dim]No new merged PRs since the last sync.[/dim]")

    current = load_profile(ctx.kensho_dir)
    if as_json:
        console.print_json(current.model_dump_json())
        return

    table = Table(title=f"Contributor Profile: @{current.github_username}", show_header=False)
    table.add_column("Field", style="bold cyan")
    table.add_column("Value")
    table.add_row("Experience", current.experience_level)
    table.add_row("Languages", ", ".join(current.preferred_languages) or "-")
    table.add_row("Focus areas", ", ".join(current.focus_areas) or "-")
    table.add_row("Favorite subdirs", ", ".join(current.favorite_subdirs) or "-")
    table.add_row("Focus modules (learned)", ", ".join(current.focus_modules) or "-")
    table.add_row("Coding style", "; ".join(current.coding_style) or "-")
    table.add_row("Review preferences", "; ".join(current.review_preferences) or "-")
    table.add_row("Last merged PR synced", f"#{current.last_synced_pr}" if current.last_synced_pr else "-")
    table.add_row("Updated", current.updated_at or "-")
    table.add_row("Bio", current.summary_bio)
    console.print(table)


@app.command()
def recommend(
    top: int = typer.Option(5, "--top", "-t", help="Number of issue recommendations to display"),
):
    """Ranks open upstream issues matched to your contributor profile and repo map."""
    ctx = detect_repo_context()
    with quiet_status("[bold cyan]Analyzing open issues & matching against your profile...[/bold cyan]"):
        recs = recommend_issues(ctx, console, top_n=top)

    if not recs:
        return

    table = Table(title=f"Top Issue Recommendations for {ctx.upstream_repo}", show_header=True, header_style="bold magenta")
    table.add_column("Fit", justify="center", style="cyan")
    table.add_column("#", justify="right", style="yellow")
    table.add_column("Issue Title", style="white")
    table.add_column("Module", style="green")
    table.add_column("Reasoning", style="dim")

    for r in sorted(recs, key=lambda x: x.fit_score, reverse=True):
        table.add_row(f"{r.fit_score}%", str(r.issue_number), r.title[:55], r.matched_module, r.reasoning[:70])

    console.print(table)
    console.print("\n[dim]To reproduce any recommended issue, run:[/dim] [cyan]git kensho issue <number>[/cyan]")


@app.command()
def issue(
    number: int = typer.Argument(..., help="GitHub Issue number"),
    repo: str | None = typer.Option(None, help="GitHub owner/repo (auto-detected from git remote)"),
    repo_dir: Path | None = typer.Option(None),
    map_path: Path | None = typer.Option(None),
    max_retries: int = typer.Option(2, help="Max self-correction retries on test syntax/import errors"),
    cli: bool = typer.Option(False, "--cli", help="Terminal-only output (default opens the Streamlit UI)"),
):
    """Triages and reproduces a GitHub Issue. Opens the UI by default; use --cli for terminal output."""
    _run(repo, number, "issue", repo_dir, map_path, max_retries, cli)


@app.command()
def pr(
    number: int = typer.Argument(..., help="GitHub Pull Request number"),
    repo: str | None = typer.Option(None, help="GitHub owner/repo (auto-detected from git remote)"),
    repo_dir: Path | None = typer.Option(None),
    map_path: Path | None = typer.Option(None),
    max_retries: int = typer.Option(2, help="Max self-correction retries"),
    cli: bool = typer.Option(False, "--cli", help="Terminal-only output (default opens the Streamlit UI)"),
):
    """Triages a GitHub Pull Request diff. Opens the UI by default; use --cli for terminal output."""
    _run(repo, number, "pr", repo_dir, map_path, max_retries, cli)


if __name__ == "__main__":
    app()