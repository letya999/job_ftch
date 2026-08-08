from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True, frozen=True)
class LabConfig:
    host: str = "127.0.0.1"
    url_host: str = "localhost"
    public_port: int = 8443
    backend_port: int = 8444
    artifacts_dir: Path = Path("artifacts")
    certs_dir: Path = Path("certs")
    ip_reputation_file: Path = Path("data/ip_reputation.example.json")
    oss_registry_file: Path = Path("data/oss_components.json")
    asn_database_file: Path | None = None
    enable_http3: bool = True
    request_body_limit: int = 2 * 1024 * 1024
    static_dir: Path = Path(__file__).with_name("static")
    playground_enabled: bool = False
    playground_seed: int = 20260804
    playground_tarpit_delay_ms: int = 1200
    playground_rate_limit: int = 40

    def __post_init__(self) -> None:
        for field_name, raw_host in (("PARITYLAB_HOST", self.host), ("PARITYLAB_URL_HOST", self.url_host)):
            host = raw_host.strip().lower()
            if host == "localhost":
                continue
            try:
                address = ipaddress.ip_address(host)
            except ValueError as exc:
                raise ValueError(f"{field_name} must be localhost or a loopback IP") from exc
            if not address.is_loopback:
                raise ValueError("Parity lab is intentionally restricted to loopback interfaces")
        if self.public_port == self.backend_port:
            raise ValueError("public_port and backend_port must differ")

    @property
    def base_url(self) -> str:
        host = f"[{self.url_host}]" if ":" in self.url_host else self.url_host
        return f"https://{host}:{self.public_port}"

    @property
    def backend_url(self) -> str:
        return f"https://{self.host}:{self.backend_port}"

    @classmethod
    def from_env(cls) -> "LabConfig":
        root = Path(os.environ.get("PARITYLAB_ROOT", Path.cwd())).resolve()
        host = os.environ.get("PARITYLAB_HOST", "127.0.0.1")
        url_host = os.environ.get("PARITYLAB_URL_HOST", "localhost")
        public_port = int(os.environ.get("PARITYLAB_PORT", "8443"))
        backend_port = int(os.environ.get("PARITYLAB_BACKEND_PORT", str(public_port + 1)))
        enable_http3 = os.environ.get("PARITYLAB_HTTP3", "1") not in {"0", "false", "False"}
        playground_enabled = os.environ.get("PARITYLAB_PLAYGROUND", "0") in {"1", "true", "True"}
        playground_seed = int(os.environ.get("PARITYLAB_PLAYGROUND_SEED", "20260804"))
        return cls(
            host=host,
            url_host=url_host,
            public_port=public_port,
            backend_port=backend_port,
            artifacts_dir=(root / os.environ.get("PARITYLAB_ARTIFACTS", "artifacts")).resolve(),
            certs_dir=(root / os.environ.get("PARITYLAB_CERTS", "certs")).resolve(),
            ip_reputation_file=(
                root / os.environ.get("PARITYLAB_IP_REPUTATION", "data/ip_reputation.example.json")
            ).resolve(),
            oss_registry_file=(
                root / os.environ.get("PARITYLAB_OSS_REGISTRY", "data/oss_components.json")
            ).resolve(),
            asn_database_file=(
                (root / os.environ["PARITYLAB_ASN_MMDB"]).resolve()
                if os.environ.get("PARITYLAB_ASN_MMDB")
                else None
            ),
            enable_http3=enable_http3,
            playground_enabled=playground_enabled,
            playground_seed=playground_seed,
        )
