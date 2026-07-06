"""Hybrid search over opportunities: dense (pgvector cosine) + lexical
(Postgres full-text), fused with Reciprocal Rank Fusion (RRF), then
optionally re-scored by a cross-encoder reranker for precision.

RRF: score = Σ 1 / (K + rank_in_each_list). Rank-based fusion sidesteps the
problem that cosine similarity and ts_rank live on incomparable scales.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg

from claimable.embeddings import embed_query

_RRF_K = 60  # standard damping constant from the RRF paper
_CANDIDATES = 50  # how deep each retriever goes before fusion


@dataclass
class SearchHit:
    opportunity_id: int
    number: str | None
    title: str
    agency_name: str | None
    close_date: str | None
    score: float
    matched_by: str  # 'dense' | 'lexical' | 'both'
    snippet: str = ""
    rerank_score: float | None = None


_HYBRID_SQL = """
WITH dense AS (
    SELECT id, row_number() OVER (ORDER BY embedding <=> %(qvec)s::vector) AS rank
    FROM opportunities
    WHERE embedding IS NOT NULL
    ORDER BY embedding <=> %(qvec)s::vector
    LIMIT %(candidates)s
),
lexical AS (
    SELECT id, row_number() OVER (
               ORDER BY ts_rank(tsv, websearch_to_tsquery('english', %(qtext)s)) DESC
           ) AS rank
    FROM opportunities
    WHERE tsv @@ websearch_to_tsquery('english', %(qtext)s)
    LIMIT %(candidates)s
),
fused AS (
    SELECT coalesce(d.id, l.id) AS id,
           coalesce(1.0 / (%(rrf_k)s + d.rank), 0)
         + coalesce(1.0 / (%(rrf_k)s + l.rank), 0) AS score,
           CASE WHEN d.id IS NOT NULL AND l.id IS NOT NULL THEN 'both'
                WHEN d.id IS NOT NULL THEN 'dense'
                ELSE 'lexical' END AS matched_by
    FROM dense d FULL OUTER JOIN lexical l USING (id)
)
SELECT o.id, o.number, o.title, o.agency_name, o.close_date::text,
       f.score, f.matched_by,
       o.title || E'\n' || left(coalesce(o.synopsis, ''), 1200) AS snippet
FROM fused f JOIN opportunities o ON o.id = f.id
ORDER BY f.score DESC
LIMIT %(k)s
"""


def hybrid_search(
    conn: psycopg.Connection, query: str, k: int = 10, rerank: bool = True
) -> list[SearchHit]:
    """Hybrid retrieve (recall) → cross-encoder rerank (precision).

    With rerank=True we pull 3x the requested depth from the fused retriever,
    re-score those candidates jointly against the query, and return the top k.
    """
    qvec = embed_query(query)
    fetch_k = min(3 * k, _CANDIDATES) if rerank else k
    with conn.cursor() as cur:
        cur.execute(
            _HYBRID_SQL,
            {
                "qvec": qvec,
                "qtext": query,
                "candidates": _CANDIDATES,
                "rrf_k": _RRF_K,
                "k": fetch_k,
            },
        )
        hits = [SearchHit(*row) for row in cur.fetchall()]

    if not rerank:
        return hits
    from claimable.rerank import rerank as ce_rerank

    scores = ce_rerank(query, [h.snippet for h in hits])
    for h, s in zip(hits, scores):
        h.rerank_score = s
    return sorted(hits, key=lambda h: h.rerank_score, reverse=True)[:k]
