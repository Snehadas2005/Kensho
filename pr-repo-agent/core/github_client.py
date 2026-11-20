import os
from dotenv import load_dotenv
from github import Github
from pydantic import BaseModel

load_dotenv()

NOISY_SUFFIXES = (
    ".pb.go",
    "_pb2.py",
    "_pb2_grpc.py",
    ".lock",
    "-lock.json",
    ".sum",
    ".min.js",
    ".svg",
    ".png",
)
NOISY_DIRS = ("vendor/", "third_party/", "manifests/kustomize/", "frontend/build/")


class GitHubWorkItem(BaseModel):
    repo_name: str
    item_type: str  # "issue" or "pr"
    number: int
    title: str
    body: str
    diff_summary: str = ""
    review_comments: str = ""
    commit_history: str = ""


def _is_noisy_file(filepath: str) -> bool:
    if filepath.endswith(NOISY_SUFFIXES):
        return True
    return any(filepath.startswith(prefix) for prefix in NOISY_DIRS)


def fetch_github_item(repo_name: str, number: int, item_type: str = "issue") -> GitHubWorkItem:
    """Fetches an Issue or PR from GitHub and compresses PR diffs by removing auto-generated noise."""
    token = os.getenv("GITHUB_TOKEN") or None
    gh = Github(token)
    repo = gh.get_repo(repo_name)

    if item_type == "issue":
        issue = repo.get_issue(number=number)
        comments_text = "\n".join(
            f"- @{c.user.login if c.user else 'ghost'}: {(c.body or '')[:600]}"
            for c in list(issue.get_comments())[:5]
        )
        full_body = f"{issue.body or ''}\n\nRecent Comments:\n{comments_text}".strip()
        return GitHubWorkItem(
            repo_name=repo_name,
            item_type="issue",
            number=number,
            title=issue.title,
            body=full_body[:8000],
        )

    elif item_type == "pr":
        pr = repo.get_pull(number=number)
        diff_chunks = []
        skipped_count = 0

        for f in pr.get_files():
            if _is_noisy_file(f.filename):
                skipped_count += 1
                continue
            patch = (f.patch or "")[:2500]
            diff_chunks.append(f"### File: {f.filename} ({f.status}, +{f.additions}/-{f.deletions})\n```diff\n{patch}\n```")

        compressed_diff = "\n\n".join(diff_chunks[:12])
        if skipped_count:
            compressed_diff += f"\n\n*(Skipped {skipped_count} auto-generated/vendor files)*"

        try:
            review_text = fetch_review_comments(repo_name, number)
        except Exception:  # noqa: BLE001
            review_text = ""
        try:
            commit_text = fetch_commit_history(repo_name, pr_number=number)
        except Exception:  # noqa: BLE001
            commit_text = ""

        # Folded into diff_summary so every existing caller (CLI + Streamlit) passes it to the LLM.
        enriched = compressed_diff[:10000]
        if review_text:
            enriched += f"\n\n### REVIEW COMMENTS\n{review_text[:2500]}"
        if commit_text:
            enriched += f"\n\n### COMMIT HISTORY\n{commit_text[:1200]}"

        return GitHubWorkItem(
            repo_name=repo_name,
            item_type="pr",
            number=number,
            title=pr.title,
            body=(pr.body or "")[:5000],
            diff_summary=enriched[:14000],
            review_comments=review_text,
            commit_history=commit_text,
        )

    else:
        raise ValueError(f"Unsupported item_type: {item_type}")


# ----------------------------------------------------------------------------------------
# v2 add-ons: review comments, commit history, merged-PR activity
# ----------------------------------------------------------------------------------------
def fetch_review_comments(repo_name: str, pr_number: int, limit: int = 12) -> str:
    """Inline review comments + top-level review verdicts of a PR, as compact text."""
    gh = Github(os.getenv("GITHUB_TOKEN") or None)
    pr = gh.get_repo(repo_name).get_pull(number=pr_number)
    lines: list[str] = []
    for c in list(pr.get_review_comments())[:limit]:
        who = c.user.login if c.user else "ghost"
        lines.append(f"- @{who} on {c.path}:{c.line or c.original_line or '?'}: {(c.body or '')[:300]}")
    for r in list(pr.get_reviews())[:6]:
        if r.body:
            who = r.user.login if r.user else "ghost"
            lines.append(f"- @{who} ({r.state}): {r.body[:300]}")
    return "\n".join(lines)


def fetch_commit_history(repo_name: str, pr_number: int | None = None, path: str = "", limit: int = 8) -> str:
    """Commit messages of a PR, or recent commits touching `path` on the default branch."""
    gh = Github(os.getenv("GITHUB_TOKEN") or None)
    repo = gh.get_repo(repo_name)
    if pr_number is not None:
        commits = list(repo.get_pull(number=pr_number).get_commits())[:limit]
    else:
        commits = list(repo.get_commits(path=path))[:limit] if path else list(repo.get_commits())[:limit]
    out = []
    for c in commits:
        msg = (c.commit.message or "").splitlines()[0][:120]
        author = c.author.login if c.author else (c.commit.author.name if c.commit.author else "unknown")
        out.append(f"- {c.sha[:7]} {author}: {msg}")
    return "\n".join(out)


def fetch_merged_pr_activity(repo_name: str, username: str, limit: int = 10, since_pr: int = 0) -> list[dict]:
    """Merged PRs authored by `username` (newest first, number > since_pr) with files, review
    comments and commit messages. Used by memory/profiler.py and memory/updater.py."""
    gh = Github(os.getenv("GITHUB_TOKEN") or None)
    repo = gh.get_repo(repo_name)
    activity: list[dict] = []
    for pr in repo.get_pulls(state="closed", sort="updated", direction="desc"):
        if len(activity) >= limit:
            break
        if not pr.merged or not pr.user or pr.user.login.lower() != username.lower() or pr.number <= since_pr:
            continue
        files = [f.filename for f in pr.get_files()][:40]
        activity.append({
            "number": pr.number,
            "title": pr.title,
            "files": files,
            "review_comments": [(c.body or "")[:300] for c in list(pr.get_review_comments())[:10]],
            "commit_messages": [(c.commit.message or "").splitlines()[0][:120] for c in list(pr.get_commits())[:10]],
        })
    return activity
