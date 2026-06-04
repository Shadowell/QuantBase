# Reference: Harness Design for Long-Running Applications

## Source

- Title: `Harness design: Building long-running applications with LLMs`
- URL: <https://www.anthropic.com/engineering/harness-design-long-running-apps>

## Why It Matters To This Repository

QuantBase is meant to stay understandable across long development sessions. The article is useful because it frames software work as a harness problem: stable files, explicit scope, and repeatable verification matter as much as the model prompt.

## Key Ideas Captured Here

### 1. Long tasks need structure

Large implementation passes are easier to review when the repository keeps durable context in files such as `docs/spec.md`, `docs/progress.md`, and page-level docs.

### 2. Planning, generation, and evaluation should not collapse into one fuzzy step

QuantBase does not require a rigid multi-agent process, but it keeps separate places for intent, implementation notes, and verification:

- `docs/spec.md`
- `docs/progress.md`
- `docs/qa/`

### 3. File handoff matters

Long-running work should not depend on one uninterrupted chat session. Important assumptions, boundaries, and verification results should be written into project files when they become part of the project contract.

### 4. Verification needs its own attention

Self-evaluation is unreliable. `scripts/check.sh`, focused tests, and QA notes give maintainers a stronger way to decide whether work is actually ready.

## How This Template Applies The Article

- `AGENTS.md` stores stable project rules.
- `docs/spec.md` stores higher-level intent.
- `docs/progress.md` records current project state.
- `scripts/check.sh` gives one place to start verification.
- `docs/qa/` records evaluator-style findings and verdicts.

## Notes

This file records the source and the applied ideas. It summarizes and adapts the article instead of reproducing the full text.
