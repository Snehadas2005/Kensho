<div align="center">
  <img width="175" height="160" alt="Kensho Icon" src="https://github.com/user-attachments/assets/c90b521b-eb83-4dba-aaa3-6cc7b3cc9e42" />
  <h1>Kensho — Autonomous Open-Source PR Triage & Bug Reproduction Agent</h1>
  <p><em><strong>Kenshō (見性)</strong> — "Seeing into one's true nature / root cause."</em></p>
</div>

<p align="center">
  <a href="https://github.com/Snehadas2005/Kensho/releases/tag/v1.0.0"><img alt="release" src="https://img.shields.io/badge/release-v2.0.0-7C83FD" /></a>
  <img alt="python" src="https://img.shields.io/badge/python-3.11%2B-blue" />
  <img alt="docker" src="https://img.shields.io/badge/sandbox-docker-2496ED" />
</p>

![Kensho UI](docs/kensho-ui.png)

**Kensho** is a reproduction-first, stateful multi-agent engineering tool that automates issue triage, root-cause localization, and sandboxed bug verification for polyglot open-source repositories (Python primary, Go secondary).

Unlike static PR bots that only summarize text diffs—or heavy autonomous coders that explore repositories blindly from scratch—Kensho maintains a **living architecture map** of the target codebase and proves bug reproducibility via **isolated Docker test execution** (`pytest` and `go test`).


## Project Structure

```text
Kensho/                                       # Repository root (branch: version-2)
├── assets/
│   └── kensho-logo.png                       # Brand logo
├── docs/
│   └── kensho-ui.png                         # Updated v2 editorial UI screenshot
├── pytest.ini
├── requirements.txt                          # Adds chromadb, tree-sitter, tree-sitter-python, tree-sitter-go
└── pr-repo-agent/
    ├── .streamlit/
    │   └── config.toml
    ├── pyproject.toml                        # Version 2.0.0 (`kensho` + `git kensho` entrypoints)
    ├── main.py                               # Adds `init`, `onboard`, `recommend`, `issue`, `pr`, `ui`
    ├── app.py                                # Editorial Streamlit trace viewer + Profile & Similar-Bugs tabs
    ├── ui_theme.py                           # Editorial grid CSS + Cobalt/Sky Blue & White palette (Light/Dark)
    ├── core/
    │   ├── __init__.py                       # __version__ = "2.0.0"
    │   ├── errors.py                         # Typed domain exceptions (RepoContextError, SandboxBuildError)
    │   ├── schemas.py                        # Adds RepoContext, ContributorProfile, SimilarBugMatch, StackSpec
    │   ├── git_context.py                    # [NEW] Auto-detects `origin` (user fork) + `upstream` (org repo)
    │   ├── stack_detector.py                 # [NEW] Detects Python/Go/Node/Rust manifests & builds cached image
    │   ├── llm.py                            # LiteLLM router + per-node token/latency telemetry
    │   ├── repo_indexer.py                   # Upgraded with Tree-sitter AST signatures + incremental SHA sync
    │   ├── github_client.py                  # Adds user merged PR/commit history fetcher & open issue scanner
    │   ├── tools.py                          # Adds `get_git_blame` and `search_past_bugs`
    │   ├── sandbox.py                        # Uses per-repo dynamic image `kensho-sandbox-<slug>:latest`
    │   ├── guards.py                         # Deterministic exit-code & setup-error guardrails
    │   └── graph.py                          # 6-node graph: MemoryRetrieve -> Triage -> Coder -> Sandbox -> Judge -> MemorySave
    ├── memory/                               # [NEW] Persistent per-repo & global contributor intelligence
    │   ├── __init__.py
    │   ├── profiler.py                       # Scans user's GitHub PRs + 3-question Gemini CLI -> `profile.json`
    │   ├── recommender.py                    # Ranks open upstream issues matched to user's stack & PR history
    │   └── vector_store.py                   # ChromaDB index of verified repro tests & root causes
    ├── docker/                               # Base fallback Dockerfiles + dynamic Dockerfile generator
    │   ├── Dockerfile.python
    │   └── Dockerfile.go
    └── tests/
        ├── test_guards.py
        ├── test_git_context.py
        └── test_stack_detector.py
```

---

## Roadmap

* **v1.0.0 (Deployed):** Persistent `repo_map.json` with incremental SHA sync, polyglot Python/Go Docker sandboxes, 4-node LangGraph self-correction loop, Rich CLI, and Streamlit trace viewer.
* **v2 (Current):** Contributor profiling (analyzing a developer's past merged PRs + interactive 3-question Gemini onboarding CLI), ChromaDB vector memory for similar past issues/tests, and Tree-sitter AST symbol indexing.
* **v3 (Planned):** GitHub Actions webhook automation (`issues.opened` / `pull_request`) and a Kubernetes Job execution backend for heavy integration suites.
* **v4 (Planned):** FastAPI + React observability dashboard.
