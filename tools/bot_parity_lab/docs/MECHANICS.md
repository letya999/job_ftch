# Detection and countermeasure encyclopedia

Generated from the versioned catalog. Entries describe controlled, owned-lab validation; they are not instructions for attacking third-party services.

## CDP instrumentation

- ID: `cdp_instrumentation`
- Category: `automation`
- Coverage: `implemented`
- Description: Control Chromium through DevTools Protocol.
- Observable consequences: injected globals, bindings, stack/evaluation markers
- Surfaces: `integrity.automation`, `integrity.timing`
- Countermeasures: Use native browser execution, Avoid invasive runtime patching
- Residual risk: Destructive vendor-specific side channels are intentionally excluded.

## WebDriver disclosure suppression

- ID: `webdriver_suppression`
- Category: `automation`
- Coverage: `implemented`
- Description: Hide navigator.webdriver without removing other automation artifacts.
- Observable consequences: descriptor mismatch, CDP globals, stack artifacts
- Surfaces: `integrity.automation`, `integrity.native_shape`
- Countermeasures: Avoid invasive runtime patching
- Residual risk: Artifacts change with browser and framework releases.

## Recorded behavior replay

- ID: `behavior_replay`
- Category: `behavior`
- Coverage: `implemented`
- Description: Replay previously captured human interactions.
- Observable consequences: cross-session templates, layout mismatch, repeated timing
- Surfaces: `behavior.replay`, `behavior.causality`
- Countermeasures: Use physical input semantics
- Residual risk: Near-match detection uses privacy-safe SimHash and needs multiple eligible sessions.

## Synthetic pointer movement

- ID: `synthetic_pointer`
- Category: `behavior`
- Coverage: `implemented`
- Description: Generate mouse or pointer trajectories programmatically.
- Observable consequences: regular cadence, linear paths, teleports, target mismatch
- Surfaces: `behavior.pointer`, `behavior.causality`
- Countermeasures: Use physical input semantics
- Residual risk: Native automation can emit trusted events.

## Timing randomization

- ID: `timing_randomization`
- Category: `behavior`
- Coverage: `implemented`
- Description: Add random delays to scripted actions.
- Observable consequences: causal mismatch, wrong distribution, resource/action disconnect, frame locking, clock inconsistencies
- Surfaces: `behavior.causality`, `integrity.timing`
- Countermeasures: Use physical input semantics, Bound concurrency and rate
- Residual risk: Distributional findings require profile-specific baselines and are never proof by themselves.

## Challenge outsourcing or token injection

- ID: `challenge_outsourcing`
- Category: `challenge`
- Coverage: `knowledge-only`
- Description: Obtain a valid answer or token through an external actor.
- Observable consequences: behavior discontinuity, network/persona switch, token timing anomaly
- Surfaces: `session.lifecycle`, `session.coherence`, `behavior.causality`
- Countermeasures: Bind state to one session, Implement challenge lifecycle correctly
- Residual risk: The lab models lifecycle only and does not integrate solving services.

## Clearance token replay

- ID: `clearance_replay`
- Category: `challenge`
- Coverage: `implemented`
- Description: Reuse challenge clearance outside its issued session or lifetime.
- Observable consequences: signature/binding failure, expiry, revocation
- Surfaces: `session.lifecycle`
- Countermeasures: Implement challenge lifecycle correctly, Bind state to one session
- Residual risk: Distributed deployments require consistent key and clock handling.

## UA and UA-CH spoofing

- ID: `ua_spoofing`
- Category: `identity`
- Coverage: `implemented`
- Description: Replace declared browser or platform identity.
- Observable consequences: realm drift, network/runtime mismatch, capability mismatch
- Surfaces: `runtime.identity`, `realm.parity`, `session.coherence`
- Countermeasures: Project one coherent persona, Use native browser execution
- Residual risk: A coherent declaration can still conflict with transport or rendering.

## Direct API harvesting

- ID: `api_harvesting`
- Category: `intent`
- Coverage: `implemented`
- Description: Skip document behavior and enumerate structured endpoints.
- Observable consequences: API-first sequence, missing navigation, pagination walk
- Surfaces: `network.http.semantics`, `session.lifecycle`
- Countermeasures: Use an authorized structured API, Bind state to one session
- Residual risk: First-party applications may legitimately use APIs directly.

## Trap and honeypot avoidance

- ID: `trap_avoidance`
- Category: `intent`
- Coverage: `implemented`
- Description: Detect and avoid hidden links or trap endpoints.
- Observable consequences: selective graph traversal, DOM/network intent mismatch
- Surfaces: `session.lifecycle`, `network.http.semantics`
- Countermeasures: Use an authorized structured API
- Residual risk: Accessibility and parser differences affect visibility.

## Audio fingerprint spoofing

- ID: `audio_spoofing`
- Category: `media`
- Coverage: `implemented`
- Description: Patch audio processing output or disable the surface.
- Observable consequences: render failure, latency/output mismatch, native wrapper leakage
- Surfaces: `media.audio`, `integrity.native_shape`
- Countermeasures: Preserve honest capability shape, Avoid invasive runtime patching
- Residual risk: Audio availability varies in containers and privacy modes.

## Media capability spoofing

- ID: `media_capability_spoofing`
- Category: `media`
- Coverage: `implemented`
- Description: Forge codec and device capability results.
- Observable consequences: API matrix inconsistency, decode failure, permission/label conflict, device transition mismatch
- Surfaces: `media.codecs`, `media.devices`
- Countermeasures: Preserve honest capability shape
- Residual risk: Hardware acceleration, permissions and enterprise policy legitimately affect results.

## Browser header replay

- ID: `header_replay`
- Category: `network`
- Coverage: `implemented`
- Description: Copy a browser-like header set into a non-browser client.
- Observable consequences: header order drift, fetch metadata gaps, collapsed waterfall
- Surfaces: `network.http.header_shape`, `network.http.semantics`
- Countermeasures: Use browser-owned transport, Use native browser execution
- Residual risk: Proxies can legitimately normalize headers.

## Proxy and IP rotation

- ID: `proxy_rotation`
- Category: `network`
- Coverage: `implemented`
- Description: Change egress identity between requests or sessions.
- Observable consequences: IP/session drift, ASN mismatch, TLS reuse break
- Surfaces: `network.ip.reputation`, `session.coherence`, `network.tls.lifecycle`
- Countermeasures: Bind state to one session
- Residual risk: Mobile and enterprise networks change legitimately.

## Privacy-browser randomization

- ID: `privacy_randomization`
- Category: `privacy`
- Coverage: `implemented`
- Description: Reduce or randomize fingerprint surfaces.
- Observable consequences: unstable outputs, reduced entropy, capability suppression
- Surfaces: `rendering.canvas`, `rendering.webgl`, `runtime.identity`, `runtime.screen`
- Countermeasures: Preserve honest capability shape
- Residual risk: Must not be treated as bot evidence by itself.

## Canvas spoofing

- ID: `canvas_spoofing`
- Category: `rendering`
- Coverage: `implemented`
- Description: Modify canvas reads or rendering output.
- Observable consequences: unstable digest, realm mismatch, API integrity drift
- Surfaces: `rendering.canvas`, `integrity.native_shape`, `realm.parity`
- Countermeasures: Preserve honest capability shape, Avoid invasive runtime patching
- Residual risk: Legitimate anti-fingerprinting introduces noise.

## Font and text metric spoofing

- ID: `font_spoofing`
- Category: `rendering`
- Coverage: `implemented`
- Description: Forge font presence or glyph metrics.
- Observable consequences: fallback inconsistency, geometry mismatch, raster mismatch
- Surfaces: `rendering.fonts`, `rendering.geometry`
- Countermeasures: Preserve honest capability shape
- Residual risk: Fonts vary naturally by OS and language packs; baseline by engine, OS and language pack.

## ClientRect and geometry spoofing

- ID: `geometry_spoofing`
- Category: `rendering`
- Coverage: `implemented`
- Description: Alter DOM geometry or inject measurement noise.
- Observable consequences: layout constraints fail, realm instability, zoom mismatch
- Surfaces: `rendering.geometry`, `runtime.screen`
- Countermeasures: Preserve honest capability shape
- Residual risk: Zoom and accessibility settings produce legitimate changes.

## WebGL spoofing

- ID: `webgl_spoofing`
- Category: `rendering`
- Coverage: `implemented`
- Description: Patch reported GPU identity or parameters.
- Observable consequences: realm mismatch, renderer/output mismatch, unsupported capability shape
- Surfaces: `rendering.webgl`, `realm.parity`
- Countermeasures: Preserve honest capability shape, Avoid invasive runtime patching
- Residual risk: Privacy browsers intentionally randomize or reduce WebGL surfaces.

## Page-only runtime patching

- ID: `page_only_patch`
- Category: `runtime`
- Coverage: `implemented`
- Description: Patch only the primary Window realm.
- Observable consequences: iframe drift, worker drift, native wrapper leakage
- Surfaces: `realm.parity`, `integrity.native_shape`
- Countermeasures: Project one coherent persona, Avoid invasive runtime patching
- Residual risk: Some isolated worlds are not directly observable.

## Worker projection leakage

- ID: `worker_patch_leakage`
- Category: `runtime`
- Coverage: `implemented`
- Description: Fail to project identity into worker realms.
- Observable consequences: worker identity mismatch, OffscreenCanvas mismatch
- Surfaces: `realm.parity`
- Countermeasures: Project one coherent persona
- Residual risk: Service-worker lifecycle complicates deterministic capture.

## Cookie and session transplant

- ID: `cookie_transplant`
- Category: `session`
- Coverage: `implemented`
- Description: Move state between clients, personas or network origins.
- Observable consequences: identity drift, transaction order mismatch, binding failure
- Surfaces: `runtime.storage`, `session.coherence`, `session.lifecycle`
- Countermeasures: Bind state to one session
- Residual risk: Legitimate browser profile migration can move cookies.

## Burst and concurrency shaping

- ID: `request_burst`
- Category: `traffic`
- Coverage: `implemented`
- Description: Fetch pages or APIs at machine concurrency.
- Observable consequences: collapsed waterfall, rate escalation, connection anomalies
- Surfaces: `network.http.semantics`, `session.lifecycle`
- Countermeasures: Bound concurrency and rate
- Residual risk: Fast legitimate clients and preload can resemble bursts.

## Browser resource blocking

- ID: `resource_blocking`
- Category: `traffic`
- Coverage: `implemented`
- Description: Suppress scripts, images, fonts or telemetry resources.
- Observable consequences: missing resource graph, probe absence, cache anomalies
- Surfaces: `network.http.semantics`, `session.coherence`
- Countermeasures: Use native browser execution
- Residual risk: Content blockers and accessibility tools can be benign.

## HTTP/2 fingerprint impersonation

- ID: `http2_impersonation`
- Category: `transport`
- Coverage: `implemented`
- Description: Mimic SETTINGS and frame behavior.
- Observable consequences: frame order drift, header mismatch, flow-control mismatch
- Surfaces: `network.http2.frames`, `session.coherence`
- Countermeasures: Use browser-owned transport
- Residual risk: A coherent implementation still requires correlation with runtime and resource behavior.

## QUIC and HTTP/3 impersonation

- ID: `quic_impersonation`
- Category: `transport`
- Coverage: `implemented`
- Description: Mimic QUIC parameters and HTTP/3 request behavior.
- Observable consequences: transport parameter drift, migration mismatch, H3 SETTINGS/frame/QPACK shape mismatch
- Surfaces: `network.quic.transport`, `network.http3.frames`
- Countermeasures: Use browser-owned transport
- Residual risk: Decrypted stream evidence requires QUIC keys or instrumentation and protocol implementations evolve rapidly.

## TLS impersonation

- ID: `tls_impersonation`
- Category: `transport`
- Coverage: `implemented`
- Description: Mimic a browser ClientHello from a non-browser client.
- Observable consequences: HTTP/2 mismatch, runtime absence, profile version drift
- Surfaces: `network.tls.client_hello`, `network.http2.frames`, `session.coherence`
- Countermeasures: Use browser-owned transport
- Residual risk: High-quality impersonators can match one protocol layer closely.
