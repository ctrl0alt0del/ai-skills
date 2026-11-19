# AI Skills

Reusable skills for understanding application behavior and refining requirements.

## Skills

- [Behavior Graph Builder](skills/behavior-knowledge-graph-builder/SKILL.md):
  investigate application behavior, review a graph collaboratively, and export
  approved graph artifacts as JSON.
- [Requirement Refinement](skills/requirement-refinement/SKILL.md): analyze a
  proposed requirement against available evidence and produce concrete decisions,
  investigations, and regression checks in Markdown and JSON.

The behavior graph can be supplied as context to requirement refinement.
The skills use different JSON schemas and have separate validators.

## Development and AI handoff

Read [HANDOFF.md](HANDOFF.md) before continuing development in a new thread or AI
session. It records the original goals, verified research references, design
decisions, implementation boundaries, graph/report contracts, validation status,
known limitations, and suggested next work.

## Installation

Copy either directory from `skills/` into your local Codex skills directory
(`$CODEX_HOME/skills`, normally `~/.codex/skills`). Keep its references, scripts,
and assets together.

The bundled Python tools use the standard library. Behavior Graph Builder
requires Python 3.10 or newer.

Invoke the installed skills with `$behavior-knowledge-graph-builder` or
`$requirement-refinement`.
