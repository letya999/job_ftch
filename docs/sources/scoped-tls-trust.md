---
title: "Scoped source TLS trust"
description: "Packaged public CA trust for source-specific HTTP clients without disabling TLS verification."
updated: 2026-11-09
---
# Scoped source TLS trust

T-Bank uses Russian Trusted CA, which is absent from the default certifi bundle.
The registered `tbank_career` parser selects `tls_ca_bundle: tbank-russian-root`
for listing, assessment and detail HTTP requests. Hostname verification and
`CERT_REQUIRED` remain enabled. Other sources and LLM clients keep their existing
trust configuration. Browser fallback does not receive a global trust change.

The packaged file contains a public CA certificate, never a private key:
`job_ftch/infrastructure/sources/certificates/tbank-russian-root.crt`.

- Provenance: [official T-Bank instructions](https://status.tbank-online.com/certificates/).
- Download: [Linux root certificate](https://help-static2.tbank-online.com/certs/linux_russian_trusted_root_ca_pem/russian_trusted_root_ca_pem.crt).
- DER SHA-256: `d26d2d0231b7c39f92cc738512ba54103519e4405d68b5bd703e9788ca8ecf31`.
- Subject: Russian Trusted Root CA; expiry: 2032-02-27.

Updating this trust anchor requires checking the official source, fingerprint,
CA constraints and a verified HTTPS request before replacing the packaged file.
Configuration accepts packaged bundle names only, not arbitrary filesystem paths.
