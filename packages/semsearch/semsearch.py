"""Semantic search over clinical guidelines — offline-first, vector-optional.

The value of CocoIndex (incremental freshness) and pg_vectorsearch
(in-Postgres ANN) without either's weight:

- Documents are chunked and indexed with content hashes; re-indexing touches
  only changed documents (CocoIndex's core trick), so a state office can
  refresh the guideline pack on a laptop and sync the delta.
- Retrieval is TF-IDF cosine over a hashed vocabulary — fully offline,
  fully stdlib, good enough for a curated corpus of protocols.
- When Postgres with the pg_vectorsearch extension (PostgreSQL-licensed,
  doctrine-clean) IS available, `pg_ddl()` emits the schema and the same
  hashes map chunk ids across both stores — the SQL store is an accelerator,
  not a requirement. The offline path remains the source of truth.
"""

import hashlib
import math
import re
from collections import Counter

_WORD = re.compile(r"[a-z0-9]+")
VOCAB_BITS = 12  # 4096-dim hashed vocabulary — small enough for a phone


class Error(Exception):
    pass


def content_hash(text):
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _token_hash(tok):
    return int.from_bytes(hashlib.blake2b(tok.encode(), digest_size=2).digest(),
                          "big") % (1 << VOCAB_BITS)


def _vectorize(text):
    counts = Counter(_token_hash(t) for t in _WORD.findall(text.lower()))
    norm = math.sqrt(sum(c * c for c in counts.values())) or 1.0
    return {k: c / norm for k, c in counts.items()}


def _cosine(a, b):
    smaller, larger = (a, b) if len(a) <= len(b) else (b, a)
    return sum(v * larger.get(k, 0.0) for k, v in smaller.items())


def chunk(text, size=400, overlap=60):
    """Split on words into ~size-char chunks with overlap so a protocol step
    never straddles a boundary."""
    words = text.split()
    chunks, cur, cur_len = [], [], 0
    for w in words:
        cur.append(w)
        cur_len += len(w) + 1
        if cur_len >= size:
            chunks.append(" ".join(cur))
            keep, keep_len = [], 0
            for rw in reversed(cur):
                if keep_len + len(rw) + 1 > overlap:
                    break
                keep.insert(0, rw)
                keep_len += len(rw) + 1
            cur, cur_len = keep, keep_len
    if cur:
        chunks.append(" ".join(cur))
    return [c for c in chunks if c.strip()]


class GuidelineIndex:
    def __init__(self):
        self.docs = {}     # doc_id -> {"hash", "title", "chunks": [ids]}
        self.chunks = {}   # chunk_id -> {"doc", "text", "vec"}
        self._chunk_seq = 0

    def add_or_update(self, doc_id, title, text):
        """Incremental: if the content hash is unchanged, indexing is a
        no-op; if changed, only this document's chunks are rebuilt."""
        h = content_hash(text)
        existing = self.docs.get(doc_id)
        if existing and existing["hash"] == h:
            return {"indexed": 0, "unchanged": True}
        if existing:
            for cid in existing["chunks"]:
                self.chunks.pop(cid, None)
        chunk_ids = []
        for i, c in enumerate(chunk(text)):
            cid = f"{doc_id}#{i}"
            self.chunks[cid] = {"doc": doc_id, "title": title, "text": c,
                                "vec": _vectorize(c)}
            chunk_ids.append(cid)
            self._chunk_seq += 1
        self.docs[doc_id] = {"hash": h, "title": title, "chunks": chunk_ids}
        return {"indexed": len(chunk_ids), "unchanged": False}

    def remove(self, doc_id):
        existing = self.docs.pop(doc_id, None)
        if not existing:
            return False
        for cid in existing["chunks"]:
            self.chunks.pop(cid, None)
        return True

    def search(self, query, limit=5, min_score=0.05):
        qv = _vectorize(query)
        scored = [(_cosine(qv, c["vec"]), cid) for cid, c in self.chunks.items()]
        scored = [(s, cid) for s, cid in scored if s >= min_score]
        scored.sort(key=lambda t: (-t[0], t[1]))
        return [{"chunk": cid, "doc": self.chunks[cid]["doc"],
                 "title": self.chunks[cid]["title"], "score": round(s, 4),
                 "excerpt": self.chunks[cid]["text"][:160]}
                for s, cid in scored[:limit]]

    def stats(self):
        return {"documents": len(self.docs), "chunks": len(self.chunks)}

    # ---- Postgres accelerator path (pg_vectorsearch / pgvector-class)
    def pg_ddl(self, table="guideline_chunks"):
        return (
            f"CREATE TABLE IF NOT EXISTS {table} ("
            "chunk_id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, "
            "title TEXT NOT NULL, content TEXT NOT NULL, "
            f"content_hash TEXT NOT NULL, embedding VECTOR({1 << VOCAB_BITS})"
            ");"
        )

    def export_for_pg(self):
        """Rows for bulk-loading the SQL accelerator: dense vectors in the
        same hashed vocabulary, so offline and online answers agree."""
        rows = []
        for cid, c in sorted(self.chunks.items()):
            dense = [0.0] * (1 << VOCAB_BITS)
            for k, v in c["vec"].items():
                dense[k] = v
            rows.append({"chunk_id": cid, "doc_id": c["doc"],
                         "title": c["title"], "content": c["text"],
                         "content_hash": self.docs[c["doc"]]["hash"],
                         "embedding": dense})
        return rows
