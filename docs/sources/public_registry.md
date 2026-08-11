---
title: "Публичный реестр источников"
description: "Откуда берётся live-список источников канала ai_engineer_jobs: runtime/DB, public-safe JSON и границы приватности."
updated: 2026-08-11
---
# Публичный реестр источников

Страница описывает **живой** список источников, которые питают публикации
канала [t.me/ai_engineer_jobs](https://t.me/ai_engineer_jobs). Список **не**
зашит в Markdown и **не** читается из `fixtures/`.

## Source of truth

| Что | Где |
|---|---|
| Runtime tenant | `ai_jobs` |
| Хранение | tenant store / runtime overlay (тот же путь, что bot / API / MCP) |
| Reader | `TenantRunner.list_sources` → public sanitizer |
| Public contract | `GET /public/tenants/ai_jobs/sources.json` |

Оператор добавляет или отключает источник через Telegram-бота. После
изменения runtime state публичный JSON отражает новое состояние **без**
PR, без правки docs и без обновления fixtures.

Fixtures (`fixtures/sources/ai_jobs.json` и bootstrap YAML) остаются только
для тестов, smoke и dev bootstrap. Они **не** являются источником истины
для этой страницы.

## Machine-readable export

Публичный read-only endpoint (без API key, только allowlisted tenant slug):

```text
GET /public/tenants/{tenant_slug}/sources.json
```

Для канала `ai_engineer_jobs` tenant slug = `ai_jobs`.

Пример ответа (схема, не текущий список):

```json
{
  "generated_at": "2026-08-11T12:00:00+00:00",
  "tenant_slug": "ai_jobs",
  "source_count": 0,
  "status": "ok",
  "stale": false,
  "sources": [],
  "error": null
}
```

Каждая запись `sources[]` содержит только public-safe поля:

| Поле | Смысл |
|---|---|
| `source_id` | Стабильный публичный идентификатор |
| `kind` | Тип источника (`career_site`, `telegram_channel`, …) |
| `public_name` | Отображаемое имя |
| `public_url` / `public_handle` | Публичный URL или Telegram handle, если сам источник публичный |
| `enabled` / `status` | `enabled`, `disabled`, `degraded`, `candidate` |
| `category` / `region` | Опциональные публичные метки |
| `last_success_at` / `last_checked_at` | Health timestamps |
| `public_failure_reason` | Короткая безопасная причина сбоя (prefer allowlisted code) |
| `parser_route_summary` | Краткий публичный маршрут парсера/monitor |

## Public health / diagnostics contract

Статусы и причины строятся **на чтении** из того же runtime listing, что и
bot/API/MCP (`list_sources` + source health). Отдельный tracker, scheduler или
fixture source of truth **не** используются.

| `status` | Когда | Что ещё смотреть |
|---|---|---|
| `enabled` | Источник включён и есть недавнее health-наблюдение без degraded/error | `last_success_at`, `last_checked_at` |
| `disabled` | Оператор отключил источник | `enabled=false`; failure reason обычно пустой |
| `degraded` | Runtime `degraded` / `failing` / `paused` / `unhealthy` / `error`, либо известный `last_error_kind` | `public_failure_reason` |
| `candidate` | `pending` / ещё не проверялся / нет timestamps health | `last_*` = null; не путать с «тихо успешным нулём» |

`public_failure_reason` (приоритет):

1. allowlisted machine code из `last_error_kind` (например `layout_changed`,
   `auth_wall`, `challenge_required`, `empty_result`, `parser_error`,
   `deadline`, `source_fetch_failed`);
2. allowlisted code, если он явно встречается в free-text `last_error`;
3. sanitized free text (paths → `[path]`, secret-like → `redacted`);
4. для degraded без error payload — status-derived code (`paused`,
   `degraded`, …).

Непубликуемые детали (cookies, tokens, proxy endpoints, browser profile paths,
raw HTML/traces, tenant/user ids, private Telegram entities, resume data)
sanitizer отбрасывает или заменяет на `redacted`. Если отдельное runtime
состояние нельзя выразить текущими полями health/listing без широкой schema
work, оно **не** выдумывается в storage: только document/test note.

### No-JS fallback

Если виджет ниже недоступен, откройте JSON напрямую на host runtime API
(тот же путь `/public/tenants/ai_jobs/sources.json`). Base URL задаётся
оператором деплоя; GitHub Pages **сам по себе** БД не читает.

## Live table

<div id="public-source-registry" data-tenant="ai_jobs" data-endpoint="">
  <p id="public-source-registry-status">
    Загрузка списка источников из runtime registry…
  </p>
  <p>
    <a id="public-source-registry-json-link" href="#">Открыть sources.json</a>
  </p>
  <div style="overflow-x:auto">
    <table>
      <thead>
        <tr>
          <th>Type</th>
          <th>Name</th>
          <th>URL / handle</th>
          <th>Status</th>
          <th>Last success</th>
          <th>Last check</th>
          <th>Category / region</th>
        </tr>
      </thead>
      <tbody id="public-source-registry-body">
        <tr>
          <td colspan="7">Ожидание ответа API…</td>
        </tr>
      </tbody>
    </table>
  </div>
</div>

<script>
(function () {
  var root = document.getElementById("public-source-registry");
  if (!root) return;
  var statusEl = document.getElementById("public-source-registry-status");
  var bodyEl = document.getElementById("public-source-registry-body");
  var linkEl = document.getElementById("public-source-registry-json-link");
  var tenant = root.getAttribute("data-tenant") || "ai_jobs";
  var configured = (root.getAttribute("data-endpoint") || "").trim();
  var params = new URLSearchParams(window.location.search);
  var fromQuery = (params.get("sources_api") || "").trim();
  var endpoint = configured || fromQuery ||
    ("/public/tenants/" + encodeURIComponent(tenant) + "/sources.json");
  if (linkEl) {
    linkEl.setAttribute("href", endpoint);
    linkEl.textContent = "Открыть sources.json";
  }

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function cellLink(url, handle) {
    if (url) {
      return '<a href="' + esc(url) + '" rel="noopener noreferrer">' +
        esc(handle || url) + "</a>";
    }
    return esc(handle || "—");
  }

  function renderError(message) {
    if (statusEl) {
      statusEl.textContent = message;
    }
    if (bodyEl) {
      bodyEl.innerHTML =
        '<tr><td colspan="7">' + esc(message) + "</td></tr>";
    }
  }

  fetch(endpoint, { credentials: "omit", cache: "no-store" })
    .then(function (response) {
      if (!response.ok) {
        throw new Error("HTTP " + response.status);
      }
      return response.json();
    })
    .then(function (payload) {
      if (!payload || typeof payload !== "object") {
        renderError("Некорректный ответ public source registry.");
        return;
      }
      var status = payload.status || "unknown";
      var count = payload.source_count != null ? payload.source_count : 0;
      var generated = payload.generated_at || "—";
      var stale = payload.stale ? " stale" : "";
      if (statusEl) {
        if (status === "ok") {
          statusEl.textContent =
            "Runtime registry: " + count + " source(s), generated_at=" +
            generated + stale + ".";
        } else if (status === "stale") {
          statusEl.textContent =
            "Реестр помечен как stale. Показаны последние public-safe данные" +
            " (generated_at=" + generated + "). Fixtures не используются.";
        } else {
          statusEl.textContent =
            "Реестр недоступен (status=" + status + "). " +
            (payload.error || "Повторите позже. Fixtures не подставляются.");
        }
      }
      var sources = Array.isArray(payload.sources) ? payload.sources : [];
      if (!bodyEl) return;
      if (!sources.length) {
        bodyEl.innerHTML =
          '<tr><td colspan="7">Нет public-safe источников в runtime registry.</td></tr>';
        return;
      }
      bodyEl.innerHTML = sources.map(function (item) {
        var cat = [item.category, item.region].filter(Boolean).join(" / ") || "—";
        return "<tr>" +
          "<td>" + esc(item.kind || "—") + "</td>" +
          "<td>" + esc(item.public_name || item.source_id || "—") + "</td>" +
          "<td>" + cellLink(item.public_url, item.public_handle) + "</td>" +
          "<td>" + esc(item.status || (item.enabled ? "enabled" : "disabled")) + "</td>" +
          "<td>" + esc(item.last_success_at || "—") + "</td>" +
          "<td>" + esc(item.last_checked_at || "—") + "</td>" +
          "<td>" + esc(cat) + "</td>" +
          "</tr>";
      }).join("");
    })
    .catch(function (err) {
      renderError(
        "Не удалось загрузить runtime registry (" +
        (err && err.message ? err.message : "network error") +
        "). JSON endpoint: " + endpoint +
        ". Это не fallback на fixtures — список не подставляется из репозитория."
      );
    });
})();
</script>

Чтобы указать URL production API со статического docs site, добавьте query
`?sources_api=https://YOUR_API_HOST/public/tenants/ai_jobs/sources.json`
или задайте `data-endpoint` на контейнере при деплое docs.

## Stale / error behavior

| Ситуация | Поведение |
|---|---|
| Runtime OK | `status=ok`, актуальный `generated_at`, `source_count` |
| Tenant не в allowlist | HTTP 404, без утечки факта существования private tenant |
| Runtime/store недоступен | `status=error`, пустой `sources`, явное `error` message |
| Явный stale snapshot (будущий export job) | `status=stale`, `stale=true`, timestamp сохранён |
| Fixtures | **Никогда** не используются как fallback |

Клиент docs должен показывать понятное сообщение, а не «тихий» пустой успех
и не подмешивать hardcoded Markdown-таблицу текущих источников.

## Privacy boundaries

Публикуется только allowlist полей. Не публикуются:

- credentials, tokens, cookies, auth headers;
- proxy endpoints и browser profile paths;
- private Telegram entity id / invite links (handle/URL redacted);
- internal tenant/user ids (кроме публичного slug `ai_jobs`);
- resume / profile / shot data;
- raw HTML, traces, logs, screenshots;
- private notes и debug metadata (`spec`, `assessment.evidence`, `added_by`, …).

Private Telegram sources могут остаться в реестре как `kind` + redacted
`source_id` без handle/URL, либо быть опущены sanitizer'ом для local-only
типов (`local_fixture`).

## Related

- [Source Setup](setup.md) — declarative bootstrap и роль fixtures
- [Source Coverage Matrix](coverage_matrix.md) — operational guidance, не live list
- [Source assessment](source_assessment.md) — pre-ingest оценка
- [ADR 026 — Runtime source overlay](../adr/026-runtime-source-overlay.md)
