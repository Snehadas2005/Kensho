<div align="center">
  <img width="175" height="160" alt="Kensho Icon" src="https://github.com/user-attachments/assets/c90b521b-eb83-4dba-aaa3-6cc7b3cc9e42" />
  <h1>Kensho — Autonomous Open-Source PR Triage & Bug Reproduction Agent</h1>
  <p><em><strong>Kenshō (見性)</strong> — "Seeing into one's true nature / root cause."</em></p>
</div>

<p align="center">
  <a href="https://github.com/Snehadas2005/Kensho/releases/tag/v1.0.0"><img alt="release" src="https://img.shields.io/badge/release-v1.0.0-7C83FD" /></a>
  <img alt="python" src="https://img.shields.io/badge/python-3.11%2B-blue" />
  <img alt="docker" src="https://img.shields.io/badge/sandbox-docker-2496ED" />
</p>

![Kensho UI](docs/kensho-ui.png)

**Kensho** is a reproduction-first, stateful multi-agent engineering tool that automates issue triage, root-cause localization, and sandboxed bug verification for polyglot open-source repositories (Python primary, Go secondary).

Unlike static PR bots that only summarize text diffs—or heavy autonomous coders that explore repositories blindly from scratch—Kensho maintains a **living architecture map** of the target codebase and proves bug reproducibility via **isolated Docker test execution** (`pytest` and `go test`).

---

## Core Engineering Highlights

* **Living Repository Architecture Map (`core/repo_indexer.py`):** Performs an initial onboarding pass over repository documentation (`README.md`, `CONTRIBUTING.md`), build manifests (`pyproject.toml`, `go.mod`), and directory structures to build `repo_map.json`, then incrementally updates only modified modules via `git diff` as new PRs merge.
* **Polyglot Ephemeral Docker Sandboxes (`core/sandbox.py`):** Streams target source trees and generated tests via in-memory `tar` archives (`put_archive`) into network-disabled (`--network none`), memory-capped (`1g`), capability-dropped containers (`repro-py` and `repro-go`) with zero host-filesystem pollution.
* **Cyclical Self-Correction Loop (`core/graph.py`):** Orchestrated with **LangGraph**, separating `TEST_ERROR` (syntax/import mistakes in generated tests) from `VERIFIED` failures (genuine `AssertionError`s or Go panics), automatically feeding `stderr` tracebacks back to the Coder agent up to `max_retries`.
* **Deterministic Judge Guardrails:** Enforces Pydantic structured schemas across all LLM calls and blocks false-positive `VERIFIED` verdicts whenever sandbox runs time out (`exit_code == 124`) or pass (`exit_code == 0`).
* **Semantic PR Diff Compression (`core/github_client.py`):** Filters auto-generated protobuf files (`*.pb.go`, `*_pb2.py`), lockfiles, and vendor directories before LLM inference.
* **Resilient Free-Tier Model Routing (`core/llm.py`):** Powered by **LiteLLM** with automatic fallback across Google AI Studio models (`gemini-3.8-flash` $\rightarrow$ `gemini-3.5-flash-lite` $\rightarrow$ `gemma-4-26b-a4b-it`).

---

## System Architecture

```text
[1. Repo Onboarding & Sync]
   └── Reads docs + directory tree ──► builds `repo_map.json` (syncs incrementally via git SHA)

[2. Daily Trigger]: `kensho issue <number>` or `pr <number>` (opens the UI; `--cli` for terminal output)
       │
       ▼
┌──────────────────────────────────────────────────────────┐
│ Node 1: Triage & Localization Agent                      │
│ • Reads `repo_map.json` + Issue/Compressed PR Diff       │
│ • Selects runtime (`python` vs `go`) & suspect files     │
└──────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────┐
│ Node 2: Explorer & Test Architect Agent                  │
│ • Inspects suspect files (`read_file_snippet`, `grep`)   │
│ • Drafts minimal `pytest` or `go test` reproduction      │
└──────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────┐
│ Node 3: Polyglot Docker Sandbox (`--network none`)       │
│ • Streams repo + test via `put_archive` into container   │
│ • Executes `pytest` or `go test` & captures `stderr`     │
└──────────────────────────────────────────────────────────┘
       │
       ├──► [TEST_ERROR: Syntax/Import Issue] ──► Loops back to Node 2 (Max N retries)
       │
       ▼
┌──────────────────────────────────────────────────────────┐
│ Node 4: Judge & Verifier Agent (+ Deterministic Guards)  │
│ • Verifies stack trace against original issue report     │
│ • Outputs `VERIFIED` + ready-to-post GitHub Markdown     │
└──────────────────────────────────────────────────────────┘

```

---

## Project Structure

```text
pr-repo-agent/
├── main.py                  # Typer + Rich CLI (`index`, `sync`, `issue`, `pr`; `--cli` for terminal-only)
├── pyproject.toml           # Installs the `kensho` / `git kensho` commands
├── app.py                   # Streamlit UI for inspecting traces, logs & Markdown reports
├── requirements.txt         # Python dependencies
├── .env.example             # Template for API keys
├── .streamlit/config.toml   # Streamlit theme
├── tests/                   # Unit tests for judge guards, path safety & diff filtering
├── core/
│   ├── __init__.py          # Package version
│   ├── errors.py            # Friendly error messages (Docker / GitHub / LLM)
│   ├── schemas.py           # Pydantic models & LangGraph TypedDict state
│   ├── llm.py               # LiteLLM multi-model router with structured output validation
│   ├── repo_indexer.py      # Initial & incremental SHA-based architecture mapper
│   ├── github_client.py     # PyGithub fetcher with auto-generated file diff filtering
│   ├── tools.py             # Bounded codebase reader & regex/keyword grep tools
│   ├── sandbox.py           # OS-independent Docker tar-stream runner (Python & Go)
│   └── graph.py             # 4-node LangGraph state machine & deterministic judge guards
└── docker/
    ├── Dockerfile.python    # Pre-warmed Python 3.11 + pytest + kfp SDK image
    └── Dockerfile.go        # Pre-warmed Go toolchain + cached go.mod dependencies

```

---

## Quickstart

### 1. Prerequisites

* Python 3.11+
* Docker Desktop (running)
* Free [Google AI Studio API Key](https://aistudio.google.com/) and a GitHub Personal Access Token

### 2. Installation & Environment Setup

```bash
git clone https://github.com/Snehadas2005/Kensho.git
cd Kensho/pr-repo-agent

python -m venv .venv
source .venv/bin/activate  # On Windows Git Bash: source .venv/Scripts/activate
pip install -e .          # installs the `kensho` and `git kensho` commands + dependencies

```

Copy the template and fill in your keys:

```bash
cp .env.example .env
```

```env
GEMINI_API_KEY=your_google_ai_studio_key
GITHUB_TOKEN=your_github_personal_access_token
```

### 3. Clone Target Repository & Build Pre-Warmed Sandboxes

```bash
mkdir -p workspace
git clone --depth 50 https://github.com/kubeflow/pipelines.git workspace/pipelines

docker build -t repro-py:latest -f docker/Dockerfile.python .
docker build -t repro-go:latest -f docker/Dockerfile.go .

```

### 4. Index & Run Kensho

```bash
# 1. Build initial Repo Architecture Map (one-time)
kensho index --repo kubeflow/pipelines

# 2. Pull the latest commits and incrementally update the map
kensho sync

# 3. Triage & reproduce a GitHub Issue -> opens the Streamlit UI and runs it automatically
kensho issue 13401

# 4. Same, but terminal-only output (no browser)
kensho issue 13401 --cli

# 5. Triage & verify a Pull Request (UI by default, --cli for terminal)
kensho pr 1333
kensho pr 1333 --cli

# 6. Open the UI on its own (shows your last run, or pick a target in the sidebar)
streamlit run app.py

# 7. Run the unit tests
pytest
```

`git kensho issue 13401` works too: the install also provides a `git-kensho` command, and git treats any `git-<name>` executable on your `PATH` as a subcommand (activate your virtual environment first). You can also run everything without installing via `python main.py <command>`.

> `kensho` can be run from any directory. The Docker builds and `streamlit run app.py` must be run from inside `pr-repo-agent/`.

---

## Roadmap

* **v1.0.0 (Current):** Persistent `repo_map.json` with incremental SHA sync, polyglot Python/Go Docker sandboxes, 4-node LangGraph self-correction loop, Rich CLI, and Streamlit trace viewer.
* **v2 (Planned):** Contributor profiling (analyzing a developer's past merged PRs + interactive 3-question Gemini onboarding CLI), ChromaDB vector memory for similar past issues/tests, and Tree-sitter AST symbol indexing.
* **v3 (Planned):** GitHub Actions webhook automation (`issues.opened` / `pull_request`) and a Kubernetes Job execution backend for heavy integration suites.
* **v4 (Planned):** FastAPI + React observability dashboard.
