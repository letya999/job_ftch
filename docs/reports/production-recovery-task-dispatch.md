---
title: "Recovery task dispatch"
description: "Созданные задачи, модель, startup status и координация."
updated: 2026-09-12
---
# Recovery task dispatch

Все пять задач фактически запущены; active подтверждён через read_thread. Модель gpt-5.6-luna, reasoning xhigh, отдельные project worktrees. Реальные IDs переданы всем workers и integration owner follow-up сообщениями. Client IDs сохранены только для сопоставления startup handles.

| Work | Client startup ID | Thread ID | Status |
|---|---|---|---|
| sequence | client-new-thread:4f875d38-6e7b-42ba-9a93-a029b6ddbc5e | 01a0952b-3137-7130-aae6-e60d6e13193a | Active |
| P1 | client-new-thread:cfe7ec38-7549-41bd-9b51-9715f7ded159 | 01a0952b-3121-71c1-9998-97ce320ca925 | Active |
| P2 | client-new-thread:66fd2f49-5714-4149-9fb8-5dec3ce7e639 | 01a0952b-3138-7093-8f8b-287378feaa74 | Active |
| P3 | client-new-thread:baf2c892-eadc-46c4-8ded-48f356451819 | 01a0952b-3176-7440-87c4-fe82b5805744 | Active; P3 handoff received |
| P4 | client-new-thread:ed5b5095-c688-4433-8297-24dce2820ff4 | 01a0952b-3110-77e1-9d70-e5e033c0cdaf | Active |

## Coordination

Последовательный task выполняет S1–S5; workers P1–P4. Реальный mapping и follow-up сообщения связывают tasks. P3 handoff передан integration owner для review; проверки пока worker-reported, production исправность не подтверждена. Master [execution plan](../plans/production-recovery-execution.md). Production writes/cleanup/deploy запрещены до отдельного подтверждения.
