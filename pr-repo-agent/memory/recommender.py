import os
import json
import re
from github import Github
from pydantic import BaseModel, Field
from core.llm import call_structured_llm


class IssueRecommendation(BaseModel):
    issue_number: int
    title: str
    fit_score: int          # 0 to 100
    matched_module: str
    reasoning: str


class RecommendationList(BaseModel):
    recommendations: list[IssueRecommendation] = Field(..., description="List of top recommended issues, aiming for up to top_n distinct options.")


def recommend_issues(ctx, console, top_n: int = 5) -> list[IssueRecommendation]:
    """Scores open upstream issues against the contributor profile, filtering out issues with open PRs."""
    profile_path = ctx.kensho_dir / "profile.json"
    if not profile_path.exists():
        console.print("[bold red]❌ Profile not found. Run `git kensho onboard` first![/bold red]")
        return []

    profile_data = profile_path.read_text(encoding="utf-8")
    map_data = ctx.map_path.read_text(encoding="utf-8") if ctx.map_path.exists() else "{}"

    github_token = os.getenv("GITHUB_TOKEN", "").strip()
    if not github_token:
        console.print("[bold red]❌ GITHUB_TOKEN required to fetch open issues. Add it to ~/.kensho/.env[/bold red]")
        return []

    g = Github(github_token)
    repo = g.get_repo(ctx.upstream_repo)
    
    # 1. Gather issue numbers that already have open PRs referencing them
    issues_with_open_prs = set()
    try:
        open_prs = repo.get_pulls(state="open")
        for pr in open_prs:
            text = f"{pr.title} {pr.body or ''}"
            found_nums = re.findall(r'#(\d+)', text)
            for num_str in found_nums:
                issues_with_open_prs.add(int(num_str))
    except Exception:
        pass

    # 2. Fetch a larger pool of open issues and filter out those with open PRs
    open_issues = []
    for issue in repo.get_issues(state="open", sort="updated", direction="desc")[:50]:
        if issue.pull_request:
            continue
        if issue.number in issues_with_open_prs:
            continue
        open_issues.append({
            "number": issue.number,
            "title": issue.title,
            "labels": [l.name for l in issue.labels],
            "body": (issue.body or "")[:300],
        })

    if not open_issues:
        console.print("[yellow]No eligible open issues found (without open PRs).[/yellow]")
        return []

    system_prompt = "You are an expert open-source maintainer matching developers to high-fit issues. You must return multiple distinct recommendations (up to top_n) if available."
    user_prompt = f"""
    Contributor Profile: {profile_data}
    Repository Architecture Map: {map_data}
    Target Number of Recommendations: {top_n}
    Eligible Open Issues (No Open PRs): {json.dumps(open_issues, indent=2)}

    Select and rank the top {top_n} issues that best match this contributor's skills, preferred subdirs, and experience level. Provide fit scores (0-100) and clear reasoning for each. Do not return only 1 issue if multiple good matches exist.
    """

    result = call_structured_llm(system_prompt, user_prompt, RecommendationList)
    return result.recommendations