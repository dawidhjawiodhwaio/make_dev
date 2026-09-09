from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from make_dev.cli import main
from make_dev.core import MAKE_DEV_COMMAND, MakeDevError, initialize_project, load_config


def git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(("git", "init", "-q", str(path)), check=True)
    return path


def test_init_creates_config_and_makefile(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "project")

    changed = initialize_project(repo)

    assert changed == (repo / ".make-dev.toml", repo / "Makefile")
    assert load_config(repo).test_commands == ()
    content = (repo / "Makefile").read_text()
    assert content.count("dev:") == 1
    assert content.count("dev-next:") == 1
    assert content.count(MAKE_DEV_COMMAND) == 2
    outputs = []
    for target in ("dev", "dev-next"):
        result = subprocess.run(
            ("make", "-n", target), cwd=repo, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        )
        outputs.append(result.stdout.strip())
    assert outputs == [f'make-dev run --repo "{repo}"'] * 2


def test_init_preserves_existing_makefile(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "project")
    original = "test:\n\t@echo testing\n"
    (repo / "Makefile").write_text(original)

    initialize_project(repo)

    assert (repo / "Makefile").read_text().startswith(original)


def test_init_is_idempotent(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "project")
    initialize_project(repo)
    before = {name: (repo / name).read_text() for name in (".make-dev.toml", "Makefile")}

    assert initialize_project(repo) == ()
    assert {name: (repo / name).read_text() for name in before} == before


def test_init_refuses_conflicting_target_without_changes(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "project")
    original = "dev:\n\t@echo custom\n"
    (repo / "Makefile").write_text(original)

    with pytest.raises(MakeDevError, match="dev.*別内容"):
        initialize_project(repo)

    assert (repo / "Makefile").read_text() == original
    assert not (repo / ".make-dev.toml").exists()


def test_init_rejects_non_git_directory(tmp_path: Path) -> None:
    with pytest.raises(MakeDevError, match="Gitリポジトリではありません"):
        initialize_project(tmp_path)


def test_init_cli_reports_changes_and_noop(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = git_repo(tmp_path / "project")

    assert main(["init", "--repo", str(repo)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "作成・変更: .make-dev.toml",
        "作成・変更: Makefile",
    ]
    assert main(["init", "--repo", str(repo)]) == 0
    assert capsys.readouterr().out.strip() == "設定済み: 変更はありません"
