from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


class MakeDevError(RuntimeError):
    pass


DEFAULT_CONFIG = '''[github]
ready_label = "Ready？"
review_label = "確認待ち"
failed_label = "GPT確認待ち"
branch_prefix = "make-dev/issue-"

[codex]
instructions = """
既存の設計と規約を守ること。
Issueと無関係な変更を行わないこと。
"""

[checks]
commands = []
'''

MAKE_DEV_COMMAND = 'make-dev run --repo "$(CURDIR)"'


@dataclass(frozen=True)
class Config:
    ready_label: str = "Ready？"
    review_label: str = "確認待ち"
    failed_label: str = "GPT確認待ち"
    branch_prefix: str = "make-dev/issue-"
    test_commands: tuple[str, ...] = ()
    instructions: str = ""


def _make_target_recipes(content: str, name: str) -> list[tuple[str, ...]]:
    """Return recipes for definitions of a simple Make target."""
    lines = content.splitlines()
    recipes: list[tuple[str, ...]] = []
    for index, line in enumerate(lines):
        if line[:1].isspace() or line.lstrip().startswith("#") or ":" not in line:
            continue
        targets, rest = line.split(":", 1)
        # Do not mistake assignments such as ``dev := value`` for targets.
        if targets.rstrip().endswith(("+", "?", ":", "!")) or name not in targets.split():
            continue
        recipe: list[str] = []
        if ";" in rest:
            recipe.append(rest.split(";", 1)[1].strip())
        for following in lines[index + 1:]:
            if following.startswith("\t"):
                recipe.append(following[1:].strip())
            elif not following.strip() or following.lstrip().startswith("#"):
                continue
            else:
                break
        recipes.append(tuple(item for item in recipe if item))
    return recipes


def initialize_project(repo: Path) -> tuple[Path, ...]:
    """Add make-dev's config and Make targets to an existing Git repository."""
    repo = repo.resolve()
    if not repo.is_dir():
        raise MakeDevError(f"対象ディレクトリがありません: {repo}")
    check = subprocess.run(
        ("git", "-C", str(repo), "rev-parse", "--show-toplevel"),
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if check.returncode:
        raise MakeDevError(f"Gitリポジトリではありません: {repo}")

    makefile = repo / "Makefile"
    try:
        content = makefile.read_text(encoding="utf-8") if makefile.exists() else ""
    except OSError as exc:
        raise MakeDevError(f"Makefileを読めません: {exc}") from exc

    missing: list[str] = []
    for target in ("dev", "dev-next"):
        recipes = _make_target_recipes(content, target)
        if not recipes:
            missing.append(target)
            continue
        expected = (MAKE_DEV_COMMAND,)
        normalized = [tuple(command.removeprefix("@").strip() for command in recipe) for recipe in recipes]
        if len(normalized) != 1 or normalized[0] != expected:
            raise MakeDevError(
                f"Makefileの既存ターゲット '{target}' は別内容のため上書きできません"
            )

    changed: list[Path] = []
    config_path = repo / ".make-dev.toml"
    if not config_path.exists():
        try:
            config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
        except OSError as exc:
            raise MakeDevError(f"設定ファイルを書けません: {exc}") from exc
        changed.append(config_path)

    if missing:
        addition = ""
        if content and not content.endswith("\n"):
            addition += "\n"
        if content:
            addition += "\n"
        addition += f".PHONY: {' '.join(missing)}\n"
        for target in missing:
            addition += f"{target}:\n\t@{MAKE_DEV_COMMAND}\n"
        try:
            makefile.write_text(content + addition, encoding="utf-8")
        except OSError as exc:
            # Avoid leaving a newly-created config after a failed Makefile write.
            if config_path in changed:
                config_path.unlink(missing_ok=True)
            raise MakeDevError(f"Makefileを書けません: {exc}") from exc
        changed.append(makefile)
    return tuple(changed)


def load_config(repo: Path) -> Config:
    path = repo / ".make-dev.toml"
    if not path.is_file():
        raise MakeDevError(f"設定ファイルがありません: {path}")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise MakeDevError(f"設定を読めません: {exc}") from exc
    github = data.get("github", {})
    codex = data.get("codex", {})
    checks = data.get("checks", {})
    commands = checks.get("commands", [])
    if not isinstance(commands, list) or not all(isinstance(item, str) and item.strip() for item in commands):
        raise MakeDevError("checks.commands は空でない文字列の配列にしてください")
    return Config(
        ready_label=str(github.get("ready_label", "Ready？")),
        review_label=str(github.get("review_label", "確認待ち")),
        failed_label=str(github.get("failed_label", "GPT確認待ち")),
        branch_prefix=str(github.get("branch_prefix", "make-dev/issue-")),
        test_commands=tuple(commands),
        instructions=str(codex.get("instructions", "")).strip(),
    )


def branch_name(config: Config, issue_number: int) -> str:
    prefix = re.sub(r"[^A-Za-z0-9._/-]+", "-", config.branch_prefix).strip("-/")
    return f"{prefix or 'make-dev/issue'}-{issue_number}"


def redact(text: str, repo: Path, environ: dict[str, str] | None = None) -> str:
    values: set[str] = set()
    for path in repo.glob(".env*"):
        if path.name.endswith((".example", ".sample", ".template")) or not path.is_file():
            continue
        try:
            for line in path.read_text(errors="replace").splitlines():
                match = re.match(r"\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*\s*=\s*(.*)", line)
                if match:
                    value = match.group(1).strip().strip("'\"")
                    if len(value) >= 4:
                        values.add(value)
        except OSError:
            pass
    for name, value in (environ or dict(os.environ)).items():
        if re.search(r"SECRET|TOKEN|PASSWORD|PASSWD|API_?KEY|COOKIE|AUTH", name, re.I) and len(value) >= 8:
            values.add(value)
    for value in sorted(values, key=len, reverse=True):
        text = text.replace(value, "[REDACTED]")
    patterns = (
        r"\b(?:gh[opusr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,})\b",
        r"\bAKIA[A-Z0-9]{16}\b",
        r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b",
    )
    for pattern in patterns:
        text = re.sub(pattern, "[REDACTED]", text)
    text = re.sub(r"(?i)(authorization\s*[:=]\s*)(?:bearer|basic)\s+\S+", r"\1[REDACTED]", text)
    return text.replace("```", "'''")[-12000:]


class Runner:
    def __init__(self, repo: Path, *, dry_run: bool = False) -> None:
        self.repo = repo.resolve()
        self.dry_run = dry_run
        self.log: list[str] = []

    def run(self, args: Sequence[str], *, cwd: Path | None = None, stdin: str | None = None) -> str:
        shown = shlex.join(args)
        print(f"→ {shown}")
        if self.dry_run:
            return ""
        result = subprocess.run(
            args, cwd=cwd or self.repo, input=stdin, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        output = result.stdout or ""
        self.log.append(f"$ {shown}\n{output}")
        if output:
            print(output, end="" if output.endswith("\n") else "\n")
        if result.returncode:
            raise MakeDevError(f"失敗 ({result.returncode}): {shown}")
        return output.strip()

    def gh(self, *args: str, cwd: Path | None = None) -> str:
        return self.run(("gh", *args), cwd=cwd)


def require_clean_repo(runner: Runner) -> None:
    if runner.run(("git", "status", "--porcelain")):
        raise MakeDevError("対象リポジトリに未コミット変更があります")


def oldest_ready_issue(runner: Runner, label: str) -> dict[str, Any] | None:
    raw = runner.gh(
        "issue", "list", "--state", "open", "--label", label,
        "--search", "sort:created-asc", "--limit", "1",
        "--json", "number,title,body,createdAt",
    )
    issues = json.loads(raw or "[]")
    return issues[0] if issues else None


def add_worktree(runner: Runner, repo: Path, worktree: Path, branch: str, default_branch: str) -> None:
    local = subprocess.run(
        ("git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"), cwd=repo
    ).returncode == 0
    remote = subprocess.run(
        ("git", "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{branch}"), cwd=repo
    ).returncode == 0
    if local:
        runner.run(("git", "worktree", "add", str(worktree), branch))
    elif remote:
        runner.run(("git", "worktree", "add", "-b", branch, str(worktree), f"origin/{branch}"))
    else:
        runner.run(("git", "worktree", "add", "-b", branch, str(worktree), f"origin/{default_branch}"))


def ensure_labels(runner: Runner, config: Config) -> None:
    existing = json.loads(runner.gh("label", "list", "--limit", "1000", "--json", "name") or "[]")
    names = {item["name"] for item in existing}
    for name, color, description in (
        (config.ready_label, "EDEDED", "仕様が実装可能な状態"),
        (config.review_label, "FBCA04", "実装完了・人間の確認待ち"),
        (config.failed_label, "D876E3", "失敗内容の確認待ち"),
    ):
        if name not in names:
            runner.gh("label", "create", name, "--color", color, "--description", description)


def make_prompt(issue: dict[str, Any], config: Config) -> str:
    extra = f"\nプロジェクト固有の指示:\n{config.instructions}\n" if config.instructions else ""
    return (
        f"GitHub Issue #{issue['number']} を実装してください。\n\n"
        f"タイトル:\n{issue['title']}\n\nIssue本文:\n{issue.get('body') or ''}\n"
        f"{extra}\nIssue要件を優先し、必要なテストを追加してください。秘密情報や生成物をcommitしないでください。"
    )


def failure_comment(stage: str, error: str, excerpt: str) -> str:
    return (
        "## make_dev failed\n\n"
        f"- 工程: {stage}\n- エラー: {error}\n- Ready？: 維持\n- GPT確認待ち: 付与\n\n"
        f"```text\n{excerpt or '（出力なし）'}\n```\n"
    )


def execute(repo: Path, *, dry_run: bool = False) -> str | None:
    config = load_config(repo)
    runner = Runner(repo, dry_run=dry_run)
    stage = "事前確認"
    issue: dict[str, Any] | None = None
    worktree: Path | None = None
    try:
        missing = [command for command in ("git", "gh", "codex") if shutil.which(command) is None]
        if missing:
            raise MakeDevError(f"必要なコマンドがありません: {', '.join(missing)}")
        runner.gh("auth", "status")
        require_clean_repo(runner)
        ensure_labels(runner, config)
        stage = "Issue取得"
        issue = oldest_ready_issue(runner, config.ready_label)
        if not issue:
            print(f"openの「{config.ready_label}」Issueはありません。")
            return None
        number = int(issue["number"])
        default_branch = runner.gh("repo", "view", "--json", "defaultBranchRef", "--jq", ".defaultBranchRef.name")
        branch = branch_name(config, number)
        runner.run(("git", "fetch", "origin", default_branch))
        existing_pr = runner.gh("pr", "list", "--state", "open", "--head", branch, "--json", "url", "--jq", ".[0].url // empty")
        if existing_pr:
            raise MakeDevError(f"既存のPRがあります: {existing_pr}")
        stage = "分離worktree作成"
        base_dir = repo / ".make-dev" / "worktrees"
        worktree = base_dir / f"issue-{number}"
        if worktree.exists():
            raise MakeDevError(f"既存worktreeがあります: {worktree}")
        add_worktree(runner, repo, worktree, branch, default_branch)
        stage = "Codex実装"
        runner.run(("codex", "exec", "--cd", str(worktree), "--sandbox", "workspace-write", "-"), cwd=worktree, stdin=make_prompt(issue, config))
        stage = "変更確認"
        if not runner.run(("git", "status", "--porcelain"), cwd=worktree):
            raise MakeDevError("Codex実行後の変更がありません")
        stage = "検証"
        for command in config.test_commands:
            runner.run(("sh", "-lc", command), cwd=worktree)
        runner.run(("git", "diff", "--check"), cwd=worktree)
        stage = "commit・push"
        runner.run(("git", "add", "--all"), cwd=worktree)
        runner.run(("git", "commit", "-m", f"Implement #{number}: {issue['title']}"), cwd=worktree)
        sha = runner.run(("git", "rev-parse", "HEAD"), cwd=worktree)
        runner.run(("git", "push", "-u", "origin", branch), cwd=worktree)
        stage = "PR作成"
        checks = "\n".join(f"- `{command}`: success" for command in (*config.test_commands, "git diff --check"))
        body = f"Closes #{number}\n\n## make_dev result\n\n- Codex: success\n{checks}\n- Commit: `{sha}`\n"
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write(body)
            body_path = handle.name
        try:
            pr_url = runner.gh("pr", "create", "--base", default_branch, "--head", branch, "--title", f"#{number} {issue['title']}", "--body-file", body_path, cwd=worktree)
        finally:
            Path(body_path).unlink(missing_ok=True)
        runner.gh("issue", "edit", str(number), "--remove-label", config.ready_label, "--add-label", config.review_label)
        print(f"完了: {pr_url}")
        return pr_url
    except (MakeDevError, json.JSONDecodeError) as exc:
        if issue and not dry_run:
            number = str(issue["number"])
            excerpt = redact("\n".join(runner.log), repo)
            body = failure_comment(stage, str(exc), excerpt)
            try:
                runner.gh("issue", "edit", number, "--add-label", config.failed_label)
                runner.gh("issue", "comment", number, "--body", body)
            except MakeDevError:
                pass
        raise
    finally:
        if worktree and worktree.exists() and not dry_run:
            subprocess.run(("git", "worktree", "remove", "--force", str(worktree)), cwd=repo, check=False)
