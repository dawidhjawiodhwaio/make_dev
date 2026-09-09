from pathlib import Path

import pytest

from make_dev.core import Config, MakeDevError, branch_name, load_config, oldest_ready_issue, redact


def test_load_config(tmp_path: Path) -> None:
    (tmp_path / ".make-dev.toml").write_text(
        '[github]\nready_label="go"\nbranch_prefix="bot/task"\n'
        '[codex]\ninstructions="keep it small"\n[checks]\ncommands=["make test"]\n'
    )
    config = load_config(tmp_path)
    assert config.ready_label == "go"
    assert config.test_commands == ("make test",)
    assert config.instructions == "keep it small"


def test_missing_config(tmp_path: Path) -> None:
    with pytest.raises(MakeDevError):
        load_config(tmp_path)


def test_branch_name_is_stable() -> None:
    assert branch_name(Config(branch_prefix="make-dev/issue-"), 42) == "make-dev/issue-42"


def test_redact_env_and_tokens(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("API_KEY=local-secret-value\n")
    text = "local-secret-value ghp_abcdefghijklmnopqrstuvwxyz123456"
    result = redact(text, tmp_path, {"ACCESS_TOKEN": "environment-secret"})
    assert "local-secret-value" not in result
    assert "ghp_" not in result
    assert result.count("[REDACTED]") == 2


def test_oldest_ready_issue_selects_first_result() -> None:
    class FakeRunner:
        def gh(self, *args: str) -> str:
            assert "sort:created-asc" in args
            return '[{"number": 7, "title": "oldest"}]'

    issue = oldest_ready_issue(FakeRunner(), "Ready？")  # type: ignore[arg-type]
    assert issue == {"number": 7, "title": "oldest"}
