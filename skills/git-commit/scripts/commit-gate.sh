#!/bin/bash
# Advisory Codex and Claude Code gate: block the first matching git commit
# attempt and allow one retry. This cannot prove that the Skill was invoked.

input=$(cat)

command=$(echo "$input" | jq -r '.tool_input.command // .tool_input.shell_command // .tool_input.cmd // ""' 2>/dev/null)
session_id=$(echo "$input" | jq -r '.session_id // "unknown-session"' 2>/dev/null)

# Tool names and shell-input fields vary by Agent. An absent command means the
# event is not a supported shell invocation.
if [ -z "$command" ]; then
  echo '{"decision":"allow"}'
  exit 0
fi

# Match the command forms supported by this gate.
if ! echo "$command" | grep -qE '^git commit([[:space:]]|$)|^git -C .+ commit([[:space:]]|$)'; then
  echo '{"decision":"allow"}'
  exit 0
fi

marker_dir="${COMMIT_GATE_STATE_DIR:-/dev/shm/git-commit-gate}"
session_key=$(printf '%s' "$session_id" | tr -c 'A-Za-z0-9._-' '_')
marker="$marker_dir/$(id -u)-$session_key-pass"

mkdir -p "$marker_dir"

if [ -f "$marker" ]; then
  rm -f "$marker"
  echo '{"decision":"allow"}'
else
  touch "$marker"
  echo '{"decision":"block","reason":"Must invoke git-commit skill first! You called git commit before using the git-commit skill. Invoke Skill(\"git-commit\") now, follow its process to craft the commit message, then retry. This block has issued a one-time pass — the next git commit attempt will succeed."}'
fi
