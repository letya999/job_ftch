from paritylab.clients.camoufox_client import CamoufoxAdapter
from paritylab.clients.hook_client import HookAdapter
from paritylab.clients.http_clients import BrowserishHttpxAdapter, CurlAdapter, RawHttpxAdapter
from paritylab.clients.nodriver_client import NodriverAdapter
from paritylab.clients.patchright_client import PatchrightAdapter
from paritylab.clients.playwright_client import (
    PlaywrightAdapter,
    PlaywrightChromeChannelAdapter,
    PlaywrightFirefoxAdapter,
    PlaywrightWebKitAdapter,
)

ADAPTERS = {
    adapter.name: adapter
    for adapter in (
        RawHttpxAdapter(),
        CurlAdapter(),
        BrowserishHttpxAdapter(),
        PlaywrightAdapter(),
        PlaywrightWebKitAdapter(),
        PlaywrightChromeChannelAdapter(),
        PlaywrightFirefoxAdapter(),
        PatchrightAdapter(),
        NodriverAdapter(),
        CamoufoxAdapter(),
        HookAdapter(),
    )
}

__all__ = ["ADAPTERS"]
