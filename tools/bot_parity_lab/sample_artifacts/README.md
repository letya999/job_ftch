# Generated sample artifacts

These artifacts were produced by a real local HTTPS/HTTP2 run during archive validation.

- `raw-httpx`: minimal raw HTTP negative control.
- `curl`: curl/libcurl TLS and request-stack negative control.
- `browserish-httpx`: copied browser headers/resources without a JavaScript runtime.

All are intentionally expected to fail. Session IDs, timestamps, ephemeral ports and TLS hashes are evidence from the validation environment, not golden values for another machine.
