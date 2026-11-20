"""Keeps `.kensho/profile.json` in sync with what the contributor actually does.

Two entry points:
  * update_profile_from_run(...)        - cheap, deterministic, runs after every agent run
  * sync_profile_from_merged_prs(...)   - pulls newly merged PRs (files, review comments, commits);
                                          optionally asks the LLM to distil coding style / review prefs
"""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field

from core.schemas import ContributorProfile

EXT_LANG = {".py": "Python", ".go": "Go", ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript",
            ".rs": "Rust", ".java": "Java", ".sh": "Shell", ".yaml": "YAML", ".yml": "YAML"}
LANG_NAMES = {"python": "Python", "go": "Go"}
MAX_FOCUS = 5
MAX_NOTES = 8


class StyleDelta(BaseModel):
    coding_style: list[str] = Field(default_factory=list, description="Up to 4 short observations about coding style")
    review_preferences: list[str] = Field(default_factory=list, description="Up to 4 short things reviewers repeatedly ask for")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def module_key(path: str) -> str:
    """Two leading path components ('workspaces/controller'), or one for top-level files."""
    parts = [p for p in Path(path).parts if p not in (".", "")]
    if len(parts) <= 1:
        return ""
    return "/".join(parts[:2]) if len(parts) > 2 else parts[0]


def _load(kensho_dir: Path) -> ContributorProfile | None:
    path = kensho_dir / "profile.json"
    if not path.is_file():
        return None
    try:
        return ContributorProfile.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _save(kensho_dir: Path, profile: ContributorProfile) -> None:
    profile.updated_at = _now()
    (kensho_dir / "profile.json").write_text(profile.model_dump_json(indent=2), encoding="utf-8")


def _refresh_focus(profile: ContributorProfile) -> None:
    ranked = sorted(profile.module_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    profile.focus_modules = [k for k, _ in ranked[:MAX_FOCUS]]
    langs = sorted(profile.language_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    known = {l.lower() for l in profile.preferred_languages}
    for lang, _ in langs:
        if lang.lower() not in known and len(profile.preferred_languages) < 8:
            profile.preferred_languages.append(lang)
            known.add(lang.lower())


def _merge_notes(existing: list[str], new: list[str]) -> list[str]:
    seen = {n.strip().lower() for n in existing}
    merged = list(existing)
    for note in new:
        note = note.strip()
        if note and note.lower() not in seen:
            merged.append(note)
            seen.add(note.lower())
    return merged[-MAX_NOTES:]


def update_profile_from_run(kensho_dir: Path, state: dict) -> ContributorProfile | None:
    """Deterministic update after one agent run. No-op if the user never onboarded."""
    profile = _load(kensho_dir)
    if profile is None:
        return None
    status = (state.get("verdict") or {}).get("status", "")
    if status not in ("VERIFIED", "NOT_REPRODUCED"):
        return profile  # a broken test says nothing about the contributor's interests

    module = module_key(state.get("target_subdir") or "") or module_key((state.get("suspect_files") or [""])[0])
    if module:
        profile.module_counts[module] = profile.module_counts.get(module, 0) + 1
    lang = LANG_NAMES.get(str(state.get("target_language", "")).lower())
    if lang:
        profile.language_counts[lang] = profile.language_counts.get(lang, 0) + 1
    _refresh_focus(profile)
    _save(kensho_dir, profile)
    return profile


def _distil_style(activity: list[dict]) -> StyleDelta | None:
    """Optional LLM step; any failure (no API key, rate limit) just skips it."""
    comments = [c for pr in activity for c in pr.get("review_comments", []) if c.strip()]
    commits = [m for pr in activity for m in pr.get("commit_messages", []) if m.strip()]
    if not comments and not commits:
        return None
    try:
        from core.llm import call_structured_llm

        return call_structured_llm(
            "You summarise a contributor's habits from their merged pull requests for an engineering profile. "
            "Be concrete and short; do not invent facts that are not in the data.",
            "REVIEW COMMENTS RECEIVED:\n" + "\n".join(f"- {c}" for c in comments[:25])
            + "\n\nTHEIR COMMIT MESSAGES:\n" + "\n".join(f"- {m}" for m in commits[:25]),
            StyleDelta,
        )
    except Exception:  # noqa: BLE001
        return None


def sync_profile_from_merged_prs(
    ctx,
    fetcher: Callable[..., list[dict]] | None = None,
    use_llm: bool = True,
    limit: int = 10,
) -> tuple[ContributorProfile | None, int]:
    """Pulls merged PRs newer than `last_synced_pr`; returns (profile, number_of_new_PRs)."""
    profile = _load(ctx.kensho_dir)
    if profile is None:
        return None, 0
    if fetcher is None:
        from core.github_client import fetch_merged_pr_activity as fetcher  # lazy: needs network/token

    activity = fetcher(ctx.upstream_repo, ctx.github_username, limit=limit, since_pr=profile.last_synced_pr)
    if not activity:
        return profile, 0

    modules: Counter = Counter()
    langs: Counter = Counter()
    for pr in activity:
        for fname in pr.get("files", []):
            key = module_key(fname)
            if key:
                modules[key] += 1
            lang = EXT_LANG.get(Path(fname).suffix)
            if lang:
                langs[lang] += 1
    for key, n in modules.items():
        profile.module_counts[key] = profile.module_counts.get(key, 0) + n
    for key, n in langs.items():
        profile.language_counts[key] = profile.language_counts.get(key, 0) + n

    if use_llm:
        delta = _distil_style(activity)
        if delta is not None:
            profile.coding_style = _merge_notes(profile.coding_style, delta.coding_style)
            profile.review_preferences = _merge_notes(profile.review_preferences, delta.review_preferences)

    profile.last_synced_pr = max(profile.last_synced_pr, *(int(pr["number"]) for pr in activity))
    _refresh_focus(profile)
    _save(ctx.kensho_dir, profile)
    return profile, len(activity)
