# Conventions

- Docs-first workflow: read `docs/vision.md`, `docs/architecture.md`, `docs/tech_stack.md`, `docs/rules.md` before substantial implementation.
- Prefer simplest solution; avoid speculative abstractions and parallel systems.
- TDD intent from project docs: write expected behavior first, especially for nodes and domain rules.
- Keep changes surgical; do not refactor unrelated areas.
- Code rules:
  full typing expected.
  prefer stdlib before new dependency.
  file size target ~150 lines, function size target ~20 lines.
  use logging, not `print()`, in real implementation code.
- Layering rules:
  no I/O in `domain/`.
  no business logic in infrastructure adapters.
  add new source/node/sink/store/llm backend in the dedicated directory only.
- Commit/PR vocabulary is restricted to `feat`, `fix`, `chore`, `docs`, `refactor`.
- Project docs are bilingual in places; code and stable identifiers stay English/ASCII.
