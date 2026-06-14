# Technical Debt & Backlog

This document is the single registry for architectural improvements, future features, and known technical debt for `job_ftch`. Items are organized by area with priority tags.

## 1. Core Architectural & Lifecycle

### 1.1 Unified RuntimeAdapter Protocol (Item 29) [Priority: v1.x]
**Problem:** Runtime adapters (FastAPI, MCP, Telegram Bot) lack a formal lifecycle contract.
**Backlog:**
- Define `RuntimeAdapter` Protocol: `startup(builder)`, `stop(timeout)`, `health()`.
- Implement `AdapterHost` to coordinate multiple adapters in a single process.
- Refactor existing adapters to implement this protocol.

### 1.2 PydanticAI Migration [Priority: v1.x] (DONE: Instructor)
**Status:** `ExtractionNode` already uses `instructor` for robust schema-based extraction.
**Backlog:**
- Evaluate migration to `PydanticAI` for more complex agentic extraction flows and better dependency injection.

### 1.3 Deep Data Lineage Hardening [Priority: v1.x]
**Status:** `JobLineage` is currently built on-demand.
**Backlog:**
- Implement a dedicated `LineageStore` to persist stage-by-stage transformations.
- Store "snapshots" of raw content and LLM prompt/response pairs for debugging.

## 2. Ingestion & Sources

### 2.1 Realtime Push Ingestion [Priority: post-MVP]
**Backlog:**
- Implement `WebhookSource` and `WebSocketSource` for low-latency updates.
- Support `EventListenerMode` in the scheduler for persistent connections.

### 2.2 Realtime Telegram (Telethon) [Priority: v1.x]
**Backlog:**
- Transition from polling to `TelegramRealtimeSource` using Telethon's event bus for high-volume channels.

### 2.3 Self-Healing Scrapers [Priority: Community]
**Backlog:**
- LLM-based repair for `CareerSiteSource` when CSS selectors fail due to site redesign.
- Autonomous `FingerprintingNode` to detect ATS type (Greenhouse, Lever) automatically.

### 2.4 Autonomous Source Discovery [Priority: Community]
**Backlog:**
- Implement `SourceDiscoverer` to find new Telegram channels/sites based on user profile keywords.

## 3. Intelligence & Semantic

### 3.1 ProfileArchitect & Autonomous Onboarding [Priority: v1.x]
**Backlog:**
- Automatically extract `FilterProfile` from user resumes.
- Implement mandatory onboarding flow to ensure every run has a valid profile.

### 3.2 Vector Active Learning [Priority: post-MVP]
**Backlog:**
- Move from static profiles to dynamic vector ensembles based on user feedback (Like/Dislike).
- Use "positive/negative" centroids to tune relevance scoring in `SemanticPrefilterNode`.

### 3.3 Skill Knowledge Graph [Priority: post-MVP]
**Backlog:**
- Build a graph of skill relationships (e.g., "PyTorch -> Deep Learning") for semantic query expansion.

## 4. Reliability, Privacy & Security

### 4.1 Pre-flight Health Checks [Priority: v1.x]
**Backlog:**
- Add `ping()` to `Source` protocol to skip dead sites before the fetch phase.
- Implement heuristic availability checks (HEAD requests).

### 4.2 PII-Sandbox (Privacy-by-Design) [Priority: post-MVP]
**Backlog:**
- Mask PII (names, phones, emails) in a sandbox node before sending data to cloud LLM providers.
- Implement local de-masking at the storage layer.

### 4.3 Prompt Guard [Priority: v1.x]
**Backlog:**
- Detect and block prompt-injection attempts in raw job descriptions.

## 5. Analytics & UX

### 5.1 Market Intelligence Module [Priority: post-MVP]
**Backlog:**
- Aggregate salary trends and skill demand across the entire `JobGroup` store.
- Implement `job_ftch analyze` CLI command.

### 5.2 Response Assistant (Ice-breakers) [Priority: post-MVP]
**Backlog:**
- Generate personalized "Ice-breaker" messages based on the match between a job and a user profile.

## 6. Cleanup & Maintenance

### 6.1 Token-Saving Pre-normalization (Item 27) [DONE]
**Status:** Covered by `SanitizeNode` and input-hygiene filters.
**Backlog:**
- Further optimize `RegexSanitizer` to strip non-informative content (emojis, tracking links) before extraction to save tokens.

### 6.2 Hybrid Persistence Overlay [Priority: v1.x]
**Backlog:**
- Implement `FileBackedStoreOverlay` to sync `InMemoryStore` changes to disk for local CLI use.
