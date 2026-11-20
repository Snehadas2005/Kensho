<div align="center">
  <img width="175" height="160" alt="Kensho Icon" src="https://github.com/user-attachments/assets/c90b521b-eb83-4dba-aaa3-6cc7b3cc9e42" />
  <h1>Kensho — Autonomous Open-Source PR Triage & Bug Reproduction Agent</h1>
  <p><em><strong>Kenshō (見性)</strong> — "Seeing into one's true nature / root cause."</em></p>
</div>

<p align="center">
  <a href="https://github.com/Snehadas2005/Kensho/releases/tag/v2.0.0"><img alt="release" src="https://img.shields.io/badge/release-v2.0.0-7C83FD" /></a>
  <img alt="python" src="https://img.shields.io/badge/python-3.11%2B-blue" />
  <img alt="docker" src="https://img.shields.io/badge/sandbox-docker-2496ED" />
</p>

**Kensho** helps open-source contributors triage GitHub issues and Pull Requests locally, generate minimal reproduction tests, and run them inside isolated Docker containers—so you can attach verified proof to issues and PRs in minutes.

---

## 1. Prerequisites & Setup (Before Running Any Command)

### A. Get Your API Keys & Tokens
1. **Gemini API Key (Required — Free Tier)**
   Create a key in [Google AI Studio](https://aistudio.google.com/app/apikey).
2. **GitHub Personal Access Token (Recommended)**
   Generate a token in [GitHub Token Settings](https://github.com/settings/tokens) (see [GitHub's token guide](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)) so Kensho can fetch issues, PR diffs, and your merged PR history without hitting anonymous rate limits.
3. **OpenCode Zen API Key (Optional — Free Fallback Router)**
   Sign up and create a key at [OpenCode Auth](https://opencode.ai/auth) (or manage models in [OpenCode Console](https://opencode.ai/console)) so Kensho can fail over automatically if Gemini hits a rate limit.

### B. Install & Open Docker Desktop
1. Download and install [Docker Desktop](https://www.docker.com/products/docker-desktop/) (sign in if prompted on first launch).
2. **Keep Docker Desktop open and running** in the background whenever you use `kensho init`, `kensho issue`, or `kensho pr`.

---

## 2. Installation & Terminal Commands

### Step 1: Install Kensho directly into your forked repository
```bash
# cd into your cloned fork (no need to clone Kensho separately) and install the version-2 CLI directly via pip:
cd /path/to/your/forked_repo

# Create and activate a virtual environment (Python 3.11+)
python -m venv .venv
source .venv/bin/activate        # Git Bash / Linux / macOS
# .venv\Scripts\activate         # Windows PowerShell

# Install Kensho v2 (`kensho` + `git kensho` commands) directly from GitHub
pip install "git+https://github.com/Snehadas2005/Kensho.git@version-2"
```

### Step 2: Save your API keys
```bash
git kensho config 
```
OR 
```bash
kensho config 
```
Prompts for `GEMINI_API_KEY`, optional `OPENCODE_ZEN_API_KEY`, and optional `GITHUB_TOKEN`, saving them to `~/.kensho/.env`.

### Step 3: Initialize your forked repository
`cd` into **your cloned fork** (for example, `~/Desktop/forked_repo` where `origin` is your fork and `upstream` is the main org repo):
```bash
cd ~/Desktop/forked_repo
git remote -v                    # Verify `origin` (your fork) and `upstream` (target org)

git kensho init
```
OR 
```bash
kensho init
```
* Detects `origin` → `upstream`, builds `.kensho/repo_map.json`, detects the tech stack, and builds the cached Docker sandbox image (`kensho-sandbox-<repo>:latest`).
* Use `git kensho init --rebuild-image` if you ever need to force-rebuild the Docker image.

### Step 4: Build your contributor profile & get issue recommendations
```bash
git kensho onboard               # 3-question onboarding + scans your past merged PRs
git kensho recommend             # Ranks open upstream issues matched to your skills
```
OR
```bash
kensho onboard               # 3-question onboarding + scans your past merged PRs
kensho recommend             # Ranks open upstream issues matched to your skills
```

### Step 5: Reproduce an Issue or verify a PR
```bash
# Opens the Streamlit UI automatically for Issue #1378
git kensho issue 1378

# Opens the Streamlit UI automatically for PR #1501
git kensho pr 1501

# Terminal-only mode (no browser UI)
git kensho issue 1378 --cli
git kensho pr 1501 --cli

# Optional: change max self-correction retries (default: 2)
git kensho issue 1378 --max-retries 3 --cli
```
OR
```bash
# Opens the Streamlit UI automatically for Issue #1378
kensho issue <issue_number>

# Opens the Streamlit UI automatically for PR #1501
kensho pr <pr_number>

# Terminal-only mode (no browser UI)
kensho issue <issue_number> --cli
kensho pr <pr_number> --cli

# Optional: change max self-correction retries (default: 2)
kensho issue <issue_number> --max-retries 3 --cli
```

### Step 6: Keep your Repo Map up to date
```bash
git kensho sync                  # Pulls/syncs `repo_map.json` with newly merged commits
```
OR
```bash
kensho sync                  # Pulls/syncs `repo_map.json` with newly merged commits
```

---

## 3. Glossary — Every Term & Status You need to Know

### Verdict Statuses
* **`VERIFIED`**: The generated test compiled, ran against the real codebase, and **triggered the reported bug** (failed with an assertion/error matching the issue).
* **`NOT REPRODUCED`**: The generated test ran cleanly and **passed** (exit code `0`), meaning the bug was not triggered (or the PR already fixes/implements it).
* **`TEST ERROR`**: The generated test itself could not run—either due to a syntax/import/compile error or a Docker/sandbox setup error.
* **`TIMEOUT`**: The sandbox container ran longer than the allowed timeout and was stopped.
* **`SANDBOX ERROR`**: Docker failed to start, create, or execute the container.

### Metrics Row (Top of Results)
* **Status**: Final verdict (`VERIFIED`, `NOT REPRODUCED`, `TEST ERROR`, `TIMEOUT`).
* **Runtime**: Programming language used to run the test (`PYTHON` or `GO`).
* **Attempts**: Total number of sandbox runs (1 initial run + up to `max_retries` self-correction retries).
* **Sandbox time**: Total seconds spent executing inside Docker.

### Exit Codes
* **`0` (`clean exit`)**: Every test in the run passed.
* **`1` (`non-zero exit`)**: A test failed (assertion/panic) or failed to compile/import.
* **`124` (`timed out`)**: The container exceeded its timeout limit.

### Agent Pipeline Steps
* **`prepare_checkout`**: For `pr` runs, fetches `pull/<n>/head` into a temporary `git worktree` so your working branch is never modified.
* **`Triage & localization`**: Reads the Repo Map, Tree-sitter AST symbols, and ChromaDB memory to pick the language, target folder, suspect files, and bug hypothesis.
* **`Writing reproduction test`**: Generates a minimal `pytest` or `go test` file (called a **reproduction test**) using real code snippets and `git blame`.
* **`Running in Docker sandbox`**: Packages the code into a tar stream and runs it inside an isolated container with no internet access during the test.
* **`Judging result`**: Inspects the exit code, `STDOUT`, and `STDERR` to decide `VERIFIED`, `NOT REPRODUCED`, or `TEST ERROR`.
* **`memory_updater`**: Saves the run to ChromaDB, updates your contributor `profile.json`, and cleans up any temporary PR worktree.

### Sidebar, Triage & Sandbox Fields
* **Repo map**: `.kensho/repo_map.json` — a compact map of the repository's modules, languages, and last indexed commit SHA.
* **Runtime stack**: The detected languages/toolchains in the repository (e.g., `go, node` or `python`).
* **Sandbox image**: The Docker image tag built for your repo (e.g., `kensho-sandbox-kubeflow-notebooks:latest`).
* **Target**: The language and subdirectory where the reproduction test runs.
* **Suspected files**: Exact file paths in the repo linked to the issue or PR.
* **Search Keywords**: Function names, symbols, or error strings used to search the codebase.
* **Hypothesis**: Plain-English theory of why the bug happens and how to trigger it.
* **Root cause**: Summary of why the test failed or passed after running in Docker.
* **Suggested fix direction**: Concrete pointer to which file or function should be changed to fix the issue.
* **STDOUT**: Standard output from the test runner, showing `RUN`, `PASS`, or `FAIL` for each test case (`STDOUT fail` = failed test output; `STDOUT pass` = passing test output; `STDOUT run` = test execution trace).
* **STDERR**: Error stream containing compiler errors, missing packages, or stack traces.
* **Test snippet**: A short preview of a past verified reproduction test retrieved from ChromaDB (`Similar PRs` tab) to guide new runs.
