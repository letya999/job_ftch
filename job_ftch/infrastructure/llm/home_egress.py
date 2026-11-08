"""Fail-closed HTTP proxy relay for an explicitly selected Android exit node.

Runs in the dedicated egress container; the localhost Tailscale proxy is never
exposed directly to CLIProxy. No application credentials are handled here.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path
from tempfile import gettempdir
from typing import Any


def approved_exit(
    status: dict[str, Any], hostname: str, pinned_id: str = ""
) -> dict[str, Any] | None:
    if status.get("BackendState") != "Running":
        return None
    candidates = [
        peer
        for peer in (status.get("Peer") or {}).values()
        if (
            peer.get("HostName") == hostname
            or str(peer.get("DNSName", "")).split(".", 1)[0] == hostname
        )
        and peer.get("Online")
        and peer.get("ExitNodeOption")
        and peer.get("TailscaleIPs")
        and (not pinned_id or str(peer.get("ID")) == pinned_id)
    ]
    return candidates[0] if len(candidates) == 1 else None


class HomeEgress:
    def __init__(self) -> None:
        self.ready = False
        self.clients: set[asyncio.StreamWriter] = set()
        self.pin = Path("/var/lib/tailscale/home-exit-id")
        self.hostname = os.environ.get("JOB_FTCH_HOME_EXIT_NODE", "android-home")

    async def command(self, *args: str) -> tuple[int, str]:
        process = await asyncio.create_subprocess_exec(
            "tailscale",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), 15)
        except TimeoutError:
            process.kill()
            await process.wait()
            return 1, ""
        return process.returncode or 0, output.decode()

    async def monitor(self) -> None:
        while True:
            next_ready = False
            code, raw = await self.command("status", "--json")
            try:
                status = json.loads(raw) if code == 0 else {}
            except ValueError:
                status = {}
            pinned_id = self.pin.read_text().strip() if self.pin.exists() else ""
            peer = approved_exit(status, self.hostname, pinned_id)
            if peer:
                selected = status.get("ExitNodeStatus") or {}
                if str(selected.get("ID")) != str(peer["ID"]):
                    code, _ = await self.command(
                        "set",
                        f"--exit-node={peer['TailscaleIPs'][0]}",
                        "--exit-node-allow-lan-access=false",
                    )
                    if code == 0:
                        _, raw = await self.command("status", "--json")
                        try:
                            status = json.loads(raw)
                        except ValueError:
                            status = {}
                selected = status.get("ExitNodeStatus") or {}
                next_ready = (
                    str(selected.get("ID")) == str(peer["ID"]) and selected.get("Online") is True
                )
                if next_ready and not pinned_id:
                    self.pin.write_text(str(peer["ID"]))
            self.ready = next_ready
            if not self.ready:
                for client in tuple(self.clients):
                    client.close()
            marker = Path(gettempdir()) / "home-egress-ready"
            if self.ready:
                marker.touch()
            else:
                marker.unlink(missing_ok=True)
            await asyncio.sleep(5)

    async def relay(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        upstream = None
        try:
            if not self.ready or len(self.clients) >= 10:
                writer.write(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
                return
            self.clients.add(writer)
            remote_reader, upstream = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", 1056), 5
            )

            async def copy(source: asyncio.StreamReader, destination: asyncio.StreamWriter) -> None:
                while chunk := await source.read(65536):
                    destination.write(chunk)
                    await destination.drain()

            tasks = [
                asyncio.create_task(copy(reader, upstream)),
                asyncio.create_task(copy(remote_reader, writer)),
            ]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        except (OSError, TimeoutError):
            pass
        finally:
            self.clients.discard(writer)
            writer.close()
            if upstream:
                upstream.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()

    async def health(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 3)
            status = b"200 OK" if self.ready else b"503 Service Unavailable"
            writer.write(
                b"HTTP/1.1 " + status + b"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
            )
            await writer.drain()
        except (OSError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()

    async def run(self) -> None:
        daemon = await asyncio.create_subprocess_exec(
            "tailscaled",
            "--tun=userspace-networking",
            "--state=/var/lib/tailscale/tailscaled.state",
            "--outbound-http-proxy-listen=127.0.0.1:1056",
        )
        try:
            # Enrollment may require the operator to open the printed login URL.
            # Retry without recreating node identity or selecting an arbitrary exit.
            async def enroll() -> None:
                while True:
                    await asyncio.sleep(5)
                    _, raw = await self.command("status", "--json")
                    try:
                        if json.loads(raw).get("BackendState") == "Running":
                            await asyncio.sleep(60)
                            continue
                    except ValueError:
                        pass
                    process = await asyncio.create_subprocess_exec(
                        "tailscale",
                        "up",
                        "--hostname=job-ftch-egress",
                        "--accept-dns=false",
                    )
                    await process.wait()
                    await asyncio.sleep(60)

            bind_host = os.environ.get("JOB_FTCH_HOME_BIND_HOST", "127.0.0.1")
            server = await asyncio.start_server(self.relay, bind_host, 1055)
            health = await asyncio.start_server(self.health, bind_host, 1054)

            async def watch_daemon() -> None:
                await daemon.wait()
                raise RuntimeError("Tailscale daemon exited")

            async with server, health, asyncio.TaskGroup() as group:
                group.create_task(self.monitor())
                group.create_task(enroll())
                group.create_task(watch_daemon())
                group.create_task(server.serve_forever())
                group.create_task(health.serve_forever())
        finally:
            daemon.terminate()
            await daemon.wait()


if __name__ == "__main__":
    asyncio.run(HomeEgress().run())
