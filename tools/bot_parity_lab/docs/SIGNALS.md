# Signal catalog

The scorer is intentionally explainable. A finding is emitted only with a stable code, class, exact reason and evidence. The default class is a starting point; calibrate it against several manual sessions from the same OS/browser family.

## Network/request layer

| Code | Default class | Why it matters |
|---|---|---|
| `NET_NO_REQUESTS` | hard | The session was finalized without any server-observed request, so there is no browser navigation to audit. |
| `NET_UA_MISSING` | hard | A document navigation without a User-Agent is unlike current interactive browsers. |
| `NET_UA_NON_BROWSER` | hard | The UA directly names a raw HTTP library. This is expected for negative controls. |
| `NET_ACCEPT_NAV_MISMATCH` | medium | Browser document navigations normally advertise HTML/document media types rather than only `*/*`. |
| `NET_ACCEPT_LANGUAGE_MISSING` | medium | Interactive profiles normally send at least one preferred language. |
| `NET_ACCEPT_ENCODING_MISSING` | medium | Current browsers negotiate compressed response encodings. |
| `NET_SEC_FETCH_ABSENT` | hard | Fetch Metadata headers are almost entirely absent on a declared browser navigation. |
| `NET_SEC_FETCH_PARTIAL` | medium | Only part of the `Sec-Fetch-*` tuple is present, which can reveal header copying or middleware loss. |
| `NET_FETCH_DEST_MISMATCH` | medium | The server-observed request path/resource type conflicts with `Sec-Fetch-Dest` or navigation semantics. |
| `NET_CH_UA_MISSING` | low | A Chromium-family declaration lacks UA Client Hints. Policies and browser versions can legitimately affect this. |
| `NET_CH_UA_FAMILY_CONFLICT` | medium | User-Agent and `Sec-CH-UA` identify incompatible browser families. |
| `NET_HEADER_ORDER_UNUSUAL` | low | The ordered document headers differ from common browser stack shapes. Header order is weak alone but useful with TLS/runtime evidence. |
| `NET_CONNECTION_REUSE_LOW` | low | A resource graph used an unexpectedly high number of independent connections instead of reusing HTTP/2 or keep-alive transport. |
| `NET_WATERFALL_COLLAPSED` | medium | Most resources arrived with near-zero spacing, suggesting a scripted request list rather than a browser parser/execution waterfall. |
| `NET_RESOURCE_MISSING_<PATH>` | hard/medium/low | A known page resource was not requested. Severity depends on whether the missing item removes the main JS runtime, a core worker/API edge or weak browser noise such as favicon. |
| `NET_COOKIE_NOT_RETURNED` | medium | A locally set secure cookie was not returned to the echo endpoint, indicating disabled storage, a broken cookie jar or non-browser fetch behavior. |
| `NET_COOKIE_TRANSACTION_ORDER` | medium | The local cookie echo preceded its set endpoint, so the session transaction graph is inconsistent. |
| `NET_REDIRECT_CHAIN_ORDER` | medium | The fixed same-origin redirect hops did not arrive in start, mid, final order. |
| `NET_CACHE_REVALIDATION_ABSENT` | low | The fixed ETag resource was fetched twice without normal conditional revalidation behavior. |
| `SESSION_REQUEST_UA_DRIFT` | medium | Same-origin requests supplied more than one User-Agent; evidence retains only value hashes. |
| `SESSION_REQUEST_LANGUAGE_DRIFT` | medium | Same-origin requests used incompatible primary `Accept-Language` families. |
| `SESSION_NETWORK_RUNTIME_UA_MISMATCH` | medium | Server-observed navigation UA conflicts with `navigator.userAgent`; values are recorded only as hashes. |
| `SESSION_NETWORK_RUNTIME_LANGUAGE_CONFLICT` | medium | `Accept-Language` and `navigator.language` disagree at the primary-language level. |
| `SESSION_PRIMARY_PROBE_ERRORS` | medium | The main window probe submitted collection errors, so a required runtime surface lacks reliable evidence. |
| `SESSION_PROBE_SEQUENCE_INVALID` | medium | Cross-realm probe submissions reused or regressed their sequence number. |
| `SESSION_WORKER_BOOTSTRAP_UA_DRIFT` | low | Worker-script bootstrap identity differed from the foreground context. It remains auditable, but is not conflated with page/API identity drift because browser process startup can precede context projection. |

The raw artifact retains every header pair in received order, not just a normalized dictionary.

## TLS and transport

| Code | Default class | Why it matters |
|---|---|---|
| `TLS_CLIENT_HELLO_UNAVAILABLE` | medium | The TCP session could not be mapped to a parsed ClientHello. This can indicate a non-TLS path, parser failure or an unsupported transport. |
| `TLS_FINGERPRINT_DRIFT` | medium | One logical session emitted different JA3/JA4 values. Legitimate multi-engine clients can do this, but a single browser profile normally stays stable. |
| `TLS_ALPN_UA_CONFLICT` | medium | A modern browser UA offered an implausible ALPN set, such as no `h2` on the TCP path. |
| `TLS_CLIENT_HELLO_TOO_SPARSE` | medium | Cipher or extension counts are too small for the declared current browser family. |
| `TLS_SNI_MISSING` | low | SNI is absent. This is common when using an IP literal locally but differs from normal domain navigation. |
| `TLS_FINGERPRINT_CAPTURED` | info | JA3/JA4, ALPN, cipher and extension evidence was captured successfully. |
| `HTTP_ONLY_1_1` | low | The entire graph used HTTP/1.1. This can be legitimate, but it is useful when a browser profile is expected to negotiate HTTP/2. |
| `HTTP_VERSION_DISTRIBUTION` | info | Records the exact server-observed protocol distribution. |
| `HTTP3_OBSERVED` | info | At least one request arrived over QUIC/HTTP/3. |

JA3 and JA4 are implementation/profile fingerprints, not “human” proofs. Compare them with a manual baseline from the same browser and machine class.

## Browser JavaScript runtime

| Code | Default class | Why it matters |
|---|---|---|
| `JS_WINDOW_PROBE_MISSING` | hard | The primary page script did not execute or submit. Raw HTTP clients and script-disabled pages fail here intentionally. |
| `JS_NAVIGATOR_WEBDRIVER` | hard | `navigator.webdriver === true` is a direct WebDriver automation disclosure. |
| `JS_HEADLESS_UA` | hard | The JavaScript UA contains an explicit headless token. |
| `JS_LANGUAGES_EMPTY` | medium | `navigator.languages` has no preferences. Minimal or privacy-hardened profiles can be exceptions. |
| `JS_LANGUAGE_ORDER_MISMATCH` | low | `navigator.language` differs from `navigator.languages[0]`. |
| `JS_PLATFORM_UACH_CONFLICT` | medium | `navigator.platform` and UA Client Hints describe different OS families. |
| `JS_CHROME_OBJECT_MISSING` | medium | A Chromium UA lacks the expected `window.chrome` surface. |
| `JS_ZERO_VIEWPORT` | hard | The document reports no usable interactive viewport. |
| `JS_OUTER_DIMENSIONS_ZERO` | medium | Inner viewport exists while outer browser-window dimensions are absent. |
| `JS_WEBGL_UNAVAILABLE` | medium | WebGL is absent in a context where the accepted baseline exposes it. |
| `JS_SOFTWARE_WEBGL` | medium | The unmasked renderer identifies a software rasterizer such as SwiftShader/llvmpipe. This is common in containers and should be calibrated. |
| `JS_CANVAS_PROBE_FAILED` | medium | Canvas rendering/readback did not produce a digest. |
| `JS_AUDIO_PROBE_FAILED` | low | OfflineAudioContext did not produce a digest. Privacy or disabled audio can explain it. |
| `JS_PLUGINS_EMPTY_CHROMIUM` | low | A Chromium profile exposes no built-in PDF/plugin entries. |
| `JS_FUNCTION_TOSTRING_PATCHED` | hard | `Function.prototype.toString` itself lacks native-code shape, suggesting a core runtime patch. |
| `JS_NATIVE_FUNCTION_SHAPE_MISMATCH` | medium | Expected built-ins stringify unlike native functions, which can reveal wrappers or incomplete stealth patches. |
| `JS_PERMISSION_NOTIFICATION_CONFLICT` | medium | `Permissions.query({name:'notifications'})` and `Notification.permission` disagree. |
| `JS_SPEECH_VOICES_EMPTY` | low | No speech synthesis voices were exposed. Common in minimal Linux containers. |

The raw probe also records values that are not directly scored by default: vendor, device memory, touch points, permissions, font presence, media-device counts, battery, storage quota, clipboard state, screen depth, DPR, WebGL extensions/parameters, Canvas/Audio hashes, Error stack shape and iframe descriptors.

## CDP and automation artifacts

| Code | Default class | Why it matters |
|---|---|---|
| `CDP_AUTOMATION_GLOBALS` | hard | Known Selenium/Playwright/Puppeteer-style globals are present in the main page realm. |
| `CDP_STACK_MARKERS` | medium | A locally generated Error stack includes evaluation/automation markers. Stack formatting changes across engines, so baseline it. |

The probe does not intentionally trigger destructive or vendor-specific CDP side channels. It checks page-visible globals, stack shape and native function integrity.

## Cross-realm parity

| Code/pattern | Default class | Why it matters |
|---|---|---|
| `REALM_IFRAME_MISSING` | medium | The same-origin iframe did not return a probe. |
| `REALM_CLASSIC_WORKER_MISSING` | medium | A classic Worker did not start or return values. |
| `REALM_MODULE_WORKER_MISSING` | low | A module Worker did not return values. Browser policy/support can explain it. |
| `REALM_PARITY_<REALM>_<FIELD>` | hard/medium/low | Window and iframe/worker expose different UA, platform, language, timezone or hardware concurrency values. Severity depends on the field. |
| `REALM_IFRAME_WEBGL_MISMATCH` | medium | Same-origin iframe and window expose different unmasked WebGL renderers. |
| `REALM_<WORKER>_WEBGL_MISMATCH` | low | OffscreenCanvas worker renderer differs from the window renderer. GPU process and privacy behavior can create legitimate variation. |

Workers are especially useful because many page-realm patches do not automatically propagate to WorkerGlobalScope.

## Behavioral signals

| Code | Default class | Why it matters |
|---|---|---|
| `BEHAVIOR_NO_EVENTS` | medium | No pointer, keyboard, scroll, focus or visibility events were captured before finalization. |
| `BEHAVIOR_ALL_UNTRUSTED` | hard | Every captured DOM event has `isTrusted=false`, consistent with JavaScript `dispatchEvent`. |
| `BEHAVIOR_NO_POINTER_PATH` | medium | The session completed without mouse/pointer movement samples. |
| `BEHAVIOR_NO_CLICK` | low | No click occurred. Some valid read-only flows will trigger this. |
| `BEHAVIOR_NO_SCROLL` | low | A deliberately scrollable page was never scrolled. |
| `BEHAVIOR_FIRST_ACTION_TOO_FAST` | medium | The first action occurred under 80 ms after navigation start. |
| `BEHAVIOR_TIMESTAMP_REGRESSION` | medium | The original event sequence moved backwards in `performance.now()` time. Sorting is never used to hide this integrity failure. |
| `BEHAVIOR_EVENT_BURST_COMPRESSED` | medium | Eight or more heterogeneous interactions were emitted inside four milliseconds. This detects batched automation while allowing normal pointer/down/up/click clusters. |
| `BEHAVIOR_POINTER_TELEPORTS` | medium | Two or more large pointer jumps occurred inside three milliseconds. One rapid movement is not sufficient evidence. |
| `BEHAVIOR_POINTER_CADENCE_REGULAR` | medium | Pointer event intervals have extremely low variance. |
| `BEHAVIOR_POINTER_LINEAR` | medium | The movement path has almost no angular variance. |
| `BEHAVIOR_USER_ACTIVATION_CONFLICT` | medium | A click was observed but final `navigator.userActivation.hasBeenActive` stayed false. |

Native automation can produce trusted input and humans can move a mouse linearly. These rules are useful only as combined evidence and regression signals. The lab retains both original sequence and page-monotonic timestamps so that temporal integrity remains auditable.

## Offline IP/ASN evidence

| Code | Class | Why it matters |
|---|---|---|
| `IP_REPUTATION_OFFLINE` | info | Records the longest-prefix local policy match and optional local MaxMind ASN metadata. No external reputation call occurs. |

The example policy marks loopback/private ranges. Add only ranges and classifications you are authorized to use. Public proxy history or “residential quality” cannot be inferred from localhost.

## Opaque payload observations

Opaque payload records are evidence rather than standalone findings. Each sample includes byte length, content type, SHA-256, entropy, printable ratio, likely Base64/JSON flags and JSON key-name shape. Use baseline comparisons to detect missing payloads, constants, malformed serialization or unexpected cross-session changes.

The lab does not decode or forge closed vendor telemetry.

## Protected playground

| Code | Class | Why it matters |
|---|---|---|
| `PLAYGROUND_INTENT` | info | Records what the client tried to parse from the owned career-site catalog: probe, recon, single page, pagination walk, detail/API/catalog harvest, or trap seeking. |
| `PLAYGROUND_GATE_DECISIONS` | info | Summarizes local edge verdicts: allow, JS challenge, interactive challenge, deny, and tarpit. |
| `PLAYGROUND_FINGERPRINT_COVERAGE` | info | Records which browser realms submitted fingerprint probes, including the deep realm when available. |
| `PLAYGROUND_TRAP_HIT` | medium | The client requested a hidden trap or internal path from the owned playground. |
| `PLAYGROUND_CHALLENGE_REJECTED` | medium | A local proof-of-work, puzzle, or clearance check was rejected or expired. |
| `PLAYGROUND_POW_BRUTE_FORCE` | hard | A proof-of-work challenge exhausted repeated attempts and escalated beyond a normal browser loop. |

The protected playground adds local challenges and clearance cookies only inside
the loopback lab. Token values are never written to artifacts; reports retain
hash prefixes and aggregate decision evidence.

## Owned protection-fixture coverage

The local-only fixture route supplies compact WAF, CAPTCHA, passive-challenge,
and Qrator/jsid evidence pages. They exist only to regression-test early
classification and monitor-safe failure handling. They do not contact a vendor,
render a provider widget, accept a token, issue a clearance cookie, or expose a
solve path.
