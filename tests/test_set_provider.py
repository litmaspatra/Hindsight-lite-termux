import importlib.util
from pathlib import Path

import pytest
import yaml

spec = importlib.util.spec_from_file_location("set_provider", Path(__file__).parent.parent / "scripts" / "set_provider.py")
sp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sp)


def provider(text):
    return (yaml.safe_load(text).get("memory") or {}).get("provider")


def test_replaces_existing_provider_and_keeps_comments_and_other_keys():
    src = "# my hermes config\nmodel: gpt  # keep\nmemory:\n  provider: holographic   # old\n  other: 1\nui:\n  theme: dark\n"
    out = sp.set_provider(src, "hindsight-lite")
    assert provider(out) == "hindsight-lite"
    assert "# my hermes config" in out and "# keep" in out and "other: 1" in out and "theme: dark" in out


def test_adds_provider_inside_an_existing_memory_block():
    out = sp.set_provider("memory:\n  other: 1\nui: x\n", "hindsight-lite")
    assert provider(out) == "hindsight-lite" and yaml.safe_load(out)["memory"]["other"] == 1


def test_appends_a_memory_block_when_missing():
    out = sp.set_provider("model: x\n", "hindsight-lite")
    assert provider(out) == "hindsight-lite" and yaml.safe_load(out)["model"] == "x"


def test_empty_file():
    assert provider(sp.set_provider("", "hindsight-lite")) == "hindsight-lite"


@pytest.mark.parametrize("src", ["memory: {}\n", "memory: null\n", "memory: {provider: old}\n"])
def test_inline_forms_fall_back_to_a_safe_rewrite(src):
    assert provider(sp.set_provider(src, "hindsight-lite")) == "hindsight-lite"


def test_a_commented_out_provider_line_is_not_touched():
    out = sp.set_provider("memory:\n  # provider: old\n", "hindsight-lite")
    assert "# provider: old" in out and provider(out) == "hindsight-lite"
