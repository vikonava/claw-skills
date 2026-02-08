# Agent Instructions

- Read `README.md` in the repository root before creating or updating skills.
- Enforce multi-tenant isolation by default: only the requesting identity can view, create, or modify its own schedules, notifications, and configuration. Never allow cross-identity access or modifications unless explicitly documented for a specific skill.
