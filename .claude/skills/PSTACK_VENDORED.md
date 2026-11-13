# Vendored: pstack (Cursor plugin) for Claude Code

- Source: https://github.com/cursor/plugins/tree/main/pstack @ `ec569379616a4826b98f0f0a205dd3411b573611`
  (plugin version 0.15.36), MIT, Copyright (c) 2026 Lauren Tan. See `PSTACK_LICENSE`, `PSTACK_README.md`.
- Copied: `skills/` → `.claude/skills/`, `agents/` → `.claude/agents/` (what the plugin manifest exposes).
  Not copied: `assets/`, `docs/`, `automations/`.
- Only edit: `.cursor/skills/` → `.claude/skills/` in 7 files (automate-me, create-verification-skill,
  maintain-verification-skill, reflect + 3 reflect references), so generated project skills land where
  Claude Code loads them. Everything else is byte-identical to upstream.

## Known Cursor-only assumptions (left as upstream wrote them)

- `~/.cursor/rules/pstack-models.mdc` (written by `/setup-pstack`; read by `swarm`, `arena`, `poteto-help`):
  no Claude Code equivalent. Defaults name Cursor models (e.g. `grok-4.7-xhigh-fast`); in Claude Code pick
  from the Agent tool's own models instead.
- `~/.cursor/projects/<slug>/agent-transcripts/` (`recall`, `reflect`, `show-me-your-work`,
  `poteto-mode/scripts/worktree-audit.sh`): Claude Code transcripts live under `~/.claude/projects/`.
- Agent frontmatter `is_background` is a Cursor key and is ignored here.
- `poteto-mode`/`statechart` scripts need Bun/Node; not installed by this repo.
- 57 of 58 skills set `disable-model-invocation: true`: invoke them explicitly, e.g. `/poteto-mode`.

To update: re-copy from a newer upstream commit, re-apply the one path rewrite, update this file.
