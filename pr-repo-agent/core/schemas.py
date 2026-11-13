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