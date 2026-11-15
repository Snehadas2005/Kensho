"""Turns low-level exceptions into short, actionable messages for the CLI and Streamlit UI."""
import docker
import github


def friendly_error(exc: Exception) -> str:
    if isinstance(exc, docker.errors.DockerException):
        return (
            "Docker is not reachable. Start Docker Desktop and make sure the "
            "`repro-py:latest` and `repro-go:latest` images are built."
        )
    if isinstance(exc, github.GithubException):
        status = getattr(exc, "status", None)
        if status == 401:
            return "GitHub rejected your token. Check `GITHUB_TOKEN` in your `.env` file."
        if status == 403:
            return "GitHub rate limit hit or access denied. Add or refresh `GITHUB_TOKEN` in `.env`."
        if status == 404:
            return "Repository or issue/PR not found. Check the repo name and number."
    if isinstance(exc, RuntimeError) and "All fallback models failed" in str(exc):
        return (
            "Every model in the fallback chain failed (rate limit or bad key?). "
            f"Check `GEMINI_API_KEY` in `.env`. Details: {exc}"
        )
    if isinstance(exc, FileNotFoundError):
        return f"A required file or folder is missing: {exc}"
    return f"{type(exc).__name__}: {exc}"
