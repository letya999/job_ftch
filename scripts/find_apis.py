import asyncio

try:
    import nodriver
    from nodriver import cdp
except ImportError:
    pass


async def main():
    browser = await nodriver.start(headless=True)
    tab = await browser.get("about:blank")
    await tab.send(cdp.network.enable())

    def on_response(event):
        try:
            resp = event.response
            url = resp.url
            if "api" in url or "graphql" in url or "search" in url:
                print(f"API Call: {url}")
        except Exception:
            pass

    tab.add_handler(cdp.network.ResponseReceived, on_response)

    print("Fetching Kaspi...")
    await tab.get("https://job.kaspi.kz/")
    await asyncio.sleep(4)

    print("Fetching Sber...")
    await tab.get("https://rabota.sber.ru/search/")
    await asyncio.sleep(4)

    browser.stop()


if __name__ == "__main__":
    asyncio.run(main())
