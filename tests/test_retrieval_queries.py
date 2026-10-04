"""Bug 1: natural-language queries must reach lexical / entity / graph arms."""
import pytest

from hindsight_lite import MemoryStore, RetrievalEngine


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(tmp_path / "m.db")
    mid = s.create_memory(
        "Peter prefers dark mode in Obsidian and uses Termux on his phone",
        memory_type="world", subtype="preference",
    )
    ent = s.find_or_create_entity("Obsidian", entity_type="app")
    s.link_memory_entity(mid, ent)
    other = s.find_or_create_entity("Termux", entity_type="app")
    s.create_relationship(ent, "synced_from", other)
    s.link_memory_entity(mid, other)
    return s


def test_lexical_finds_memory_from_natural_sentence(store):
    hits = RetrievalEngine(store).lexical("hey can you remind me what theme I like in Obsidian?")
    assert len(hits) == 1


def test_lexical_finds_memory_from_hinglish_sentence(store):
    hits = RetrievalEngine(store).lexical("mujhe obsidian mein dark mode pasand hai kya")
    assert len(hits) == 1


def test_lexical_tolerates_plural_and_suffix_variants(store):
    assert len(RetrievalEngine(store).lexical("which phones use termux")) == 1


def test_lexical_only_stopwords_returns_nothing_without_error(store):
    assert RetrievalEngine(store).lexical("what is the of and") == []


def test_entity_arm_matches_entity_named_inside_sentence(store):
    assert len(RetrievalEngine(store).entity("what theme do I like in obsidian")) == 1


def test_graph_arm_expands_from_entity_named_inside_sentence(store):
    assert len(RetrievalEngine(store).graph("what theme do I like in obsidian")) == 1


def test_entity_arm_does_not_match_substrings_of_other_words(store):
    # "term" must not match the entity "Termux"
    assert RetrievalEngine(store).entity("in the long term plan") == []
