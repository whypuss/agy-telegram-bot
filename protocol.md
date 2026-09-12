# Token-Efficient Agent Protocol

## Output
- Never print large command output.
- Commands expected to exceed 50 lines MUST redirect to a file.
- Inspect failures with grep, head, tail, or targeted reads.
- Never print full git diff unless explicitly requested.

## State
- Maintain `~/.antigravity/task_state.md`.
- Store discoveries, decisions, blockers and next steps there.
- Do not repeat information already recorded there.

## Editing
- Make surgical edits.
- Never rewrite an entire file when a targeted patch is sufficient.

## Verification
- Run the smallest relevant test first.
- On failure, capture output to a log and inspect only the relevant section.
- Do not rerun unchanged expensive tests.

## Checkpoints
- Checkpoint on events, not on a tool-call count. A complete fix in two calls
  is a checkpoint; ten calls still inside investigation is also a checkpoint.
- Checkpoint boundaries: root cause confirmed, patch applied, targeted test
  passed, evidence confirmed.
- At each checkpoint update task_state.md and report only:
  1. completed
  2. current blocker
  3. next action

## Git
- Check `git status --short`.
- Prefer `git diff --stat` before detailed diff.
- Commit coherent milestones.

## Evidence Discipline
- Never assume a change worked because the edit succeeded.
- Verify only the affected behavior.
- Prefer exit code + concise evidence over full logs.
- Record verified facts in task_state.md.
