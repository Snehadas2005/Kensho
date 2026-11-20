"""Shared test helpers (importable because pytest puts tests/ on sys.path)."""
import hashlib

import chromadb


class HashEmbedding(chromadb.EmbeddingFunction):
    """Deterministic bag-of-words embedding so tests never download a model."""

    def __init__(self):
        pass

    @staticmethod
    def name() -> str:
        return "kensho-test-hash"

    def get_config(self):
        return {}

    @staticmethod
    def build_from_config(config):
        return HashEmbedding()

    def __call__(self, input):
        out = []
        for text in input:
            vec = [0.0] * 64
            for word in text.lower().split():
                vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % 64] += 1.0
            norm = sum(v * v for v in vec) ** 0.5 or 1.0
            out.append([v / norm for v in vec])
        return out
