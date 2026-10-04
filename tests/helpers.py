"""Test doubles. No network, no real LLM: everything is deterministic."""
from __future__ import annotations

import hashlib
import re

from hindsight_lite.embeddings import EmbeddingError


class HashEmbedder:
    """Bag-of-words hashing embedder: shared words -> higher cosine similarity."""

    provider = "fake"
    model = "hash"

    def __init__(self, dim: int = 64):
        self.dim = dim
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        out = []
        for text in texts:
            vec = [0.0] * self.dim
            for word in re.findall(r"\w+", str(text).lower()):
                vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dim] += 1.0
            if not any(vec):
                vec[0] = 1.0
            out.append(vec)
        return out


class FailingEmbedder:
    provider = "fake"
    model = "hash"

    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        raise EmbeddingError("embedding request failed: TimeoutError")


class ScriptedExtractor:
    """Returns queued extraction payloads, one per extract() call."""

    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.seen = []

    def extract(self, text):
        self.seen.append(text)
        return self.payloads.pop(0) if self.payloads else {"memories": []}


def fact(content, *, subtype="preference", entities=(), relationships=(), importance=0.6, confidence=0.8):
    return {
        "content": content, "memory_type": "world", "subtype": subtype,
        "importance": importance, "confidence": confidence, "durable": True,
        "entities": [{"name": n, "entity_type": "thing"} for n in entities],
        "relationships": [
            {"subject": s, "predicate": p, "object": o, "confidence": 0.8} for s, p, o in relationships
        ],
    }


def payload(*facts):
    return {"memories": list(facts)}


class ScriptedJudge:
    """Judge double: `fn(new_fact, candidates) -> Judgement` (or raises)."""

    def __init__(self, fn):
        self.fn = fn
        self.calls = []

    def judge(self, new_fact, candidates):
        self.calls.append((new_fact, [dict(c) for c in candidates]))
        return self.fn(new_fact, candidates)
