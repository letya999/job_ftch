from job_ftch.infrastructure.bypass.persona import PERSONA_POOL


def test_persona_pool_hardware_diversity():
    distinct_hardware = set()
    for persona in PERSONA_POOL:
        distinct_hardware.add((persona.hardware_concurrency, persona.device_memory))
    assert len(distinct_hardware) >= 8


def test_gpu_matches_os():
    for persona in PERSONA_POOL:
        platform = persona.navigator_platform
        sec_platform = persona.sec_ch_ua_platform
        renderer = persona.webgl_renderer
        if platform == "Win32" or sec_platform == '"Windows"':
            assert renderer.startswith("ANGLE")
        elif "Mac" in platform or sec_platform == '"macOS"':
            assert renderer.startswith("Apple GPU")
        elif "Linux" in platform or sec_platform == '"Linux"':
            assert renderer.startswith("Mesa")


def test_locale_matches_timezone():
    from job_ftch.infrastructure.bypass.persona import _LOCALES

    for persona in PERSONA_POOL:
        assert (persona.locale, persona.timezone) in _LOCALES


def test_ua_matches_platform():
    for persona in PERSONA_POOL:
        ua = persona.ua
        sec_platform = persona.sec_ch_ua_platform
        if "Windows NT" in ua:
            assert sec_platform == '"Windows"'
        elif "Macintosh" in ua:
            assert sec_platform == '"macOS"'
        elif "X11; Linux" in ua:
            assert sec_platform == '"Linux"'


def test_browser_family_consistency():
    for persona in PERSONA_POOL:
        family = persona.browser_family
        if family == "safari":
            assert "Mac" in persona.navigator_platform or persona.sec_ch_ua_platform == '"macOS"'
        elif family == "firefox":
            assert persona.sec_ch_ua == ""
            assert persona.navigator_vendor == ""


def test_font_list_matches_os():
    for persona in PERSONA_POOL:
        fonts = persona.font_list
        ua = persona.ua
        if "Windows NT" in ua:
            assert "Segoe UI" in fonts
        elif "Macintosh" in ua:
            assert "San Francisco" in fonts
        elif "X11; Linux" in ua:
            assert "DejaVu Sans" in fonts


def test_vendor_matches_family():
    for persona in PERSONA_POOL:
        family = persona.browser_family
        vendor = persona.navigator_vendor
        if family == "chromium":
            assert vendor == "Google Inc."
        elif family == "firefox":
            assert vendor == ""
        elif family == "safari":
            assert vendor == "Apple Computer, Inc."


def test_canvas_seed_uniqueness():
    seeds = set(p.canvas_seed for p in PERSONA_POOL)
    assert len(seeds) == 20


def test_screen_viewport_consistency():
    for p in PERSONA_POOL:
        assert p.screen_width >= p.viewport_width
        assert p.screen_height >= p.viewport_height
