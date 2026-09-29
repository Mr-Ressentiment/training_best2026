# Collaborative Hackathon Constitution

## Core Principles

### I. Canonical Git Repository
The Git repository is the single canonical source of truth for all project specifications, architecture designs, source code, tests, documentation, and configuration.
- Anything existing outside the repository (chat threads, whiteboard notes, local scratchpads, ephemeral agent context) is non-binding until committed.
- All team members and AI assistants MUST synchronize against repository state before planning or implementing changes.

### II. Spec-Driven Development
Requirements and expected behavior MUST be explicitly defined and accepted before implementation begins.
- Implementation work MUST remain strictly traceable to accepted specifications.
- Specifications (`spec.md`), implementation plans (`plan.md`), and execution tasks (`tasks.md`) MUST remain mutually consistent at all times.
- If requirements evolve or unexpected complexity emerges during implementation, specifications and plans MUST be updated and approved before or alongside code modifications.

### III. Mandatory Human Decision Gates
Explicit human review and approval are MANDATORY before merging changes affecting any of the following decision gates:
- Public API or shared contract/schema changes.
- Persistent data model, database schema, or storage layout changes.
- Security-sensitive architecture, authentication, authorization, or trust boundaries.
- Introduction of new major external libraries, frameworks, or cloud dependencies.
- Fundamental architectural or infrastructural restructuring.
- Changes to accepted product semantics, core user experience, or business logic.
- Changes to accepted MVP scope or hackathon deliverable commitments.

### IV. Architectural Decision Records (ADRs)
Important architectural and product decisions MUST be recorded as Architectural Decision Records (ADRs) in the repository.
- Each ADR must document context, considered alternatives, chosen solution, trade-offs, and consequences.
- ADRs must be committed prior to or concurrently with the implementing changes.

### V. Vertical Slice Simplicity
Teams MUST prioritize a simple, reliable, working end-to-end vertical slice over complex, multi-tiered, or speculative architecture.
- Under hackathon time constraints, a functional and verifiable core loop delivers higher value than theoretical extensibility, unneeded abstractions, or premature optimization.
- Architecture must be justified by immediate MVP demo needs rather than hypothetical future scale.

### VI. Test Integrity (Non-Negotiable)
Tests MUST NOT be deleted, skipped, relaxed, or weakened merely to make an implementation pass.
- When an implementation fails valid tests, the implementation code MUST be fixed.
- Test suites may be updated only when accepted specifications or interface contracts explicitly change through authorized human decision gates.

### VII. Verifiable Task Acceptance Criteria
Every implementation task MUST define clear, objective, and verifiable acceptance criteria before execution begins.
- A task is complete only when all criteria can be verified through automated tests, reproducible script commands, or concrete manual inspection steps.

### VIII. Contract and Architecture Preservation
Existing accepted contracts, interfaces, and architecture MUST be respected unless an explicit, accepted decision modifies them.
- New features and bug fixes MUST NOT introduce regressions, break sibling workflows, or arbitrarily alter established module boundaries.

### IX. Security by Default & Least Privilege
Security standards MUST be maintained without exception during hackathon development:
- Secrets, credentials, private tokens, API keys, and sensitive personal data MUST NEVER be committed to the repository; local environment variables and ignore rules must be enforced.
- All untrusted input (user submissions, webhook payloads, query parameters) MUST be strictly validated and sanitized.
- Apply least privilege to service accounts, database users, and third-party API scopes.
- Security-sensitive changes require mandatory secondary human review before merge.

### X. Integration Quality & Incremental Delivery
Code integration MUST remain rapid and safe across concurrent workstreams:
- Work MUST be partitioned into small, independently reviewable, and mergeable increments.
- Independent workstreams MUST minimize coupling and communicate through defined contracts.
- Changes MUST pass automated repository verification (linting, type checking, build, and tests) before merging into the main branch.

### XI. AI-Assisted Engineering Boundaries
AI assistants and autonomous agents are collaborative engineering tools operating within defined bounds:
- AI systems MAY propose implementations, architectural alternatives, refactoring plans, and test suites.
- AI systems MUST NOT silently redefine, discard, or assume changes to accepted requirements or specifications.
- All material assumptions made by AI agents MUST be stated explicitly for human evaluation.
- Humans retain ultimate authority and responsibility over product direction, architectural trade-offs, and decision gates.

### XII. Hackathon Optimization & Demo Reliability
Development priorities MUST optimize directly for hackathon success:
- Prioritize working end-to-end functionality, high demo reliability, user-perceived value, judging criteria alignment, and critical-path stability over speculative polish, edge-case rabbit holes, or unnecessary redesigns.
- Features that cannot be reliably demonstrated within the hackathon judging timeframe MUST be descoped in favor of making core functionality rock-solid.

## Operational Constraints

- **MVP Scope Discipline**: Core demo capabilities take precedence. Non-critical secondary features must be treated as stretch goals and isolated so they cannot destabilize the primary demo flow.
- **Dependency & Service Management**: Favor dependencies that are well-documented, zero-configuration or minimal-configuration, and dependable offline or in unstable network conditions.
- **Environment Parity**: The repository MUST maintain reproducible setup documentation and environment variable templates (`.env.example`) so any collaborator can spin up the environment quickly.

## Development Workflow & Verification Gates

- **Branch & Trunk Hygiene**: Feature work proceeds in short-lived branches. The `main` branch MUST remain deployable and demo-ready at all times.
- **Pre-Merge Verification Gate**: Before merging to `main`, every increment must satisfy:
  1. Automated test suite and build verification pass.
  2. Defined task acceptance criteria are demonstrated.
  3. No secrets or untracked sensitive assets are committed.
  4. Human gate sign-off is documented if any criteria under Principle III are touched.
- **Decision Tracking**: When a gate decision is approved, the corresponding ADR or specification update must be merged into the repository.

## Governance

- **Supremacy**: This constitution is the governing policy for this repository. It supersedes informal conversations, team chat messages, and autonomous agent defaults.
- **Amendment Procedure**: Any team collaborator may propose an amendment. Amendments require team discussion, consensus approval, and a documented version update to this file.
- **Versioning Policy**: Semantic versioning (`MAJOR.MINOR.PATCH`) applies to this constitution:
  - `MAJOR`: Removals, redefinitions, or incompatible changes to core principles or governance authority.
  - `MINOR`: Addition of new principles, operational constraints, or materially expanded workflow guidance.
  - `PATCH`: Wording clarifications, grammatical fixes, formatting improvements, or non-semantic refinements.
- **Compliance & Review**: All pull requests, feature specifications, and implementation plans must be reviewed for compliance with these principles.

**Version**: 1.0.0 | **Ratified**: 2026-09-29 | **Last Amended**: 2026-09-29
