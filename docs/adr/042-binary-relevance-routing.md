---
title: "ADR-042: Binary relevance routing"
description: "Status: SUPERSEDED-BY-058"
updated: 2026-07-24
---
# ADR-042: Binary relevance routing

Status: SUPERSEDED-BY-058
Date: 2026-06-22

## Context

Original 3-class routing (accept/review/reject) caused ambiguity: "review" items
were unpredictably handled. Evaluation showed P≈0.70 was gold-label noise.

## Decision

Final relevance routing is binary: accept or reject. No "review" class in production
routing. The routing_decision field retains MatchDecision enum for compatibility but
production gate treats review == reject. MatchDecision.REVIEW is only used internally
for JobValidationNode soft-review (flagging for human inspection, not hard reject).

## Consequences

F1=0.80 on re-audited gold dataset. P=1.0 after gold re-audit confirmed P≈0.70 was
label noise, not model error. Recall bottleneck remains the main optimization target.
