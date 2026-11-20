"""Deterministic judge guardrails. No LLM, no Docker, so they are trivially testable."""
from core.schemas import JudgeVerdict

TIMEOUT_EXIT_CODE = 124
SANDBOX_ERROR_MARKER = "Sandbox execution error"

# Deterministic markers indicating the generated test itself failed to compile, import, or collect
TEST_SETUP_ERROR_MARKERS = (
    "SyntaxError:",
    "IndentationError:",
    "ImportError:",
    "ModuleNotFoundError:",
    "collected 0 items",
    "[build failed]",
    "cannot find package",
    "no non-test Go files",
    "no test files",
)

ENVIRONMENT_ERROR_MARKERS = (
    "go.mod file not found",
    "Sandbox setup error",
    "go mod download failed",
    "missing go.sum entry",
    "proxy.golang.org",
    "GOPROXY list is not the empty string",
    "toolchain not available",
)


def is_environment_error(stderr: str, stdout: str = "") -> bool:
    combined = f"{stdout or ''}\n{stderr or ''}"
    return any(marker in combined for marker in ENVIRONMENT_ERROR_MARKERS)


def environment_error_detail(stderr: str, stdout: str = "", limit: int = 600) -> str:
    text = (stderr or stdout or "").strip()
    return text[-limit:]


def is_timeout(state: dict) -> bool:
    return state.get("exit_code") == TIMEOUT_EXIT_CODE


def is_sandbox_error(state: dict) -> bool:
    return SANDBOX_ERROR_MARKER in (state.get("stderr") or "")


def is_test_setup_error(state: dict) -> bool:
    """Detects syntax, import, collection, or Go compilation failures before target logic runs."""
    exit_code = state.get("exit_code")
    combined_output = f"{state.get('stdout') or ''}\n{state.get('stderr') or ''}"

    # pytest exit codes 2 (collection/syntax error) and 4 (CLI/usage/path error)
    if state.get("target_language") == "python" and exit_code in (2, 4):
        return True

    return any(marker in combined_output for marker in TEST_SETUP_ERROR_MARKERS)

def _compile_excerpt(state: dict, limit: int = 700) -> str:
    stderr = (state.get("stderr") or "").strip()
    stdout = (state.get("stdout") or "").strip()
    text = stderr or stdout
    return text[:limit]


def deterministic_verdict(state: dict) -> JudgeVerdict | None:
    """Returns a verdict when the outcome is fully decided by exit codes or logs, else None (ask the LLM)."""
    if is_timeout(state):
        return JudgeVerdict(
            status="TEST_ERROR",
            root_cause_summary="The sandbox timed out before the test finished, so nothing was verified.",
            suggested_fix_direction="Increase the sandbox timeout or narrow the target package / test scope.",
            github_markdown_report="Sandbox execution timed out before test verification completed. No reproduction was confirmed.",
        )

    if state.get("exit_code") != 0 and is_environment_error(state.get("stderr") or "", state.get("stdout") or ""):
        detail = environment_error_detail(state.get("stderr") or "", state.get("stdout") or "")
        return JudgeVerdict(
            status="TEST_ERROR",
            root_cause_summary=(
                "The sandbox could not build the project (module/dependency problem), "
                "so the generated test never ran. This is an environment issue, not a verdict on the bug. "
                f"Details: {detail}"
            ),
            suggested_fix_direction=(
                "Check Docker has internet access for the first dependency download, that the repo "
                "checkout contains the Go module (go.mod), then re-run."
            ),
            github_markdown_report="Sandbox environment/setup failure; no reproduction was attempted.",
        )

    if is_sandbox_error(state):
        return JudgeVerdict(
            status="TEST_ERROR",
            root_cause_summary="The sandbox container failed to run (Docker or image problem), not the test itself.",
            suggested_fix_direction="Check that Docker is running and the `repro-py` / `repro-go` images are built.",
            github_markdown_report="The Docker sandbox failed to start, so no reproduction was attempted.",
        )

    if state.get("exit_code") == 0:
        return JudgeVerdict(
            status="NOT_REPRODUCED",
            root_cause_summary="The generated test passed (exit code 0), so the reported bug was not triggered.",
            suggested_fix_direction="The hypothesis may be wrong, or the bug needs a different input or environment.",
            github_markdown_report=(
                "**Not reproduced.** A generated reproduction test ran in an isolated sandbox and passed "
                "(exit code 0), so the reported behavior could not be confirmed."
            ),
        )

    if is_test_setup_error(state):
        return JudgeVerdict(
            status="TEST_ERROR",
            root_cause_summary=(
                "The generated test failed to compile, import, or collect before executing target logic. "
                f"Compiler output: {_compile_excerpt(state)}"
            ),
            suggested_fix_direction="Fix imports, package declarations, or syntax in the generated reproduction test.",
            github_markdown_report=(
                "No reproduction was verified: the generated test did not compile or run. "
                f"Compiler output:\n{_compile_excerpt(state)}"
            ),
        )

    return None


def should_retry(state: dict) -> bool:
    """Only retry when the *generated test* is at fault, never for timeouts or Docker problems."""
    if is_timeout(state) or is_sandbox_error(state):
        return False
    if state.get("exit_code") != 0 and is_environment_error(state.get("stderr") or "", state.get("stdout") or ""):
        return False
    status = (state.get("verdict") or {}).get("status")
    return status == "TEST_ERROR" and state.get("retry_count", 0) <= state.get("max_retries", 0)