from __future__ import annotations

import os

from mixassist.envfile import ROOT_ENV, load_env, parse_env


def test_parse_env_formats():
    text = "\n".join([
        "# comment",
        "",
        "A=1",
        "export B = two",
        'C="quoted # not a comment"',
        "D='single'",
        "E=value # trailing comment",
        "not a line",
        "F=",
    ])
    assert parse_env(text) == {"A": "1", "B": "two", "C": "quoted # not a comment", "D": "single", "E": "value", "F": ""}


def test_load_env_only_sets_missing(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("SP_TEST_NEW=from-file\nSP_TEST_SET=from-file\nSP_TEST_EMPTY=\n")
    monkeypatch.setenv("SP_TEST_SET", "from-shell")
    for name in ("SP_TEST_NEW", "SP_TEST_EMPTY"):
        monkeypatch.delenv(name, raising=False)
    assert load_env(env) == ["SP_TEST_NEW"]
    assert os.environ["SP_TEST_NEW"] == "from-file"
    assert os.environ["SP_TEST_SET"] == "from-shell"
    assert "SP_TEST_EMPTY" not in os.environ
    monkeypatch.delenv("SP_TEST_NEW")


def test_load_env_missing_file(tmp_path):
    assert load_env(tmp_path / "nope.env") == []


def test_default_path_is_repo_root():
    assert ROOT_ENV.name == ".env" and (ROOT_ENV.parent / "pyproject.toml").exists()
