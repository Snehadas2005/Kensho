from pathlib import Path
import chromadb
from pydantic import BaseModel


class SimilarBugMatch(BaseModel):
    item_id: str
    title: str
    status: str
    root_cause: str
    test_snippet: str
    distance: float


class RepoVectorMemory:
    """Local per-repo ChromaDB vector store for verified bug reproductions & root causes."""

    def __init__(self, kensho_dir: Path):
        db_path = kensho_dir / "chroma_db"
        db_path.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(db_path))
        self.collection = self.client.get_or_create_collection(
            name="kensho_repro_cases",
            metadata={"hnsw:space": "cosine"},
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

    def query_similar(self, title: str, body: str, top_k: int = 3) -> list[SimilarBugMatch]:
        """Retrieves top-k past triaged bugs in this repository."""
        if self.collection.count() == 0:
            return []

        n = min(top_k, self.collection.count())
        results = self.collection.query(
            query_texts=[f"{title}\n{body[:1200]}"],
            n_results=n,
        )
        matches: list[SimilarBugMatch] = []
        ids = results.get("ids", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        dists = results.get("distances", [[]])[0]

        for idx, meta, dist in zip(ids, metas, dists):
            matches.append(SimilarBugMatch(
                item_id=idx,
                title=meta.get("title", ""),
                status=meta.get("status", ""),
                root_cause=meta.get("root_cause", ""),
                test_snippet=meta.get("test_snippet", ""),
                distance=round(float(dist), 4),
            ))
        return matches