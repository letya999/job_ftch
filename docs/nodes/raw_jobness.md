---
title: "RawJobnessEvidenceNode"
description: "Graph id `raw_jobness`: pre-extraction IS_JOB evidence для RawItem."
updated: 2026-07-27
---
# RawJobnessEvidenceNode

`raw_jobness` — graph id для `RawJobnessEvidenceNode`.

Контракт: `RawItem -> RawItem`. Узел читает post type distribution из metadata,
пишет diagnostic `jobness_diagnostic` и добавляет typed `IS_JOB` evidence atom.

Он не дропает item и не решает profile relevance.

См. [Jobness nodes](jobness.md).
