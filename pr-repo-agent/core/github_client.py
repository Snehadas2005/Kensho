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
            f"- @{c.user.login}: {c.body[:600]}" for c in list(issue.get_comments())[:5]
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

        return GitHubWorkItem(
            repo_name=repo_name,
            item_type="pr",
            number=number,
            title=pr.title,
            body=(pr.body or "")[:5000],
            diff_summary=compressed_diff[:12000],
        )

    else:
        raise ValueError(f"Unsupported item_type: {item_type}")