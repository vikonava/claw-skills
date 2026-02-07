# OpenClaw Skills Collection

This repository is a curated collection of **skills for OpenClaw**.

Each skill should live at the repository root as its own directory, since this repo is dedicated to skills only.

## Repository layout

Use this structure for every skill:

```text
<skill-name>/
  SKILL.md
  references/   # optional
  scripts/      # optional
  assets/       # optional
```

## Add a new skill

1. Create a new root-level directory named after the skill (example: `code-review/`).
2. Add `SKILL.md` with:
   - `name`
   - `description`
   - clear workflow instructions
3. Add optional supporting materials:
   - `references/` for long-form docs
   - `scripts/` for repeatable automation
   - `assets/` for templates/static files
4. Include a short usage example in your pull request.

## Contribution guidelines

- Keep skills focused and composable.
- Keep instructions concrete and action-oriented.
- Prefer reusable scripts/resources over repeated prose.
- Update this README when conventions change.

## Roadmap

- Add starter templates for common OpenClaw skill types.
- Add validation checks for `SKILL.md` quality and structure.
- Add an index of available skills and their use cases.
