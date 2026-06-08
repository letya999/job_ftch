# Plan: Roadmap cleanup — remove unrequested Phase 28 and leftover stub

Target file: `docs/roadmap.md` ONLY. No code changes. Documentation edit only.

## Context
A previous edit (a) left an orphan stub heading and (b) added an unrequested Phase 28.
Both must be removed so the roadmap ends cleanly at Phase 27.

## FIX A — Remove orphan stub heading AND close the numbering gap
- Delete the line `## Phase 12. Persistent store (Moved to Phase 16)` and the adjacent
  blank line it introduced (around line 268).
- Removing it leaves a gap (Phase 11 → Phase 13). CLOSE THE GAP by renumbering every
  phase heading AFTER Phase 11 to be sequential, preserving their CURRENT ORDER.
  Concretely, shift each heading down by one so the sequence becomes contiguous:
  - `Phase 13. Configurable filter profiles`  -> `Phase 12.`
  - `Phase 14. Domain model hardening`         -> `Phase 13.`
  - `Phase 15. Cross-source job aggregation`   -> `Phase 14.`
  - `Phase 16. Persistent store`               -> `Phase 15.`
  - `Phase 17. Fulltext and semantic search`   -> `Phase 16.`
  - `Phase 18. Scheduler and daemon mode`      -> `Phase 17.`
  - `Phase 19. Source configuration system v2` -> `Phase 18.`
  - `Phase 20. Official API sources`           -> `Phase 19.`
  - `Phase 21. Browser and hard scraper`       -> `Phase 20.`
  - `Phase 22. Realtime and push ingestion`    -> `Phase 21.`
  - `Phase 23. Library packaging and adapters` -> `Phase 22.`
  - `Phase 24. Multi-tenant and multi-instance`-> `Phase 23.`
  - `Phase 25. FastMCP server`                 -> `Phase 24.`
  - `Phase 26. Telegram bot + FastAPI bridge`  -> `Phase 25.`
  - `Phase 27. Observability, lineage, watermark` -> `Phase 26.`
- Phases 0 through 11 (including 4.5) are UNCHANGED.
- Keep ALL RM-XXX task IDs exactly as they are. Only `## Phase N.` heading numbers change.
- Update EVERY cross-reference in task bodies that names a phase number to the new number
  (search the file for "Phase 12".."Phase 27" mentions and remap each per the table above).

## FIX B — Remove the entire unrequested Phase 28
- Delete the whole `## Phase 28. Event broadcasting and notification sinks` section and
  ALL its tasks (RM-145, RM-146, and every other RM-### under Phase 28) — from the
  `## Phase 28.` heading down to just before the next top-level section
  (`## Parallel work streams`).
- The roadmap must end its phase list at `## Phase 27. Observability, lineage, and unified
  watermark` (RM-141..RM-144).

## FIX C — Clean up references to the removed content
- In `## Parallel work streams`: remove any stream bullet or line that references Phase 28
  or notification/broadcasting (RM-145+). Leave the other streams intact.
- In `## Milestone boundaries`: remove any milestone line for Phase 28 (e.g. an `M28 -
  Event broadcasting` entry). The milestone list must end at the Phase 27 milestone.
- Search the whole file for any remaining mention of "Phase 28", "notification",
  "broadcast", "RM-145", "RM-146" and remove those references.

## Validation after edits
- Phase headings run CONTIGUOUSLY: 0,1,2,3,4,4.5,5,6,7,8,9,10,11,12,13,...,26 with
  NO gaps, NO Phase 27 (it became Phase 26), NO Phase 28, NO orphan/empty headings.
- Persistent store is now Phase 15; Search is Phase 16; Observability is Phase 26.
- All RM-XXX IDs still present and unchanged (only phase heading numbers shifted).
- Every "Phase N" cross-reference in task bodies points to the correct new number.
- Milestone boundaries list remapped to the new contiguous numbering and ends at the
  Observability milestone (Phase 26). No M27/M28.
- DONE markers preserved on phases 0-10 and 4.5.
- File is valid Markdown; no dangling references to Phase 28 / RM-145+ / notification /
  broadcast. All other content unchanged.
