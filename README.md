# make_dev

スマホで仕様を決め、GitHub Issueへ `Ready？` を付けた後の実装作業を開発PCへ任せるための実行エンジンです。最古のReady Issueを1件だけ取得し、Codexで実装、プロジェクト固有の検証、commit、push、PR作成、GitHubへの結果報告まで進めます。

## 全体フロー

```text
スマホのChatGPTで仕様相談
  → GitHub Issue作成・Ready？付与
  → PCで make-dev run
  → 分離worktreeでCodexが実装
  → 設定されたテスト・ビルド
  → branchをpush・PR作成
  → 確認待ち
```

PRの自動マージはしません。最終判断は人間が行います。

## 必要なもの

- Python 3.11以上
- Git
- GitHub CLI（`gh auth login` 済み）
- Codex CLI（ログイン済み）

## インストール

```bash
git clone https://github.com/dawidhjawiodhwaio/make_dev.git
cd make_dev
python3 -m venv .venv
.venv/bin/pip install -e .
```

## 新規プロジェクトへの導入

対象のGitリポジトリへ設定ファイルと `make dev` / `make dev-next` ターゲットを追加します。

```bash
make-dev init --repo /path/to/project
```

既存のMakefileがあれば内容を保持して末尾へターゲットを追加します。再実行しても重複しません。同名ターゲットが別内容で存在する場合は、安全のため上書きせずエラーになります。実行後は作成・変更したファイルが表示されます。

生成された `.make-dev.toml` のチェックコマンドやCodex向け指示は、プロジェクトに合わせて編集してください。

```toml
[github]
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
commands = [
  "uv sync --extra dev",
  "uv run pytest -q",
  "uv run ruff check .",
]
```

技術構成が違うプロジェクトでは `checks.commands` だけを変更します。たとえばNode.jsなら `npm ci`、`npm test`、`npm run build` を指定できます。

## 実行

```bash
make-dev check --repo /path/to/project
make-dev run --repo /path/to/project
make dev
make dev-next
```

対象リポジトリがdirtyな場合は、既存作業を守るため何も変更せず終了します。実装は `.make-dev/worktrees/issue-N` の分離worktreeで行い、終了時にworktreeだけを片付けます。branchとcommitは残ります。

## 状態遷移

- 成功: `Ready？` を外す → `確認待ち` を付ける → PR本文へ検証結果を記録
- 失敗: `Ready？` を維持 → `GPT確認待ち` を付ける → Issueへ秘密情報を伏せたエラーを記録
- Ready Issueなし: 変更せず正常終了
- 同じbranchのopen PRあり: 二重実行を避けて停止

## 既存プロジェクトからの移行

各リポジトリの `scripts/dev-next.sh` に埋め込まれていたテストコマンドとCodex向け指示を `.make-dev.toml` へ移します。Makefileから使いたい場合は次の薄い入口だけを置きます。

この設定は `make-dev init --repo /path/to/project` で自動追加できます。

## 初期版の対象外

常駐監視、複数Issueの並列処理、PRの自動マージ、ChatGPT側のIssue作成は含みません。まず手動で `make-dev run` を実行する安全なMVPとして運用し、安定後にPC常駐化を追加します。
