import json
import os
from pathlib import Path
from github import Github
from pydantic import BaseModel
from core.llm import call_structured_llm
from core.schemas import ContributorProfile  # noqa: F401  (moved to core/schemas.py; re-exported here)


def load_profile(kensho_dir: Path) -> "ContributorProfile | None":
    path = kensho_dir / "profile.json"
    if not path.is_file():
        return None
    try:
        return ContributorProfile.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


class OnboardingInterviewResponse(BaseModel):
    preferred_languages: list[str]
    favorite_subdirs: list[str]
    experience_level: str
    focus_areas: list[str]
    summary_bio: str


def build_contributor_profile(ctx, console) -> ContributorProfile:
    """Analyzes GitHub history and runs an interactive CLI onboarding interview."""
    github_token = os.getenv("GITHUB_TOKEN", "").strip()
    gh_history_summary = "No GitHub token provided; relying entirely on interview."

    if github_token:
        try:
            g = Github(github_token)
            repo = g.get_repo(ctx.upstream_repo)
            # Fetch recent merged PRs by user
            prs = list(repo.get_pulls(state="closed", sort="updated", direction="desc"))
            user_prs = [pr for pr in prs if pr.user and pr.user.login.lower() == ctx.github_username.lower() and pr.merged]
            
            subdirs_touched = set()
            for pr in user_prs[:15]:
                for f in pr.get_files():
                    parts = Path(f.filename).parts
                    if len(parts) > 1:
                        subdirs_touched.add(parts[0])
            gh_history_summary = f"User has {len(user_prs)} merged PRs in upstream. Common subdirs touched: {list(subdirs_touched)[:5]}"
        except Exception:
            pass

    console.print(f"\n[bold cyan]🤖 Kensho Contributor Onboarding for @{ctx.github_username}[/bold cyan]")
    console.print(f"[dim]{gh_history_summary}[/dim]\n")

    # Interactive CLI Q&A
    q1 = console.input("[bold yellow]1. What programming languages & frameworks are you most comfortable with? (e.g., Python, Go, React): [/bold yellow]").strip() or "Python"
    q2 = console.input("[bold yellow]2. Which parts of the codebase do you prefer working in? (e.g., SDK, backend, CLI, UI): [/bold yellow]").strip() or "SDK and backend"
    q3 = console.input("[bold yellow]3. How would you rate your experience level with open-source codebases? (beginner/intermediate/advanced): [/bold yellow]").strip() or "intermediate"

    system_prompt = "You are a senior technical recruiter structuring a developer contributor profile for an open-source codebase."
    user_prompt = f"""
    GitHub Username: {ctx.github_username}
    Upstream Repo: {ctx.upstream_repo}
    GitHub History Insight: {gh_history_summary}
    Developer Answers:
    - Languages/Frameworks: {q1}
    - Preferred Subdirs/Focus: {q2}
    - Experience Level: {q3}

    Extract and summarize a clean structured contributor profile.
    """

    profile_data = call_structured_llm(system_prompt, user_prompt, OnboardingInterviewResponse)
    
    profile = ContributorProfile(
        github_username=ctx.github_username,
        preferred_languages=profile_data.preferred_languages,
        favorite_subdirs=profile_data.favorite_subdirs,
        experience_level=profile_data.experience_level,
        focus_areas=profile_data.focus_areas,
        summary_bio=profile_data.summary_bio,
    )

    profile_path = ctx.kensho_dir / "profile.json"
    profile_path.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
    return profile