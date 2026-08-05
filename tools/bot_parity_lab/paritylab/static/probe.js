(() => {
  "use strict";

  const config = window.__PARITY_CONFIG__ || {};
  const sid = String(config.sid || "unassigned");
  const baseNavigationStart = performance.timeOrigin || Date.now() - performance.now();
  const probeErrors = [];
  const performanceSamples = {
    observers: [],
    eventTiming: [],
    longTasks: [],
    layoutShifts: [],
    errors: []
  };
  let sequence = 0;
  let eventSequence = 0;
  let eventQueue = [];
  let eventFlushTimer = null;
  let finalizing = false;

  const status = (id, value) => {
    const node = document.getElementById(id);
    if (node) node.textContent = value;
  };

  const setMessage = (value) => {
    const node = document.getElementById("status-message");
    if (node) node.textContent = value;
  };

  const url = (path, extra = {}) => {
    const target = new URL(path, location.origin);
    target.searchParams.set("sid", sid);
    for (const [key, value] of Object.entries(extra)) {
      target.searchParams.set(key, String(value));
    }
    return target.toString();
  };

  const normalize = (value, depth = 0, seen = new WeakSet()) => {
    if (depth > 8) return "[depth-limit]";
    if (value === null || value === undefined) return value ?? null;
    if (typeof value === "string" || typeof value === "boolean") return value;
    if (typeof value === "number") {
      if (Number.isNaN(value)) return "NaN";
      if (!Number.isFinite(value)) return String(value);
      return value;
    }
    if (typeof value === "bigint") return value.toString();
    if (typeof value === "symbol") return String(value);
    if (typeof value === "function") {
      try { return Function.prototype.toString.call(value); }
      catch (error) { return `[function:${String(error)}]`; }
    }
    if (value instanceof Error) {
      return {name: value.name, message: value.message, stack: value.stack || ""};
    }
    if (ArrayBuffer.isView(value)) {
      return Array.from(value.slice ? value.slice(0, 2048) : value).map(item => normalize(item, depth + 1, seen));
    }
    if (value instanceof ArrayBuffer) {
      return Array.from(new Uint8Array(value.slice(0, 2048)));
    }
    if (Array.isArray(value)) {
      return value.slice(0, 2048).map(item => normalize(item, depth + 1, seen));
    }
    if (typeof value === "object") {
      if (seen.has(value)) return "[circular]";
      seen.add(value);
      const output = {};
      for (const key of Reflect.ownKeys(value).slice(0, 512)) {
        const printableKey = typeof key === "symbol" ? String(key) : key;
        try { output[printableKey] = normalize(value[key], depth + 1, seen); }
        catch (error) { output[printableKey] = `[throws:${String(error)}]`; }
      }
      return output;
    }
    return String(value);
  };

  const captureError = (area, error) => {
    const item = {
      area,
      name: error && error.name ? String(error.name) : "Error",
      message: error && error.message ? String(error.message) : String(error),
      stack: error && error.stack ? String(error.stack) : ""
    };
    probeErrors.push(item);
    return item;
  };

  const safe = async (area, operation, fallback = null) => {
    try { return await operation(); }
    catch (error) { captureError(area, error); return fallback; }
  };

  const withTimeout = (promise, timeoutMs, label) => Promise.race([
    promise,
    new Promise((_, reject) => setTimeout(() => reject(new Error(`${label} timed out`)), timeoutMs))
  ]);

  const postJson = async (path, payload, options = {}) => {
    const response = await fetch(url(path), {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      keepalive: Boolean(options.keepalive),
      headers: {
        "content-type": "application/json",
        "x-parity-session": sid,
        ...(options.headers || {})
      },
      body: JSON.stringify(normalize(payload))
    });
    const contentType = response.headers.get("content-type") || "";
    return contentType.includes("application/json") ? response.json() : response.text();
  };

  const sendProbe = async (realm, data, errors = []) => {
    sequence += 1;
    return postJson("/api/probe", {realm, sequence, data, errors});
  };

  const hashBytes = async (bytes) => {
    const input = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
    const digest = await crypto.subtle.digest("SHA-256", input);
    return Array.from(new Uint8Array(digest)).map(byte => byte.toString(16).padStart(2, "0")).join("");
  };

  const hashString = async (value) => hashBytes(new TextEncoder().encode(String(value)));

  const runtimeCollector = window.ParityLabProbes.createRuntimeCollector({
    safe, withTimeout, hashBytes, hashString, normalize, captureError, performanceSamples, sid
  });
  const collectWindowProbe = runtimeCollector.collectWindowProbe;
  const collectIframeProbe = runtimeCollector.collectIframeProbe;
  const probePerformance = runtimeCollector.probePerformance;

  const probeDeep = async () => {
    if (!window.ParityLabProbes || typeof window.ParityLabProbes.collectDeep !== "function") {
      throw new Error("modular deep probe unavailable");
    }
    return window.ParityLabProbes.collectDeep({safe, hashString, hashBytes, normalize});
  };

  const runWorker = (kind, script, options = undefined, timeoutMs = 3500) => new Promise(resolve => {
    let worker;
    let timer;
    const finish = result => {
      clearTimeout(timer);
      try { if (worker && worker.terminate) worker.terminate(); }
      catch (_) {}
      resolve(result);
    };
    try {
      worker = new Worker(url(script), options);
      worker.addEventListener("message", event => finish({ok: true, data: event.data}));
      worker.addEventListener("error", event => finish({ok: false, error: `${event.message || kind} @ ${event.filename || ""}:${event.lineno || 0}`}));
      worker.postMessage({type: "probe", sid});
      timer = setTimeout(() => finish({ok: false, error: `${kind} timeout`}), timeoutMs);
    } catch (error) {
      finish({ok: false, error: String(error)});
    }
  });

  const runSharedWorker = (timeoutMs = 3500) => new Promise(resolve => {
    let shared;
    let timer;
    const finish = result => {
      clearTimeout(timer);
      try { if (shared) shared.port.close(); }
      catch (_) {}
      resolve(result);
    };
    try {
      shared = new SharedWorker(url("/static/shared-worker.js"), {name: `parity-${sid}`});
      shared.port.onmessage = event => finish({ok: true, data: event.data});
      shared.onerror = event => finish({ok: false, error: event.message || "shared worker error"});
      shared.port.start();
      shared.port.postMessage({type: "probe", sid});
      timer = setTimeout(() => finish({ok: false, error: "shared worker timeout"}), timeoutMs);
    } catch (error) {
      finish({ok: false, error: String(error)});
    }
  });

  const installPerformanceObservers = () => {
    const observe = (type, target) => {
      try {
        const observer = new PerformanceObserver(list => {
          for (const entry of list.getEntries()) {
            const value = entry.toJSON ? entry.toJSON() : normalize(entry);
            if (target.length < 1000) target.push(value);
          }
        });
        observer.observe({type, buffered: true, durationThreshold: type === "event" ? 16 : undefined});
        performanceSamples.observers.push(type);
      } catch (error) {
        performanceSamples.errors.push({type, error: String(error)});
      }
    };
    if (PerformanceObserver.supportedEntryTypes) {
      if (PerformanceObserver.supportedEntryTypes.includes("event")) observe("event", performanceSamples.eventTiming);
      if (PerformanceObserver.supportedEntryTypes.includes("longtask")) observe("longtask", performanceSamples.longTasks);
      if (PerformanceObserver.supportedEntryTypes.includes("layout-shift")) observe("layout-shift", performanceSamples.layoutShifts);
    }
  };

  const legacyEventData = event => {
    const data = {};
    if ("clientX" in event) {
      data.x = Math.round(event.clientX * 100) / 100;
      data.y = Math.round(event.clientY * 100) / 100;
      data.screenX = Math.round(event.screenX * 100) / 100;
      data.screenY = Math.round(event.screenY * 100) / 100;
      data.buttons = event.buttons;
      data.button = event.button;
    }
    if ("pointerType" in event) {
      data.pointerType = event.pointerType;
      data.pressure = event.pressure;
      data.width = event.width;
      data.height = event.height;
      data.isPrimary = event.isPrimary;
    }
    if (event.type === "scroll") {
      data.scrollX = window.scrollX;
      data.scrollY = window.scrollY;
    }
    if (event.type === "visibilitychange") data.visibilityState = document.visibilityState;
    if (event.type === "keydown" || event.type === "keyup") {
      const key = event.key || "";
      data.category = key.length === 1 ? "printable" : (/^(Arrow|Page|Home|End)/.test(key) ? "navigation" : "control");
      data.repeat = event.repeat;
      data.alt = event.altKey;
      data.ctrl = event.ctrlKey;
      data.meta = event.metaKey;
      data.shift = event.shiftKey;
    }
    if (event.type === "focus" || event.type === "blur") {
      data.target = event.target && event.target.id ? event.target.id : event.target === window ? "window" : "other";
    }
    return data;
  };

  const eventData = event => window.ParityLabProbes && window.ParityLabProbes.eventData
    ? window.ParityLabProbes.eventData(event)
    : legacyEventData(event);

  const queueEvent = event => {
    eventSequence += 1;
    eventQueue.push({
      sequence: eventSequence,
      type: event.type,
      clientTsMs: event.timeStamp,
      sinceNavigationMs: performance.now(),
      trusted: typeof event.isTrusted === "boolean" ? event.isTrusted : null,
      data: eventData(event)
    });
    if (eventQueue.length >= 64) void flushEvents();
  };

  const flushEvents = async () => {
    if (!eventQueue.length) return;
    const batch = eventQueue.splice(0, 512);
    try { await postJson("/api/events", {events: batch}, {keepalive: true}); }
    catch (error) {
      eventQueue = batch.concat(eventQueue).slice(-4096);
      captureError("events.flush", error);
    }
  };

  const installBehaviorCapture = () => {
    const targetEvents = [
      "pointermove", "pointerdown", "pointerup", "pointercancel", "mousemove", "mousedown", "mouseup",
      "click", "dblclick", "wheel", "scroll", "keydown", "keyup", "focus", "blur", "visibilitychange"
    ];
    for (const type of targetEvents) {
      const target = type === "visibilitychange" ? document : window;
      target.addEventListener(type, queueEvent, {capture: true, passive: type !== "keydown" && type !== "keyup"});
    }
    eventFlushTimer = setInterval(() => void flushEvents(), 750);
  };

  const runNetworkGraph = async () => {
    const output = {steps: {}, errors: []};
    const step = async (name, operation) => {
      const started = performance.now();
      try {
        const value = await operation();
        output.steps[name] = {ok: true, durationMs: performance.now() - started, value: normalize(value)};
      } catch (error) {
        output.steps[name] = {ok: false, durationMs: performance.now() - started, error: String(error)};
        output.errors.push({name, error: String(error)});
      }
    };

    await step("cookie-set", async () => (await fetch(url("/api/cookie/set"), {credentials: "same-origin", cache: "no-store"})).json());
    await step("cookie-echo", async () => (await fetch(url("/api/cookie/echo"), {credentials: "same-origin", cache: "no-store"})).json());
    await step("fetch-get", async () => (await fetch(url("/api/fetch"), {
      credentials: "same-origin", cache: "no-store", headers: {"x-parity-probe": "fetch-get"}
    })).json());
    await step("fetch-post", async () => (await fetch(url("/api/fetch"), {
      method: "POST", credentials: "same-origin", cache: "no-store",
      headers: {"content-type": "application/json", "x-parity-probe": "fetch-post"},
      body: JSON.stringify({sid, sentAt: performance.now()})
    })).json());
    await step("cache-first", async () => {
      const response = await fetch(url("/api/cacheable"), {credentials: "same-origin", cache: "default"});
      return {status: response.status, body: await response.text(), etag: response.headers.get("etag")};
    });
    await step("cache-revalidate", async () => {
      const response = await fetch(url("/api/cacheable"), {credentials: "same-origin", cache: "no-cache"});
      return {status: response.status, body: await response.text(), etag: response.headers.get("etag")};
    });
    await step("no-store", async () => (await fetch(url("/api/no-store"), {cache: "no-store"})).text());
    await step("delayed-waterfall", async () => Promise.all([
      fetch(url("/api/delay/25"), {cache: "no-store"}).then(response => response.text()),
      fetch(url("/api/delay/70"), {cache: "no-store"}).then(response => response.text()),
      fetch(url("/api/delay/120"), {cache: "no-store"}).then(response => response.text())
    ]));
    await step("opaque-shape", async () => postJson("/api/opaque", {
      version: 1,
      nonce: sid.slice(0, 16),
      timing: [performance.now(), Date.now() - baseNavigationStart],
      capabilities: {webgl: Boolean(window.WebGLRenderingContext), workers: Boolean(window.Worker)}
    }, {headers: {"x-opaque-id": `owned-${sid}`}}));
    await step("beacon", async () => {
      const payload = new Blob([JSON.stringify({sid, at: performance.now(), source: "local-page"})], {type: "application/json"});
      return navigator.sendBeacon(url("/api/beacon"), payload);
    });
    return output;
  };

  const collectFinalProbe = () => ({
    runtime: {
      webdriver: navigator.webdriver,
      userAgent: navigator.userAgent,
      platform: navigator.platform,
      language: navigator.language,
      languages: Array.from(navigator.languages || []),
      hardwareConcurrency: navigator.hardwareConcurrency
    },
    locale: {
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      resolvedLocale: Intl.DateTimeFormat().resolvedOptions().locale,
      dateOffset: new Date().getTimezoneOffset()
    },
    userActivation: navigator.userActivation ? {
      isActive: navigator.userActivation.isActive,
      hasBeenActive: navigator.userActivation.hasBeenActive
    } : null,
    focus: {
      documentHasFocus: document.hasFocus(),
      visibilityState: document.visibilityState,
      activeElement: document.activeElement ? document.activeElement.id || document.activeElement.tagName : null
    },
    performance: probePerformance(),
    behaviorQueueDepth: eventQueue.length,
    finishedAt: Date.now()
  });

  const finalize = async () => {
    if (finalizing) return window.__parityLabResult || null;
    finalizing = true;
    const button = document.getElementById("finish-button");
    if (button) button.disabled = true;
    status("status-report", "finalizing");
    setMessage("Flushing events and scoring the session…");
    try {
      await flushEvents();
      await sendProbe("window-final", collectFinalProbe(), []);
      await new Promise(resolve => setTimeout(resolve, 120));
      await flushEvents();
      const result = await postJson(`/api/finish/${encodeURIComponent(sid)}`, {
        client: config.client || "manual-browser",
        family: config.family || "manual",
        expectedFailure: Boolean(config.expectedFailure),
        gate: Boolean(config.gate),
        metadata: {
          pageSecureContext: window.isSecureContext,
          userActivation: navigator.userActivation ? {
            isActive: navigator.userActivation.isActive,
            hasBeenActive: navigator.userActivation.hasBeenActive
          } : null,
          finalizedFromPage: true
        }
      });
      window.__parityLabResult = result;
      window.__parityLabDone = true;
      status("status-report", result.summary ? result.summary.disposition : "complete");
      const summary = document.getElementById("report-summary");
      if (summary) summary.textContent = JSON.stringify({
        summary: result.summary,
        artifact_dir: result.artifact_dir,
        finding_codes: result.finding_codes
      }, null, 2);
      setMessage("Report finalized. Raw JSON, NDJSON, and Markdown were written locally.");
      return result;
    } catch (error) {
      captureError("finalize", error);
      status("status-report", "failed");
      setMessage(`Finalization failed: ${String(error)}`);
      if (button) button.disabled = false;
      finalizing = false;
      throw error;
    }
  };

  const bootstrap = async () => {
    status("status-bootstrap", "running");
    const secure = document.getElementById("secure-context");
    if (secure) secure.textContent = `secure context: ${window.isSecureContext}`;
    installPerformanceObservers();
    installBehaviorCapture();

    const networkPromise = safe("networkGraph", runNetworkGraph, {steps: {}, errors: ["failed"]});
    const windowData = await safe(
      "window.deadline",
      () => withTimeout(collectWindowProbe(), 20_000, "window probe"),
      {unavailable: true, timeout: true}
    );
    await sendProbe("window", windowData, probeErrors.slice());
    status("status-window", "captured");

    const iframeResult = await safe("iframe", collectIframeProbe, null);
    if (iframeResult) await sendProbe("iframe", iframeResult, []);

    const [classic, module, shared] = await Promise.all([
      runWorker("classic-worker", "/static/classic-worker.js"),
      runWorker("module-worker", "/static/module-worker.js", {type: "module"}),
      typeof SharedWorker === "function" ? runSharedWorker() : Promise.resolve({ok: false, error: "SharedWorker unavailable"})
    ]);
    for (const [realm, result] of [["classic-worker", classic], ["module-worker", module], ["shared-worker", shared]]) {
      if (result.ok) await sendProbe(realm, result.data, []);
      else await sendProbe(realm, {unavailable: true, error: result.error}, [{area: realm, message: result.error}]);
    }
    const deepData = await safe("deep", () => withTimeout(probeDeep(), 15_000, "deep probe"), {});
    await sendProbe("deep", deepData, []);
    status("status-realms", "captured");

    const network = await networkPromise;
    await sendProbe("network-client", network, network.errors || []);
    status("status-network", "captured");
    status("status-bootstrap", "ready");
    status("status-behavior", "recording");
    setMessage("Initial probes complete. Interact with the page, then finalize the report.");
    const button = document.getElementById("finish-button");
    if (button) {
      button.disabled = false;
      button.addEventListener("click", () => void finalize());
    }
    const target = document.getElementById("interaction-target");
    if (target) target.addEventListener("click", () => { target.textContent = "Target clicked"; });
    window.__parityLabReady = true;
  };

  const startBootstrap = () => void bootstrap().catch(error => {
    const captured = captureError("bootstrap", error);
    window.__parityLabBootstrapError = captured;
    status("status-bootstrap", "failed");
    setMessage(`Bootstrap failed: ${captured.name}: ${captured.message}`);
  });

  window.parityLabFinish = finalize;
  window.addEventListener("pagehide", () => {
    if (eventFlushTimer) clearInterval(eventFlushTimer);
    if (eventQueue.length) {
      try {
        navigator.sendBeacon(url("/api/events"), new Blob([JSON.stringify({events: eventQueue})], {type: "application/json"}));
      } catch (_) {}
    }
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", startBootstrap, {once: true});
  } else {
    startBootstrap();
  }
})();
