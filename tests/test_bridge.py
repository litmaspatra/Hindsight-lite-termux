import json
import time

import pytest

from hindsight_lite.hermes import HermesLiteConfig, HermesMemoryBridge, TOOL_SCHEMAS
from hindsight_lite.maintenance import MaintenancePolicy
from hindsight_lite.reconcile import Judgement, Verdict

from .helpers import FailingEmbedder, HashEmbedder, ScriptedExtractor, ScriptedJudge, fact, payload


def write_config(home, **cfg):
    d = home / "hindsight-lite"
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(json.dumps(cfg))


def boot(tmp_path, **cfg):
    write_config(tmp_path, **cfg)
    bridge = HermesMemoryBridge()
    bridge.initialize(session_id="s1", hermes_home=str(tmp_path))
    return bridge


def call(bridge, name, **args):
    return json.loads(bridge.tool(name, args))


def drain(bridge):
    bridge._queue.join()


@pytest.fixture
def bridge(tmp_path):
    b = boot(tmp_path, llm_model="test-model")
    b.retention.extractor = ScriptedExtractor()      # never touch the network in tests
    drain(b)
    yield b
    b.shutdown()


# --- configuration ------------------------------------------------------------

def test_placeholder_values_in_config_count_as_unset(tmp_path):
    write_config(tmp_path, llm_model="YOUR_MODEL", embedding_base_url="http://127.0.0.1:1/v1",
                 embedding_model="YOUR_EMBEDDING_MODEL", llm_api_key_env="YOUR_LLM_API_KEY_ENV")
    cfg = HermesLiteConfig.load(tmp_path / "hindsight-lite" / "config.json")
    assert cfg.llm_model == "" and cfg.embedding_model == "" and cfg.llm_api_key_env == ""


@pytest.mark.parametrize("bad", [
    {"prefetch_min_semantic": 2}, {"superseded_ttl_days": -1}, {"prefetch_embedding_timeout": 0},
    {"keep_change_history": "yes"}, {"expire_unused_days": -5},
])
def test_invalid_new_settings_are_rejected(tmp_path, bad):
    write_config(tmp_path, **bad)
    with pytest.raises(ValueError):
        HermesLiteConfig.load(tmp_path / "hindsight-lite" / "config.json")


def test_without_an_llm_everything_local_still_works(tmp_path):
    b = boot(tmp_path)  # no llm_model at all
    try:
        assert b.retention.extractor is None and b.reflection is None
        assert call(b, "hmem_remember", content="Peter lives in Jaipur")["created"] == 1
        hits = call(b, "hmem_recall", query="where does Peter live? Jaipur")["results"]
        assert hits and "Jaipur" in hits[0]["content"]
        assert "disabled" in call(b, "hmem_status")["retention"]
        assert "error" in call(b, "hmem_reflect", query="anything")
        b.sync_turn("I live in Jaipur", "ok")     # silently skipped, no crash
        assert b._queue.qsize() == 0
    finally:
        b.shutdown()


def test_prefetch_uses_a_short_embedding_timeout_but_background_keeps_the_long_one(tmp_path):
    b = boot(tmp_path, llm_model="m", embedding_base_url="http://127.0.0.1:9/v1",
             embedding_model="e", embedding_timeout=60, prefetch_embedding_timeout=2.5)
    try:
        assert b.retrieval.embedding_backend.timeout == 60
        assert b.fast_retrieval.embedding_backend.timeout == 2.5
    finally:
        b.shutdown()


# --- prefetch ------------------------------------------------------------------

def test_prefetch_injects_only_relevant_memories_and_records_the_recall(bridge):
    emb = HashEmbedder()
    bridge.retrieval.embedding_backend = bridge.fast_retrieval.embedding_backend = bridge.retention.embedding_backend = emb
    call(bridge, "hmem_remember", content="Peter prefers dark mode in Obsidian")
    assert bridge.prefetch("quantum chromodynamics lattice") == ""
    text = bridge.prefetch("what theme does Peter use in Obsidian?")
    assert "dark mode" in text and text.startswith("# Hindsight Lite recalled memory")
    mid = bridge.store.list_memories()[0]["id"]
    assert bridge.store.get_memory(mid)["recall_count"] == 1


def test_dead_embedding_endpoint_costs_two_calls_not_one_per_turn(bridge):
    call(bridge, "hmem_remember", content="Peter prefers dark mode in Obsidian")
    dead = FailingEmbedder()
    bridge.fast_retrieval.embedding_backend = dead
    for _ in range(6):
        assert "dark mode" in bridge.prefetch("which theme does Peter want in Obsidian")  # local arms still answer
    assert dead.calls == 2
    last = bridge.trace()
    assert last["semantic_skipped"] == "circuit_open" and last["injected"] == 1


# --- retention loop --------------------------------------------------------------

def test_trivial_acknowledgements_are_not_sent_to_the_llm(bridge):
    bridge.retention.extractor = ScriptedExtractor()
    for msg in ("ok", "thanks!", "ok thanks", "haan theek hai"):
        bridge.sync_turn(msg, "Sure.")
    drain(bridge)
    assert bridge.retention.extractor.seen == []


def test_extractor_sees_the_user_text_and_only_a_trimmed_assistant_reply(bridge):
    bridge.retention.extractor = ScriptedExtractor(payload(fact("Peter lives in Jaipur", entities=("Peter",))))
    bridge.sync_turn("I live in Jaipur", "A" * 5000)
    drain(bridge)
    seen = bridge.retention.extractor.seen[0]
    assert "I live in Jaipur" in seen and len(seen) < 2000
    assert bridge.trace()["status"] == "ok"


def test_oversized_turn_is_truncated_not_rejected(bridge):
    bridge.retention.extractor = ScriptedExtractor()
    bridge.sync_turn("x " * 100_000, "y " * 100_000)  # must not raise
    drain(bridge)
    assert len(bridge.retention.extractor.seen[0]) <= 125_000


def test_queue_overflow_is_counted_and_reported(bridge):
    drain(bridge)
    bridge._stop.set()                         # park the worker so the queue fills
    bridge._worker.join(timeout=2)
    for i in range(bridge._queue.maxsize + 5):
        bridge.sync_turn(f"my favourite number is {i}", "ok")
    assert call(bridge, "hmem_status")["dropped_turns"] == 5


def test_changed_preference_flows_end_to_end_and_old_fact_is_cleaned(tmp_path):
    b = boot(tmp_path, llm_model="m", superseded_ttl_days=0)
    try:
        b.retention.extractor = ScriptedExtractor(
            payload(fact("Peter likes jazz in the morning", entities=("Peter", "morning music"))),
            payload(fact("Peter likes lo-fi in the morning", entities=("Peter", "morning music"))),
        )
        b.retention.judge = ScriptedJudge(lambda new, c: Judgement(
            (Verdict(c[0]["id"], "replaces"),), "Peter likes lo-fi in the morning (previously jazz)"))
        b.sync_turn("I like jazz in the morning", "Nice.")
        drain(b)
        b.sync_turn("I now prefer lo-fi in the morning", "Got it.")
        drain(b)
        assert b.trace()["superseded"] == 1
        out = call(b, "hmem_purge", dry_run=False)
        assert out["superseded_removed"] == 1
        rows = b.store.list_memories(include_inactive=True)
        assert [r["content"] for r in rows] == ["Peter likes lo-fi in the morning (previously jazz)"]
    finally:
        b.shutdown()


def test_startup_maintenance_removes_dead_rows_in_the_background(tmp_path):
    from hindsight_lite import MemoryStore
    write_config(tmp_path, llm_model="m", superseded_ttl_days=0)
    store = MemoryStore(tmp_path / "hindsight-lite" / "memory.db")
    old, new = (store.create_memory(t, memory_type="world") for t in ("old", "new"))
    store.supersede_memory(old, new)
    b = HermesMemoryBridge()
    b.initialize(session_id="s", hermes_home=str(tmp_path))
    try:
        drain(b)
        assert [r["content"] for r in b.store.list_memories(include_inactive=True)] == ["new"]
    finally:
        b.shutdown()


# --- tools -----------------------------------------------------------------------

def test_forget_is_a_real_delete(bridge):
    call(bridge, "hmem_remember", content="Peter's secret hobby is pottery")
    mid = bridge.store.list_memories()[0]["id"]
    assert call(bridge, "hmem_forget", memory_id=mid)["deleted"] is True
    assert bridge.store.get_memory(mid) is None
    assert call(bridge, "hmem_recall", query="pottery hobby")["results"] == []


def test_purge_defaults_to_a_dry_run(bridge):
    a = bridge.store.create_memory("a", memory_type="world")
    b = bridge.store.create_memory("b", memory_type="world")
    bridge.store.supersede_memory(a, b)
    bridge.config = HermesLiteConfig(superseded_ttl_days=0)
    out = call(bridge, "hmem_purge")
    assert out["dry_run"] is True and out["superseded_removed"] == 1
    assert bridge.store.get_memory(a) is not None


def test_recall_accepts_a_time_window(bridge):
    mid = bridge.store.create_memory("Peter visited Goa", memory_type="experience", event_time="2024-03-01T00:00:00")
    hits = call(bridge, "hmem_recall", query="zzzz", since="2024-01-01", until="2024-12-31")["results"]
    assert [h["memory_id"] for h in hits] == [mid]
    assert call(bridge, "hmem_recall", query="zzzz", since="2025-01-01")["results"] == []


def test_tool_schemas_are_complete():
    names = [t["name"] for t in TOOL_SCHEMAS]
    assert len(names) == len(set(names)) == 11 and "hmem_purge" in names
    recall = next(t for t in TOOL_SCHEMAS if t["name"] == "hmem_recall")
    assert {"since", "until"} <= set(recall["parameters"]["properties"])


# --- obsidian debounce -------------------------------------------------------------

def test_obsidian_sync_is_debounced_but_flushed_on_demand_and_shutdown(tmp_path):
    vault = tmp_path / "vault"
    b = boot(tmp_path, llm_model="m", obsidian_vault=str(vault), obsidian_sync_interval=3600)
    b.retention.extractor = ScriptedExtractor(
        payload(fact("Peter likes jazz", entities=("Peter",))), payload(fact("Peter drinks chai", entities=("Peter",))))
    b.sync_turn("I like jazz", "ok")
    drain(b)
    assert b.trace()["obsidian_sync"]["written"] > 0                 # first change syncs immediately
    b.sync_turn("I drink chai", "ok")
    drain(b)
    assert b.trace()["obsidian_sync"] == "deferred"                  # second within the interval waits
    assert not any("chai" in p.read_text() for p in (vault / "Memories").glob("*.md"))
    b.shutdown()                                                     # dirty mirror is flushed on exit
    assert any("chai" in p.read_text() for p in (vault / "Memories").glob("*.md"))


def test_broken_vault_path_does_not_stop_the_provider(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    b = boot(tmp_path, llm_model="m", obsidian_vault=str(blocker / "vault"))
    try:
        assert b.mirror is None
        assert "obsidian" in call(b, "hmem_status")
    finally:
        b.shutdown()
