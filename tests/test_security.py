import pytest

from hindsight_lite.safety import looks_sensitive


@pytest.mark.parametrize("secret", [
    "password = hunter2hunter2",
    "my api_key: abcdef123456",
    "sk-abcdefghijklmnopqrstuvwxyz123456",
    "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    "github_pat_11ABCDEFG0abcdefghijklmnop_qrstuvwxyz",
    "AKIAIOSFODNN7EXAMPLE",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r",
    "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789",
    "xoxb-1234567890-abcdefghij",
    "-----BEGIN OPENSSH PRIVATE KEY-----",
])
def test_secrets_are_detected(secret):
    assert looks_sensitive(secret)


@pytest.mark.parametrize("fine", [
    "Peter likes jazz in the morning",
    "Decided to use Postgres for project Alpha",
    "The password reset flow needs a redesign",
])
def test_ordinary_facts_pass(fine):
    assert not looks_sensitive(fine)


def test_remember_direct_refuses_secrets(tmp_path):
    from hindsight_lite import MemoryStore, RetentionEngine
    store = MemoryStore(tmp_path / "m.db")
    report = RetentionEngine(store, None).remember_direct("my token is ghp_abcdefghijklmnopqrstuvwxyz0123456789")
    assert report.ignored == 1 and store.list_memories(include_inactive=True) == []
