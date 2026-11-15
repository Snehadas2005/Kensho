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


def deterministic_verdict(state: dict) -> JudgeVerdict | None:
    """Returns a verdict when the outcome is fully decided by exit codes or logs, else None (ask the LLM)."""
    if is_timeout(state):
        return JudgeVerdict(
            status="TEST_ERROR",
            root_cause_summary="The sandbox timed out before the test finished, so nothing was verified.",
            suggested_fix_direction="Increase the sandbox timeout or narrow the target package / test scope.",
            github_markdown_report="Sandbox execution timed out before test verification completed. No reproduction was confirmed.",
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
            root_cause_summary="The generated test failed to compile, import, or collect before executing target logic.",
            suggested_fix_direction="Fix imports, package declarations, or syntax in the generated reproduction test.",
            github_markdown_report="Generated reproduction test encountered a syntax/build error and is being repaired.",
        )

    return None


def should_retry(state: dict) -> bool:
    """Only retry when the *generated test* is at fault, never for timeouts or Docker problems."""
    if is_timeout(state) or is_sandbox_error(state):
        return False
    status = (state.get("verdict") or {}).get("status")
    return status == "TEST_ERROR" and state.get("retry_count", 0) <= state.get("max_retries", 0)