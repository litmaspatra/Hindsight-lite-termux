"""Smart overwrite: a changed fact replaces the stale one; unrelated facts survive."""
import pytest

from hindsight_lite import MemoryStore, RetentionEngine
from hindsight_lite.maintenance import MaintenancePolicy, run_maintenance
from hindsight_lite.reconcile import Judgement, ReconcileError, Verdict, parse_judgement

from .helpers import FailingEmbedder, HashEmbedder, ScriptedExtractor, ScriptedJudge, fact, payload

JAZZ = "Peter likes listening to jazz in the morning"
LOFI = "Peter likes listening to lo-fi in the morning"
MORNING = ("Peter", "morning music")


def make(tmp_path, *payloads, judge=None, embedder=None, keep_history=True):
    store = MemoryStore(tmp_path / "m.db")
    engine = RetentionEngine(
        store, ScriptedExtractor(*payloads), embedding_backend=embedder, judge=judge, keep_history=keep_history
    )
    return store, engine


def replaces_first(merged=None):
    return lambda new, cands: Judgement((Verdict(cands[0]["id"], "replaces"),), merged)


def statuses(store):
    return {m["content"]: m["status"] for m in store.list_memories(include_inactive=True)}


# --- the headline scenario -------------------------------------------------

def test_changed_preference_overwrites_old_and_keeps_history_note(tmp_path):
    merged = "Peter likes listening to lo-fi in the morning (previously: jazz)"
    judge = ScriptedJudge(replaces_first(merged))
    store, engine = make(
        tmp_path, payload(fact(JAZZ, entities=MORNING)), payload(fact(LOFI, entities=MORNING)), judge=judge
    )
    engine.retain("I like jazz in the morning")
    report = engine.retain("Actually now I like lo-fi in the morning")

    assert report.superseded == 1 and report.created == 0
    active = [m for m in store.list_memories(include_inactive=False)]
    assert [m["content"] for m in active] == [merged]
    old = next(m for m in store.list_memories(include_inactive=True) if m["content"] == JAZZ)
    assert old["status"] == "superseded" and old["superseded_by"] == active[0]["id"]
    # the judge was shown the old fact as a candidate
    assert JAZZ in [c["content"] for c in judge.calls[0][1]]

    run_maintenance(store, MaintenancePolicy(superseded_ttl_days=0))
    assert [m["content"] for m in store.list_memories(include_inactive=True)] == [merged]


def test_history_note_is_dropped_when_keep_history_is_off(tmp_path):
    judge = ScriptedJudge(replaces_first("Peter likes lo-fi in the morning (previously: jazz)"))
    store, engine = make(
        tmp_path, payload(fact(JAZZ, entities=MORNING)), payload(fact(LOFI, entities=MORNING)),
        judge=judge, keep_history=False,
    )
    engine.retain("a")
    engine.retain("b")
    assert [m["content"] for m in store.list_memories(include_inactive=False)] == [LOFI]


def test_extension_keeps_both_facts(tmp_path):
    judge = ScriptedJudge(lambda new, cands: Judgement((Verdict(cands[0]["id"], "extends"),)))
    store, engine = make(
        tmp_path, payload(fact(JAZZ, entities=MORNING)),
        payload(fact("Peter drinks chai while listening to music in the morning", entities=MORNING)), judge=judge,
    )
    engine.retain("a")
    report = engine.retain("b")
    assert report.created == 1 and report.superseded == 0
    assert len(store.list_memories(include_inactive=False)) == 2


def test_duplicate_verdict_stores_nothing_new(tmp_path):
    judge = ScriptedJudge(lambda new, cands: Judgement((Verdict(cands[0]["id"], "same"),)))
    store, engine = make(
        tmp_path, payload(fact(JAZZ, entities=MORNING)),
        payload(fact("Peter enjoys jazz music each morning", entities=MORNING)), judge=judge,
    )
    engine.retain("a")
    report = engine.retain("b")
    assert report.ignored == 1 and report.created == 0
    assert len(store.list_memories(include_inactive=True)) == 1


def test_unrelated_verdict_creates_new_memory(tmp_path):
    judge = ScriptedJudge(lambda new, cands: Judgement(tuple(Verdict(c["id"], "unrelated") for c in cands)))
    store, engine = make(
        tmp_path, payload(fact(JAZZ, entities=MORNING)),
        payload(fact("Peter commutes by metro", entities=("Peter",))), judge=judge,
    )
    engine.retain("a")
    assert engine.retain("b").created == 1
    assert len(store.list_memories(include_inactive=False)) == 2


def test_judge_is_not_called_when_there_is_nothing_to_compare(tmp_path):
    judge = ScriptedJudge(replaces_first())
    _, engine = make(tmp_path, payload(fact(JAZZ, entities=MORNING)), judge=judge)
    engine.retain("a")
    assert judge.calls == []


# --- safety: the judge is untrusted-ish and may be offline -------------------

def test_verdict_for_an_unknown_id_cannot_delete_arbitrary_memories(tmp_path):
    judge = ScriptedJudge(lambda new, cands: Judgement((Verdict("mem_not_a_candidate", "replaces"),)))
    store, engine = make(tmp_path, payload(fact(JAZZ, entities=MORNING)), payload(fact(LOFI, entities=MORNING)), judge=judge)
    engine.retain("a")
    report = engine.retain("b")
    assert report.superseded == 0 and report.created == 1
    assert set(statuses(store).values()) == {"active"}


def test_judge_outage_never_destroys_data(tmp_path):
    def boom(new, cands):
        raise ReconcileError("judge request failed: TimeoutError")

    judge = ScriptedJudge(boom)
    store, engine = make(tmp_path, payload(fact(JAZZ, entities=MORNING)), payload(fact(LOFI, entities=MORNING)), judge=judge)
    engine.retain("a")
    report = engine.retain("b")
    assert report.created == 1
    assert statuses(store) == {JAZZ: "active", LOFI: "active"}


@pytest.mark.parametrize(
    "bad_merge",
    ["", "   ", "x" * 600, "api_key = sk-abcdefghijklmnopqrstuvwxyz123456", "line one\nline two"],
)
def test_unsafe_or_empty_merged_content_falls_back_to_the_plain_new_fact(tmp_path, bad_merge):
    judge = ScriptedJudge(replaces_first(bad_merge))
    store, engine = make(tmp_path, payload(fact(JAZZ, entities=MORNING)), payload(fact(LOFI, entities=MORNING)), judge=judge)
    engine.retain("a")
    engine.retain("b")
    assert [m["content"] for m in store.list_memories(include_inactive=False)] == [LOFI]


def test_multiple_stale_facts_are_all_replaced(tmp_path):
    judge = ScriptedJudge(lambda new, cands: Judgement(tuple(Verdict(c["id"], "replaces") for c in cands)))
    store, engine = make(
        tmp_path,
        payload(fact("Peter listens to jazz in the morning", entities=MORNING), fact("Peter listens to rock in the morning", entities=MORNING)),
        payload(fact(LOFI, entities=MORNING)),
        judge=judge,
    )
    engine.retain("a")
    engine.retain("b")
    assert [m["content"] for m in store.list_memories(include_inactive=False)] == [LOFI]


def test_new_embedding_matches_the_content_that_was_actually_stored(tmp_path):
    emb = HashEmbedder()
    merged = "Peter likes listening to lo-fi in the morning (previously: jazz)"
    judge = ScriptedJudge(replaces_first(merged))
    store, engine = make(
        tmp_path, payload(fact(JAZZ, entities=MORNING)), payload(fact(LOFI, entities=MORNING)), judge=judge, embedder=emb
    )
    engine.retain("a")
    engine.retain("b")
    new = store.list_memories(include_inactive=False)[0]
    stored = store.get_embedding(new["id"])["vector"]
    assert stored == emb.embed([merged])[0]


# --- fallback heuristics when no judge is configured ----------------------------

def test_without_a_judge_similar_facts_about_different_projects_are_not_merged(tmp_path):
    store, engine = make(
        tmp_path,
        payload(fact("Decided to use Postgres for project Alpha", subtype="decision", entities=("Alpha",))),
        payload(fact("Decided to use Postgres for project Beta", subtype="decision", entities=("Beta",))),
        embedder=HashEmbedder(),
    )
    engine.retain("a")
    report = engine.retain("b")
    assert report.superseded == 0 and report.created == 1
    assert len(store.list_memories(include_inactive=False)) == 2


def test_without_a_judge_a_near_identical_setting_for_the_same_entity_is_replaced(tmp_path):
    old = "Peter uses the default code editor in Termux which is Vim"
    new = "Peter uses the default code editor in Termux which is Neovim"
    store, engine = make(
        tmp_path,
        payload(fact(old, subtype="configuration", entities=("Peter", "Termux"))),
        payload(fact(new, subtype="configuration", entities=("Peter", "Termux"))),
        embedder=HashEmbedder(),
    )
    engine.retain("a")
    report = engine.retain("b")
    assert report.superseded == 1
    assert [m["content"] for m in store.list_memories(include_inactive=False)] == [new]


@pytest.mark.parametrize("predicate", ["memory_provider", "lives_in", "current_phone", "default_shell"])
def test_single_valued_relationship_change_replaces_without_a_judge(tmp_path, predicate):
    store, engine = make(
        tmp_path,
        payload(fact("Hermes setting is the old value", subtype="fact", entities=("Hermes",), relationships=[("Hermes", predicate, "OldValue")])),
        payload(fact("Hermes setting is now the new value", subtype="fact", entities=("Hermes",), relationships=[("Hermes", predicate, "NewValue")])),
    )
    engine.retain("a")
    assert engine.retain("b").superseded == 1


def test_failing_embedder_does_not_block_retention(tmp_path):
    store, engine = make(tmp_path, payload(fact(JAZZ, entities=MORNING)), embedder=FailingEmbedder())
    assert engine.retain("a").created == 1


# --- parsing the judge's JSON --------------------------------------------------

def test_parse_judgement_filters_ids_and_relations():
    j = parse_judgement(
        {"verdicts": [
            {"id": "m1", "relation": "replaces"},
            {"id": "ghost", "relation": "replaces"},
            {"id": "m2", "relation": "banana"},
        ], "merged_content": "now X (previously Y)"},
        allowed_ids={"m1", "m2"},
    )
    assert j.verdicts == (Verdict("m1", "replaces"), Verdict("m2", "unrelated"))
    assert j.merged_content == "now X (previously Y)"


def test_parse_judgement_ignores_merged_content_without_a_replacement():
    j = parse_judgement({"verdicts": [{"id": "m1", "relation": "extends"}], "merged_content": "sneaky"}, allowed_ids={"m1"})
    assert j.merged_content is None


@pytest.mark.parametrize("bad", [None, [], "text", {"verdicts": "nope"}])
def test_parse_judgement_rejects_malformed_payloads(bad):
    with pytest.raises(ReconcileError):
        parse_judgement(bad, allowed_ids={"m1"})
