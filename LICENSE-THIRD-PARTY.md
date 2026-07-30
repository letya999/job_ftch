# Third-party license notices

This file records dependencies whose license or deployment conditions require
special attention. It is not a replacement for the license texts shipped by
those projects or for legal review.

| Component | Project extra | License / gate | Operational rule |
|---|---|---|---|
| `nodriver` | `nodriver` and `all` | AGPL-3.0-only; see ADR-073 | Optional only. Internal/private use is permitted by project policy. Distribution or public/SaaS use requires the ADR-073 source-disclosure review or removal/disablement of this capability. |
| `cloakbrowser` | `browser` and `all` | Verify the installed release's license before distribution | Optional terminal browser capability. Do not bundle or publish it without the release license gate. |
| `camoufox` | `camoufox` and `all` | Verify the installed release and bundled browser notices | Optional Firefox-family browser capability. Preserve upstream notices when distributing binaries. |

FlareSolverr is not a supported dependency and must not be restored by an
optional extra, container, runtime fallback, or documentation shortcut.
