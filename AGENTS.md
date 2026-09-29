

## Source of Truth

The Git repository is the canonical source of truth.

Follow accepted specifications, ADRs, task acceptance criteria, and project
contracts.

Do not silently redefine accepted requirements.

## AI Execution Model

This project uses different AI systems for different responsibilities.

### Project Brain

ChatGPT is used by the project lead for:

- requirements and scope analysis;
- architecture;
- research;
- planning;
- task decomposition review;
- integration reasoning;
- audits and reviews;
- human decision gates.

Implementation agents must treat accepted repository artifacts and explicit
human decisions as authoritative rather than independently redefining them.

### Fast Executor

Antigravity is primarily used for:

- small and well-scoped implementation tasks;
- repository exploration;
- straightforward fixes;
- documentation;
- simple tests;
- low-risk changes;
- Spec Kit workflow operations.

### Heavy Executor

Codex is primarily used for:

- complex implementation;
- difficult debugging;
- cross-layer or cross-file changes;
- architecture-sensitive implementation;
- critical-path work;
- high-impact changes.

The choice of executor does not change project requirements or acceptance
criteria.

## Scope

Work only within the assigned task scope.

Do not perform unrelated refactors unless required for correctness or
explicitly approved.

Cross-cutting changes involving public APIs, schemas, persistent data,
security, major dependencies, or fundamental architecture require a human
decision gate.

## Decisions

Read relevant specifications and ADRs before changing behavior.

If repository artifacts conflict, do not silently choose one interpretation.
Surface the conflict.

## Verification

Before declaring implementation complete:

- run relevant tests;
- run configured lint/type checks;
- run the repository verification command when one exists;
- report exact commands and their results.

Never claim that a check passed unless it actually ran successfully.

Never weaken tests merely to make implementation pass.

## Security

Never commit:

- API keys;
- tokens;
- passwords;
- private keys;
- secrets;
- local `.env` files.

Security-sensitive changes require additional review.

## Git

Do not commit directly to `main` during normal development.

Keep changes focused and reviewable.

Do not overwrite unrelated work.

## Completion Report

Every implementation completion report should include:

- summary of changes;
- files changed;
- checks/tests executed;
- exact results;
- assumptions;
- known limitations;
- unresolved issues.