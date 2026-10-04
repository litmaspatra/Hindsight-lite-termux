from __future__ import annotations

import json
import math
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Protocol, Sequence

MAX_EMBEDDING_DIMENSION = 16_384
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_TEXT_CHARS = 100_000
MAX_BATCH_CHARS = 1_000_000


class EmbeddingError(RuntimeError):
    """Raised when an embedding backend returns invalid or unusable data."""


class EmbeddingBackend(Protocol):
    provider: str
    model: str

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one finite, non-zero vector for each input text."""


def validate_vector(vector: Sequence[float], *, expected_dimension: int | None = None) -> tuple[list[float], float]:
    if not vector:
        raise EmbeddingError("embedding vector must not be empty")
    if len(vector) > MAX_EMBEDDING_DIMENSION:
        raise EmbeddingError(f"embedding dimension exceeds {MAX_EMBEDDING_DIMENSION}")
    if expected_dimension is not None and len(vector) != expected_dimension:
        raise EmbeddingError(
            f"embedding dimension mismatch: expected {expected_dimension}, got {len(vector)}"
        )
    clean: list[float] = []
    for value in vector:
        number = float(value)
        if not math.isfinite(number):
            raise EmbeddingError("embedding contains non-finite values")
        clean.append(number)
    norm = math.sqrt(math.fsum(v * v for v in clean))
    if not math.isfinite(norm) or norm <= 0.0:
        raise EmbeddingError("embedding vector must have a finite non-zero norm")
    return clean, norm


def pack_vector(vector: Sequence[float]) -> bytes:
    clean, _ = validate_vector(vector)
    return struct.pack(f"<{len(clean)}f", *clean)


def unpack_vector(payload: bytes, dimension: int) -> list[float]:
    if dimension < 1 or dimension > MAX_EMBEDDING_DIMENSION:
        raise EmbeddingError("invalid stored embedding dimension")
    expected = dimension * 4
    if len(payload) != expected:
        raise EmbeddingError(
            f"stored embedding payload has {len(payload)} bytes; expected {expected}"
        )
    return list(struct.unpack(f"<{dimension}f", payload))


def cosine_similarity(query: Sequence[float], candidate: Sequence[float], *, query_norm: float | None = None, candidate_norm: float | None = None) -> float:
    q, calculated_q_norm = validate_vector(query)
    c, calculated_c_norm = validate_vector(candidate, expected_dimension=len(q))
    qn = calculated_q_norm if query_norm is None else float(query_norm)
    cn = calculated_c_norm if candidate_norm is None else float(candidate_norm)
    if not math.isfinite(qn) or not math.isfinite(cn) or qn <= 0 or cn <= 0:
        raise EmbeddingError("invalid embedding norm")
    return math.fsum(a * b for a, b in zip(q, c)) / (qn * cn)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class OpenAICompatibleEmbeddingBackend:
    """Minimal OpenAI-compatible /embeddings client using only stdlib.

    Plain HTTP is restricted to loopback by default. Remote endpoints must use
    HTTPS unless allow_insecure_remote=True is explicitly set by the operator.
    """

    base_url: str
    model: str
    api_key: str = field(default="", repr=False)
    provider: str = "openai-compatible"
    timeout: float = 30.0
    allow_insecure_remote: bool = False

    def __post_init__(self) -> None:
        parsed = urllib.parse.urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("base_url must be an absolute http(s) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("base_url must not contain credentials, query parameters, or fragments")
        loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if parsed.scheme == "http" and not loopback and not self.allow_insecure_remote:
            raise ValueError("remote embedding endpoints must use HTTPS")
        if not self.model.strip() or len(self.model) > 256:
            raise ValueError("model must be between 1 and 256 characters")
        if not self.provider.strip() or len(self.provider) > 128:
            raise ValueError("provider must be between 1 and 128 characters")
        if self.timeout <= 0 or self.timeout > 300:
            raise ValueError("timeout must be between 0 and 300 seconds")

    @property
    def endpoint(self) -> str:
        return self.base_url.rstrip("/") + "/embeddings"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        clean_texts = [str(text).strip() for text in texts]
        if not clean_texts or any(not text for text in clean_texts):
            raise ValueError("texts must contain at least one non-empty string")
        if len(clean_texts) > 128:
            raise ValueError("embedding batch is limited to 128 texts")
        if any(len(text) > MAX_TEXT_CHARS for text in clean_texts):
            raise ValueError("an embedding input exceeds the per-text size limit")
        if sum(len(text) for text in clean_texts) > MAX_BATCH_CHARS:
            raise ValueError("embedding batch exceeds the total size limit")
        body = json.dumps({"model": self.model, "input": clean_texts}).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(self.endpoint, data=body, headers=headers, method="POST")
        opener = urllib.request.build_opener(_NoRedirect())
        try:
            with opener.open(request, timeout=self.timeout) as response:
                payload = response.read(MAX_RESPONSE_BYTES + 1)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise EmbeddingError(f"embedding request failed: {exc.__class__.__name__}") from exc
        if len(payload) > MAX_RESPONSE_BYTES:
            raise EmbeddingError("embedding response exceeded size limit")
        try:
            decoded = json.loads(payload)
            data = decoded["data"]
        except (json.JSONDecodeError, KeyError, TypeError, RecursionError) as exc:
            raise EmbeddingError("embedding endpoint returned invalid JSON") from exc
        if not isinstance(data, list) or len(data) != len(clean_texts):
            raise EmbeddingError("embedding endpoint returned the wrong number of vectors")
        if any(
            not isinstance(item, dict)
            or "embedding" not in item
            for item in data
        ):
            raise EmbeddingError("embedding response item is malformed")

        # OpenAI responses normally include ``index``. Some compatible
        # gateways preserve request order while omitting it. Support either
        # representation, but reject mixed/ambiguous batches.
        has_index = ["index" in item for item in data]
        if any(has_index) and not all(has_index):
            raise EmbeddingError("embedding response mixes indexed and unindexed items")
        if all(has_index):
            if any(not isinstance(item.get("index"), int) for item in data):
                raise EmbeddingError("embedding response indices are invalid")
            indices = [item["index"] for item in data]
            if sorted(indices) != list(range(len(clean_texts))):
                raise EmbeddingError("embedding response indices are invalid")
            ordered = sorted(data, key=lambda item: item["index"])
        else:
            ordered = data
        vectors: list[list[float]] = []
        dimension: int | None = None
        for item in ordered:
            vector, _ = validate_vector(item["embedding"], expected_dimension=dimension)
            dimension = len(vector)
            vectors.append(vector)
        return vectors


class CircuitBreaker:
    """Stops calling a dead endpoint on the hot path (prefetch).

    After `failure_threshold` consecutive failures the breaker opens and
    allow() is False until `cooldown_seconds` pass; then one probe is allowed.
    """

    def __init__(self, failure_threshold: int = 2, cooldown_seconds: float = 60.0,
                 clock: Callable[[], float] = time.monotonic):
        self.failure_threshold = max(1, int(failure_threshold))
        self.cooldown_seconds = float(cooldown_seconds)
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None

    def allow(self) -> bool:
        if self._opened_at is None:
            return True
        if self._clock() - self._opened_at >= self.cooldown_seconds:
            return True  # half-open: let one probe through
        return False

    def record(self, ok: bool) -> None:
        if ok:
            self._failures = 0
            self._opened_at = None
            return
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._opened_at = self._clock()
