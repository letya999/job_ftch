---
title: "LexicalEvidenceNode"
description: "Non-blocking exact phrase evidence по profile phrases и anti-preferences."
updated: 2026-07-27
---
# LexicalEvidenceNode

`LexicalEvidenceNode` фиксирует exact phrase matches между вакансией и
профилем, не превращая keyword overlap в gate.

## Вход и выход

**Вход:** `JobRecord`.

**Выход:** `JobRecord` с lexical metadata.

## Параметры

На вход constructor принимает один `SearchProfile` или объект с `.profiles`
например `ProfileCatalog`.

## Логика

Positive phrases собираются из target roles, target domains, project types,
required skills и preferred skills.

Negative phrases собираются из anti preferences и blocked domains.

Текст для поиска строится из title, description, role_family и role_track.
Узел ищет exact casefold substring matches.

## Что пишет

`lexical_positive_matches`, `lexical_negative_matches`,
`lexical_profile_matches`, `lexical_score`.

## Границы

Positive keyword match не должен auto-accept вакансию. Negative matches
позже превращаются в typed evidence и могут создать conflict/review.
