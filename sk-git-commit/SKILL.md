---
name: git-commit
description: >
  Use this skill whenever you are about to run git commit, create a commit message, or the user asks to commit changes (e.g., "commit", "提交", "git commit", "帮我提交"). This skill enforces the project's commit message convention. Invoke it BEFORE writing any commit message or running any git commit command — do not proceed with committing until you have followed this skill.
---

# Git Commit Convention

## Commit Message Format

```
[param1][param2][param3] <Brief English description>

[what] 中文简述对修改项描述，内容过多需要分点叙述
[why] 中文简述修改原因，没有则写null
[how] 中文简述修改的方法或思路简要描述，没有则写null
```

The first line is the subject. Leave **one blank line** between the subject and the body. The body always has three fields: `[what]`, `[why]`, `[how]`.

---

## param1 — Change type (required)

| Value | When to use |
|-------|-------------|
| `bugfix` | Fixing a bug or completing an incomplete fix |
| `feature` | New functionality or new module |
| `merge` | Merging from a long-lived branch (param3 format: `[<branch>#<commit>]`) |
| `config` | Config files, documentation, or comments only |
| `revert` | Reverting a previous commit (param3 format: `[<commit>]`) |

## param2 — Scope (required)

| Value | When to use |
|-------|-------------|
| `common` | Shared files across multiple sub-projects |
| `<sub-repo name>` | Changes inside a sub-repository, such as mcu/fpga |
| `<project name>` | Changes inside a specific project/module |

## param3 — Modifier (required)

| Value | When to use |
|-------|-------------|
| `<module name>` | The specific module being modified (e.g., `chat`, `ingest`, `search`) |

---

## Process

1. **Read current repository rules** — inspect the live `AGENTS.md`, contributing guidance, task or issue requirements, and relevant recent history before deciding scope or wording. Repository-specific rules take precedence over this general workflow. In particular, use `merge` only when the current repository permits merge commits.

2. **Inspect both index and working tree** — run `git status --short`, `git diff --cached --name-status`, `git diff --cached --check`, `git diff --cached`, and `git diff` as applicable. Inspect relevant untracked files separately. Do not treat a combined `git diff HEAD` as proof of what will be committed.

3. **Select and stage the narrowest related scope**:
   - An explicit user request to commit authorizes staging only the clearly related paths and creating that commit.
   - Use path-specific staging such as `git add -- <path>...`; never use `git add .` or `git add -A`.
   - Preserve unrelated working-tree changes by leaving them unstaged.
   - If unrelated changes are already staged, do not commit them or alter the user's index silently. Stop and ask how to handle that staged scope.
   - After staging, rerun the cached name-status, whitespace check, and diff. The displayed scope and drafted message must describe exactly the staged content.

4. **Choose all three required params**:
   - Pick `param1` based on the nature of the change.
   - Pick `param2` based on the scope (use the project name from the working directory or the sub-repo if applicable).
   - Always pick `param3` for the specific module or topic. For `merge` and `revert`, use their special `param3` formats instead.

5. **Draft and display the commit message** — write it from the verified staged changes and show the complete message together with the staged path list. An explicit ordinary commit request normally needs no repeated confirmation. If the current repository or caller workflow requires confirmation, follow that stricter rule before committing.

6. **Execute the commit** using a quoted HEREDOC to preserve formatting:

```bash
git commit -m "$(cat <<'EOF'
[param1][param2][param3] Brief English description

[what] 中文描述
[why] 中文描述或null
[how] 中文描述或null
EOF
)"
```

7. **Handle failure deliberately** — preserve and report the exact Git or hook error. Before any retry, verify that the staged scope is unchanged. If a commit gate says to invoke or reread this skill, reread the current skill and retry the identical scoped commit at most once; if it blocks again or the scope changed, stop rather than bypassing the hook or broadening the commit.

8. **Prove completion** — after success, report the commit hash and subject, inspect the committed path list, then run `git status --short` and `git diff --cached --name-status`. Distinguish the created local commit from any push, tag, merge, build, flash, or hardware result; none of those is implied by commit success.

## Optional commit gate

The bundled gate supports Codex and Claude Code, but it is not part of ordinary commit execution. Read [references/hook-setup.md](references/hook-setup.md) only when installing or diagnosing the hook. Installing the Skill and registering the hook are separate operations; do not perform either one or edit Agent configuration without an explicit request. For the supported Codex integration, use `python3 tools/skill_manager.py hook install git-commit --agent codex` after Stable installation; use `hook status` or `hook uninstall` for inspection and removal.

---

## Examples

**Example 1 — new feature targeting the chat module:**
```
[feature][knowledgeAI][chat] add streaming response support

[what] 在聊天接口中增加流式输出支持
[why] 提升用户体验，减少等待时间
[how] 使用 SSE 事件流替换原有的一次性响应
```

**Example 2 — bug fix prior work:**

```
[bugfix][knowledgeAI][ingest] fix negative timer guard in folder ingest

[what] 修复文件夹摄取时计时器出现负数的问题
[why] onUnmounted 未清理导致组件销毁后仍在更新 nowMs
[how] 添加 onUnmounted 清理逻辑，并在摄取开始时重置 nowMs
```

**Example 3 — config/doc change:**
```
[config][knowledgeAI][doc] update AGENTS.md with git commit convention

[what] 在 AGENTS.md 中补充 git commit 格式规范
[why] null
[how] null
```

**Example 4 — merge from dev branch:**
```
[merge][common][dev#a1b2c3d] use Windows tools when release version

[what] 1.当发布版本时使用 Windows 工具
       2.Windows 工具与 Linux 工具不兼容
[why] 使得在 Linux 和 Windows 下调用不同的工具
[how] 判断当前操作系统类型
```
