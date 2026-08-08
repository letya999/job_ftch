# Architecture

The parity lab is an opt-in, loopback-first defensive measurement tool. Observation, interpretation and policy are separate so a missing probe or one weak signal cannot silently become a bot verdict.

```text
collector -> raw observation -> normalized fact -> finding
          -> correlated hypothesis -> score policy -> gate decision
```

## Package boundaries

| Package | Responsibility |
|---|---|
| `paritylab.catalog` | Versioned knowledge schema, registry, audit and coverage ledger |
| `paritylab.scoring.network` | HTTP request shape, waterfall and fetch metadata |
| `paritylab.scoring.tls` | ClientHello persona and TLS connection lifecycle coherence |
| `paritylab.scoring.runtime` | Browser runtime and rendering availability |
| `paritylab.scoring.integrity` | Cross-request and runtime/network coherence |
| `paritylab.scoring.realm` | Window, iframe and worker parity |
| `paritylab.scoring.behavior` | Explainable behavioral findings |
| `paritylab.behavior_features` | Pointer, keyboard and scroll feature extraction |
| `paritylab.scoring.playground` | Owned playground findings |
| `paritylab.scoring.protocol` | Protocol and offline reputation evidence |
| `paritylab.capture_adapters` | Pure, privacy-filtering adapters for external protocol oracles |
| `paritylab.oss_registry` | Version, license, namespace and checksum admission for OSS oracles |
| `paritylab.routes.playground` | Protected catalog, trap and challenge routes |
| `paritylab.scoring.engine` | Finding aggregation and gate disposition |

`paritylab.scoring.__init__` is the compatibility facade. Consumers continue importing `score_session` from `paritylab.scoring`.

Browser collectors follow the same ownership split:

| Module | Responsibility |
|---|---|
| `static/probes/capabilities.js` | Storage, quota, media-device and cross-origin capability shape |
| `static/probes/runtime.js` | Window/iframe runtime identity, native shape and automation integrity |
| `static/probes/rendering.js` | Geometry, WebGPU workload/readback, codecs and font raster evidence |
| `static/probes/deep.js` | Timing, WebRTC and deep realm orchestration |
| `static/probes/behavior.js` | Trusted event stream and physical interaction evidence |

## Knowledge contract

`paritylab/catalog/catalog.json` is the source of truth for the surface inventory and bypass/countermeasure relationships. Generate and validate documentation with:

```powershell
python scripts/catalog_docs.py
python scripts/catalog_docs.py --check --json
```

- `implemented`: collector and analyzer exist and are tested.
- `partial`: useful evidence exists but the documented surface is not covered fully.
- `planned`: accepted scope with no complete collector yet.
- `knowledge-only`: documented for correlation or external capture, not collected by default.
- `unavailable`: cannot be provided honestly in the local architecture.
- `obsolete`: retained for historical report compatibility.

## Safety and privacy

- The default server is local and uses owned fixtures.
- Reports minimize raw identity values and prefer hashes or aggregate shapes.
- Behavioral features are session-scoped. Cross-session replay analysis requires an explicit dataset and retention policy.
- Vendor telemetry is treated as opaque payload shape; closed telemetry is not decoded or forged.
- External collectors cannot define canonical finding codes or verdict policy.
