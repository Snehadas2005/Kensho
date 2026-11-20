from typing import Literal, TypedDict
from pydantic import BaseModel, Field


class TriageDecision(BaseModel):
    target_language: Literal["python", "go"] = Field(
        description="Primary runtime to reproduce the bug: 'python' (sdk/python) or 'go' (backend/src)"
    )
    target_subdir: str = Field(
        description="Subdirectory inside the repo where the test should run, e.g. 'sdk/python' or 'backend/src/apiserver'"
    )
    suspect_files: list[str] = Field(
        description="1 to 4 exact file paths inside the repo most likely responsible for the bug or affected by the PR"
    )
    search_keywords: list[str] = Field(
        description="2 to 4 function names, class names, or error strings to grep in the codebase"
    )
    hypothesis: str = Field(
        description="Concise technical hypothesis of what is failing and how to trigger it"
    )


class ReproTestDraft(BaseModel):
    explanation: str = Field(description="Brief reasoning behind the reproduction test")
    test_code: str = Field(
        description="Complete, self-contained runnable pytest file (with test_* functions) or Go test file (with TestRepro* functions)"
    )


class JudgeVerdict(BaseModel):
    status: Literal["VERIFIED", "NOT_REPRODUCED", "TEST_ERROR"] = Field(
        description=(
            "'VERIFIED' if the test executed and reproduced the reported bug/assertion/panic; "
            "'TEST_ERROR' if the test itself failed due to bad imports, syntax error, or missing setup; "
            "'NOT_REPRODUCED' if the test passed (exit code 0) without triggering the bug."
        )
    )
    root_cause_summary: str = Field(description="Clear explanation of the root cause based on code and stack trace")
    suggested_fix_direction: str = Field(description="Actionable pointer on which file/function needs fixing")
    github_markdown_report: str = Field(
        description="Clean, ready-to-post GitHub markdown comment summarizing the reproduction proof and stack trace"
    )


class AgentState(TypedDict):
    repo_name: str
    item_type: str  # "issue" or "pr"
    item_number: int
    title: str
    body: str
    diff_summary: str
    repo_map_json: str

    # Populated by Triage Node
    target_language: str
    target_subdir: str
    suspect_files: list[str]
    search_keywords: list[str]
    hypothesis: str

    # Populated by Explorer & Coder Node
    code_context: str
    test_code: str

    # Populated by Sandbox Node
    exit_code: int
    stdout: str
    stderr: str
    duration_sec: float

    # Loop control & Final Verdict
    retry_count: int
    max_retries: int
    verdict: dict

    # v2 add-ons
    repo_root: str
    work_dir: str                 # PR worktree used for the run (empty = local working tree)
    checkout_note: str
    similar_prs: list[dict]       # SimilarPRMatch dicts retrieved during triage
    ast_hits: list[str]           # deterministic AST symbol matches shown to the LLM
    blame_context: str
    model_telemetry: dict         # node -> "model · tokens · latency" (rendered by app.py)
    telemetry_detail: dict        # node -> raw numbers


# ----------------------------------------------------------------------------------------
# v2 add-ons: RAG matches, deterministic AST symbols, contributor profile
# ----------------------------------------------------------------------------------------
class SimilarPRMatch(BaseModel):
    """A past triaged bug / PR retrieved from ChromaDB (structured RAG match)."""
    item_id: str
    title: str
    status: str
    root_cause: str
    test_snippet: str = ""
    distance: float = 0.0


class ASTSymbol(BaseModel):
    kind: str                 # "function" | "method" | "class" | "struct" | "interface" | "type"
    name: str
    signature: str
    line: int
    parent: str = ""          # enclosing class / receiver type, if any


class ASTSymbolMap(BaseModel):
    """Deterministic (tree-sitter) symbol index: file path -> symbols. No LLM involved."""
    root_sha: str = ""
    files: dict[str, list[ASTSymbol]] = Field(default_factory=dict)

    def find(self, names: list[str], limit: int = 12) -> list[tuple[str, ASTSymbol]]:
        """Symbols whose name contains any of `names` (case-insensitive), best matches first."""
        wanted = [n.lower() for n in names if n and len(n) >= 3]
        scored: list[tuple[int, str, ASTSymbol]] = []
        for path, symbols in self.files.items():
            for sym in symbols:
                low = sym.name.lower()
                score = max((3 if low == w else 2 if w in low else 0) for w in wanted) if wanted else 0
                if score:
                    scored.append((score, path, sym))
        scored.sort(key=lambda x: (-x[0], x[1], x[2].line))
        return [(path, sym) for _, path, sym in scored[:limit]]


class ContributorProfile(BaseModel):
    """Moved here from memory/profiler.py. New fields have defaults so old profile.json files still load."""
    github_username: str
    preferred_languages: list[str]
    favorite_subdirs: list[str]
    experience_level: str               # "beginner" | "intermediate" | "advanced"
    focus_areas: list[str]              # e.g. ["sdk", "backend", "ui"]
    summary_bio: str
    # --- maintained automatically by memory/updater.py ---
    focus_modules: list[str] = Field(default_factory=list)
    coding_style: list[str] = Field(default_factory=list)
    review_preferences: list[str] = Field(default_factory=list)
    module_counts: dict[str, int] = Field(default_factory=dict)
    language_counts: dict[str, int] = Field(default_factory=dict)
    last_synced_pr: int = 0
    updated_at: str = ""
