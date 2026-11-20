from pathlib import Path

import chromadb

from core.schemas import SimilarPRMatch

# Backwards-compatible alias (older code imported SimilarBugMatch from here).
SimilarBugMatch = SimilarPRMatch


class RepoVectorMemory:
    """Local per-repo ChromaDB vector store for verified bug reproductions & root causes."""

    def __init__(self, kensho_dir: Path, embedding_function=None):
        db_path = kensho_dir / "chroma_db"
        db_path.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(db_path))
        kwargs = {"embedding_function": embedding_function} if embedding_function is not None else {}
        self.collection = self.client.get_or_create_collection(
            name="kensho_repro_cases",
            metadata={"hnsw:space": "cosine"},
            **kwargs,
        )

    def save_run(self, state: dict) -> None:
        """Persists VERIFIED or NOT_REPRODUCED traces for future few-shot RAG retrieval."""
        verdict = state.get("verdict") or {}
        status = verdict.get("status", "UNKNOWN")
        if status not in ("VERIFIED", "NOT_REPRODUCED"):
            return

        item_id = f"{state.get('item_type', 'issue')}-{state.get('item_number', 0)}"
        doc_text = (
            f"Title: {state.get('title', '')}\n"
            f"Subdir: {state.get('target_subdir', '')}\n"
            f"Hypothesis: {state.get('hypothesis', '')}\n"
            f"Root Cause: {verdict.get('root_cause_summary', '')}"
        )
        self.collection.upsert(
            ids=[item_id],
            documents=[doc_text],
            metadatas=[{
                "title": str(state.get("title", ""))[:300],
                "status": status,
                "root_cause": str(verdict.get("root_cause_summary", ""))[:500],
                "test_snippet": str(state.get("test_code", ""))[:1500],
            }],
        )

    def query_similar(self, title: str, body: str, top_k: int = 3, exclude_id: str = "") -> list[SimilarPRMatch]:
        """Retrieves top-k past triaged bugs in this repository (optionally excluding the current item)."""
        total = self.collection.count()
        if total == 0:
            return []

        n = min(top_k + (1 if exclude_id else 0), total)
        results = self.collection.query(
            query_texts=[f"{title}\n{(body or '')[:1200]}"],
            n_results=n,
        )
        ids = results.get("ids", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        dists = results.get("distances", [[]])[0]

        matches: list[SimilarPRMatch] = []
        for idx, meta, dist in zip(ids, metas, dists):
            if exclude_id and idx == exclude_id:
                continue
            matches.append(SimilarPRMatch(
                item_id=idx,
                title=meta.get("title", ""),
                status=meta.get("status", ""),
                root_cause=meta.get("root_cause", ""),
                test_snippet=meta.get("test_snippet", ""),
                distance=round(float(dist), 4),
            ))
        return matches[:top_k]
