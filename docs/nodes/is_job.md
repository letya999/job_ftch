---
title: "Graph node: is_job"
description: "Graph id для prototype IsJobNode."
updated: 2026-07-27
---
# Graph node: `is_job`

`is_job` — graph id для prototype `IsJobNode`.

Контракт: `JobRecord -> JobRecord`; результат пишется в
`metadata.is_job_prototype`.

Статус: diagnostic/prototype. Production jobness должен идти через typed
`IS_JOB` evidence.

См. [IsJobNode](is_job_classifier.md).
