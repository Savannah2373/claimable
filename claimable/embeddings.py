"""Embedding model wrapper. Lazy-loaded so scripts that don't embed stay fast.

Local dev model: BAAI/bge-small-en-v1.5 (384-dim, ~130 MB). Deployment target
is bge-m3 via TEI; keeping the model env-configurable makes that swap (and the
eval comparing them) a config change, not a refactor.
"""

from __future__ import annotations

import os

_MODEL_NAME = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")

# bge models are asymmetric: queries get an instruction prefix, documents don't.
_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(_MODEL_NAME)
    return _model


def embed_documents(texts: list[str]) -> list[list[float]]:
    return _get_model().encode(texts, normalize_embeddings=True).tolist()


def embed_query(text: str) -> list[float]:
    return _get_model().encode(_QUERY_PREFIX + text, normalize_embeddings=True).tolist()
