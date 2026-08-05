from __future__ import annotations

import asyncio
import importlib.util
import logging
import signal

from hypercorn.asyncio import serve
from hypercorn.config import Config as HypercornConfig

from paritylab.app import create_app
from paritylab.certs import ensure_local_certificate
from paritylab.config import LabConfig
from paritylab.store import ArtifactStore
from paritylab.tls import TLSConnectionRegistry, TLSMirrorProxy

LOGGER = logging.getLogger(__name__)


async def serve_lab(config: LabConfig) -> None:
    certificates = ensure_local_certificate(config.certs_dir)
    store = ArtifactStore(config.artifacts_dir)
    registry = TLSConnectionRegistry()
    app = create_app(config, store=store, registry=registry)

    hyper = HypercornConfig()
    hyper.bind = [f"{config.host}:{config.backend_port}"]
    hyper.certfile = str(certificates.cert)
    hyper.keyfile = str(certificates.key)
    hyper.alpn_protocols = ["h2", "http/1.1"]
    hyper.accesslog = None
    hyper.errorlog = "-"
    hyper.graceful_timeout = 2.0
    hyper.keep_alive_timeout = 10.0
    hyper.use_reloader = False

    http3_available = importlib.util.find_spec("aioquic") is not None
    if config.enable_http3 and http3_available:
        hyper.quic_bind = [f"{config.host}:{config.public_port}"]
        LOGGER.info("HTTP/3 enabled on UDP %s:%s", config.host, config.public_port)
    elif config.enable_http3:
        LOGGER.warning("HTTP/3 requested but aioquic is unavailable; continuing with HTTP/1.1 and HTTP/2")

    stop_event = asyncio.Event()

    def request_stop() -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_stop)
        except NotImplementedError:
            pass

    async def shutdown_trigger() -> None:
        await stop_event.wait()

    proxy = TLSMirrorProxy(
        listen_host=config.host,
        listen_port=config.public_port,
        backend_host=config.host,
        backend_port=config.backend_port,
        registry=registry,
    )
    await proxy.start()
    LOGGER.info("Parity lab: %s", config.base_url)
    LOGGER.info("Artifacts: %s", config.artifacts_dir)
    try:
        await serve(app, hyper, shutdown_trigger=shutdown_trigger)
    finally:
        await proxy.close()


def run_server(config: LabConfig | None = None) -> None:
    asyncio.run(serve_lab(config or LabConfig.from_env()))
