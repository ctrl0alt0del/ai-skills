# AI Skills

Reusable skills for understanding application behavior and refining requirements.

## Skills

- [Behavior Graph Builder](skills/behavior-knowledge-graph-builder/SKILL.md):
  investigate application behavior, review a graph collaboratively, and export
  approved graph artifacts as JSON.
- [Requirement Refinement](skills/requirement-refinement/SKILL.md): analyze a
  proposed requirement with two isolated investigators and a fresh evidence judge,
  producing concrete decisions, investigations, and regression checks in Markdown
  and JSON, with a separate review audit.

The behavior graph can be supplied as context to requirement refinement.
The skills use different JSON schemas and have separate validators.

The host's subagent tools run the investigators and judge. The refinement helper
freezes their shared input and validates candidate decisions and report lineage.
When isolated agents are unavailable, the skill records an explicit sequential
fallback; no external model API key or background service is required.

## Development and AI handoff

Read [HANDOFF.md](HANDOFF.md) before continuing development in a new thread or AI
session. It records the original goals, verified research references, design
decisions, implementation boundaries, graph/report contracts, validation status,
known limitations, and suggested next work.

## Installation

Copy either directory from `skills/` into your local Codex skills directory
(`$CODEX_HOME/skills`, normally `~/.codex/skills`). Keep its references, scripts,
and assets together.

The bundled Python tools use the standard library. Behavior Graph Builder and
the refinement review helper require Python 3.10 or newer.

Invoke the installed skills with `$behavior-knowledge-graph-builder` or
`$requirement-refinement`.

## Development checks

Run the refinement review regression tests from the repository root:

```bash
python3 -m unittest discover -s tests -p 'test_refinement_review.py'
```

These test artifact lineage, role declarations, candidate accounting, final report
compatibility, and output preservation. They do not measure reasoning accuracy
or authenticate that a host actually created isolated agent contexts.
