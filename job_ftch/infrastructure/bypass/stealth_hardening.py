"""Advanced stealth hardening for browser pages.

Composable enhancement applied after basic stealth (playwright-stealth).
Injects JS overrides for:
- Canvas/WebGL fingerprint noise (per-session deterministic)
- Client Hints alignment with FingerprintProfile
- Timezone consistency enforcement
- Font enumeration masking
- WebDriver/automation property hiding

Applied via apply_stealth_hardening(page, persona) after page creation.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

import structlog

logger = structlog.get_logger("job_ftch.bypass.stealth_hardening")

# Wave 2.3 — GPU/SwiftShader safety. Containers commonly render via SwiftShader
# or ANGLE-on-LLVM; claiming a discrete NVIDIA/AMD renderer from such a
# container is a fast CreepJS red flag because the WebGL renderer string
# cross-correlates with GPU benchmarks (texture size limits, shader precision,
# textureFileDialog sites) that SwiftShader cannot match.
#
# Resolution strategy:
# - `JOB_FTCH_FORCE_SWIFTSHADER=1` — the operator declares the runtime cannot
#   do real GPU rendering. All personas are coerced to the SwiftShader
#   vendor/renderer pair ("Google Inc." / "Google SwiftShader").
# - Default (no env) — personas keep their ANGLE/Mesa/Apple renderer claim
#   because the operator asserts a real GPU is present at the browsew launch.
_SWIFTSHADER_VENDOR = "Google Inc."
_SWIFTSHADER_RENDERER = "Google SwiftShader"


def coerce_persona_renderer_for_runtime(persona: Any) -> Any:
    """Return a persona whose WebGL claim matches the runtime GPU capacity.

    If ``JOB_FTCH_FORCE_SWIFTSHADER`` is enabled, every persona is replaced
    with a SwiftShader-safe variant so we never claim a discrete GPU from a
    container that renders with SwiftShader. Callers must invoke this
    before ``apply_persona_hardening`` for the override to take effect.
    """
    import os

    if os.environ.get("JOB_FTCH_FORCE_SWIFTSHADER", "").lower() not in {"1", "true", "yes"}:
        return persona
    try:
        from dataclasses import replace

        return replace(
            persona,
            webgl_renderer=_SWIFTSHADER_RENDERER,
            # Keep webgl_vendor if persona already exposes it; otherwise the
            # stealth hardening step will derive it from the renderer.
        )
    except Exception:
        return persona


def _webgl_vendor_for_renderer(renderer: str) -> str:
    """Map a WebGL renderer string to its coherent vendor string."""
    if not renderer:
        return _SWIFTSHADER_VENDOR
    if renderer.startswith("Apple GPU"):
        return "Google Inc."
    if renderer == _SWIFTSHADER_RENDERER or "SwiftShader" in renderer:
        return _SWIFTSHADER_VENDOR
    # ANGLE/Mesa on Intel/NVIDIA/AMD strains all report "Google Inc. (Intel)"
    # in Chrome <=131 desktop layouts. Stay coherent with that historical
    # observation rather than guessing per-vendor families.
    return "Google Inc. (Intel)"


_NATIVE_TOSTRING_GUARD_JS = """
(() => {
    const nativeToString = Function.prototype.toString;
    const patchedFns = new WeakSet();
    const origDescriptors = new WeakMap();
    const _markNative = (fn, name) => {
        patchedFns.add(fn);
        origDescriptors.set(fn, name || 'anonymous');
    };
    window.__markNative = _markNative;
    Function.prototype.toString = function() {
        if (patchedFns.has(this)) {
            const name = origDescriptors.get(this) || '';
            return 'function ' + name + '() { [native code] }';
        }
        return nativeToString.call(this);
    };
    _markNative(Function.prototype.toString, 'toString');
    const origDefineProperty = Object.defineProperty;
    Object.defineProperty = function(obj, prop, descriptor) {
        const result = origDefineProperty.call(this, obj, prop, descriptor);
        if (descriptor && typeof descriptor.get === 'function') {
            _markNative(descriptor.get, 'get ' + String(prop));
        }
        if (descriptor && typeof descriptor.set === 'function') {
            _markNative(descriptor.set, 'set ' + String(prop));
        }
        if (descriptor && typeof descriptor.value === 'function') {
            _markNative(descriptor.value, String(prop));
        }
        return result;
    };
    _markNative(Object.defineProperty, 'defineProperty');
})();
"""

_CANVAS_NOISE_JS = """
(() => {
    const SEED = %d;
    const rng = (function(s) {
        return function() {
            s = Math.sin(s) * 10000;
            return s - Math.floor(s);
        };
    })(SEED);
    const touched = new WeakSet();
    const origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
    const applyNoiseOnce = (canvas) => {
        if (touched.has(canvas)) return;
        const ctx = canvas.getContext('2d');
        if (!ctx || !canvas.width || !canvas.height) return;
        const imageData = origGetImageData.call(ctx, 0, 0, canvas.width, canvas.height);
        for (let i = 0; i < imageData.data.length; i += 4) {
            imageData.data[i] += Math.floor(rng() * 2) - 1;
        }
        ctx.putImageData(imageData, 0, 0);
        touched.add(canvas);
    };

    CanvasRenderingContext2D.prototype.getImageData = function(sx, sy, sw, sh) {
        applyNoiseOnce(this.canvas);
        return origGetImageData.call(this, sx, sy, sw, sh);
    };

    const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(type, quality) {
        applyNoiseOnce(this);
        return origToDataURL.call(this, type, quality);
    };

    const origToBlob = HTMLCanvasElement.prototype.toBlob;
    HTMLCanvasElement.prototype.toBlob = function(cb, type, quality) {
        applyNoiseOnce(this);
        return origToBlob.call(this, cb, type, quality);
    };
})();
"""

_WEBGL_NOISE_JS = """
(() => {
    const VENDOR = '%s';
    const RENDERER = '%s';
    if (typeof WebGLRenderingContext === 'undefined') return;
    const origGetParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(pname) {
        if (pname === 37445) return VENDOR;
        if (pname === 37446) return RENDERER;
        return origGetParameter.call(this, pname);
    };
    if (typeof WebGL2RenderingContext !== 'undefined') {
        const origGetParameter2 = WebGL2RenderingContext.prototype.getParameter;
        WebGL2RenderingContext.prototype.getParameter = function(pname) {
            if (pname === 37445) return VENDOR;
            if (pname === 37446) return RENDERER;
            return origGetParameter2.call(this, pname);
        };
    }
})();
"""

_WORKER_WEBGL_BOOTSTRAP_JS = """
(() => {
    const WORKER_IDENTITY_PATCH = %s;
    const resolvedUrl = (scriptURL) => {
        try {
            return new URL(String(scriptURL), location.href).href;
        } catch (e) {
            return String(scriptURL);
        }
    };
    const makeBootstrapUrl = (scriptURL, options) => {
        const original = resolvedUrl(scriptURL);
        const isModule = Boolean(options && options.type === 'module');
        const bootstrap = isModule
            ? WORKER_IDENTITY_PATCH + "\\n;import " + JSON.stringify(original) + ";"
            : WORKER_IDENTITY_PATCH + "\\n;importScripts(" + JSON.stringify(original) + ");";
        return URL.createObjectURL(new Blob([bootstrap], {type: 'application/javascript'}));
    };
    const shouldWrap = (scriptURL, options) => {
        if (typeof Blob === 'undefined' || typeof URL === 'undefined' || !URL.createObjectURL) return false;
        try {
            const url = new URL(String(scriptURL), location.href);
            return url.protocol === 'blob:' || url.protocol === 'data:' || url.origin === location.origin;
        } catch (e) {
            return true;
        }
    };
    const wrapWorkerCtor = (name) => {
        const NativeWorker = self[name];
        if (typeof NativeWorker !== 'function') return;
        const WrappedWorker = function(scriptURL, options) {
            if (!shouldWrap(scriptURL, options)) {
                return new NativeWorker(scriptURL, options);
            }
            try {
                return new NativeWorker(makeBootstrapUrl(scriptURL, options), options);
            } catch (e) {
                return new NativeWorker(scriptURL, options);
            }
        };
        WrappedWorker.prototype = NativeWorker.prototype;
        Object.setPrototypeOf(WrappedWorker, NativeWorker);
        self[name] = WrappedWorker;
    };
    wrapWorkerCtor('Worker');
    wrapWorkerCtor('SharedWorker');
})();
"""

_WORKER_NAVIGATOR_JS = """
(() => {
    const LANGUAGE = '%s';
    const LANGUAGES = %s;
    if (typeof navigator === 'undefined') return;
    const proto = Object.getPrototypeOf(navigator);
    try {
        Object.defineProperty(proto, 'language', {get: () => LANGUAGE, configurable: true});
        Object.defineProperty(proto, 'languages', {get: () => LANGUAGES.slice(), configurable: true});
    } catch (e) {}
})();
"""

_REALM_COVERAGE_MATRIX = """
Realm coverage matrix for worker-readable identity axes:
- userAgent/platform/language/timezone/hardwareConcurrency/deviceMemory:
  owned by browser context/CDP, worker bootstrap, or left real, so window and
  Worker stay aligned.
- WebGL vendor/renderer: page init scripts patch window/frame contexts and the
  Worker/SharedWorker wrapper prepends the same OffscreenCanvas-readable
  getParameter override before classic or module same-origin/blob/data worker
  code runs.
- UA-CH/chrome/plugins/screen/battery/fonts/media/performance/serviceWorker:
  window-facing browser API shape only; not used as declared worker identity in
  the fingerprint self-check oracle.
"""

_CLIENT_HINTS_JS = """
(() => {
    Object.defineProperty(navigator, 'userAgentData', {
        get: () => ({
            brands: %s,
            mobile: false,
            platform: %s,
            getHighEntropyValues: async (hints) => ({
                brands: %s,
                mobile: false,
                platform: %s,
                platformVersion: '%s',
                architecture: '%s',
                bitness: '%s',
                model: '',
                uaFullVersion: '%s',
            }),
        }),
    });
})();
"""

_TIMEZONE_JS = """
(() => {
    const tz = '%s';
    const origResolvedOptions = Intl.DateTimeFormat.prototype.resolvedOptions;
    Intl.DateTimeFormat.prototype.resolvedOptions = function() {
        const result = origResolvedOptions.call(this);
        result.timeZone = tz;
        return result;
    };
})();
"""

_WEBDRIVER_HIDE_JS = """
(() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    delete navigator.__proto__.webdriver;
    Object.defineProperty(navigator, 'languages', {
        get: () => ['%s', 'en'],
    });
})();
"""

_CHROMIUM_SHAPE_JS = """
(() => {
    Object.defineProperty(navigator, 'plugins', {
        get: () => [
            { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
            { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
            { name: 'Native Client', filename: 'internal-nacl-plugin' },
        ],
    });
    if (!window.chrome) {
        Object.defineProperty(window, 'chrome', {
            value: {runtime: {}}, configurable: false, enumerable: true,
        });
    }
})();
"""

_WEBRTC_PROXY_GUARD_JS = """
(() => {
    const NativePC = window.RTCPeerConnection || window.webkitRTCPeerConnection;
    if (!NativePC) return;
    const GuardedPC = function(config, constraints) {
        const safe = Object.assign({}, config || {}, {iceTransportPolicy: 'relay'});
        return new NativePC(safe, constraints);
    };
    GuardedPC.prototype = NativePC.prototype;
    Object.setPrototypeOf(GuardedPC, NativePC);
    window.RTCPeerConnection = GuardedPC;
    if (window.webkitRTCPeerConnection) window.webkitRTCPeerConnection = GuardedPC;
})();
"""

_AUDIO_STABILITY_JS = """
(() => {
    if (!window.AudioBuffer) return;
    const SEED = %d;
    const touched = new WeakSet();
    const original = AudioBuffer.prototype.getChannelData;
    AudioBuffer.prototype.getChannelData = function(channel) {
        const data = original.call(this, channel);
        if (!touched.has(this) && data.length) {
            const index = Math.abs(SEED) %% data.length;
            data[index] = data[index] + ((SEED %% 7) - 3) * 1e-8;
            touched.add(this);
        }
        return data;
    };
})();
"""

_WEB_API_SHAPE_JS = """
(() => {
    if (navigator.permissions && navigator.permissions.query) {
        const nativeQuery = navigator.permissions.query.bind(navigator.permissions);
        navigator.permissions.query = (descriptor) => {
            if (descriptor && descriptor.name === 'notifications' && window.Notification) {
                return Promise.resolve({state: Notification.permission, onchange: null});
            }
            return nativeQuery(descriptor);
        };
    }
    if (navigator.getBattery) {
        const battery = Promise.resolve({
            charging: true, chargingTime: 0, dischargingTime: Infinity, level: 1,
            addEventListener: () => {}, removeEventListener: () => {},
        });
        Object.defineProperty(navigator, 'getBattery', {value: () => battery});
    }
    if (navigator.mediaDevices && navigator.mediaDevices.enumerateDevices) {
        const enumerate = navigator.mediaDevices.enumerateDevices.bind(navigator.mediaDevices);
        navigator.mediaDevices.enumerateDevices = async () => await enumerate();
    }
    if (window.speechSynthesis && window.speechSynthesis.getVoices) {
        const getVoices = window.speechSynthesis.getVoices.bind(window.speechSynthesis);
        window.speechSynthesis.getVoices = () => getVoices().slice();
    }
})();
"""

_FONT_MASK_JS = """
(() => {
    const allowedFonts = new Set([
        'Arial', 'Verdana', 'Helvetica', 'Times New Roman', 'Georgia',
        'Courier New', 'Trebuchet MS', 'Impact', 'Comic Sans MS',
        'Segoe UI', 'Roboto', 'Open Sans', 'Noto Sans',
    ]);
    if (document.fonts && document.fonts.check) {
        const origCheck = document.fonts.check.bind(document.fonts);
        document.fonts.check = function(font, text) {
            const family = font.replace(/['"\\d.]+/g, '').trim().split(',')[0].trim();
            if (!allowedFonts.has(family)) return false;
            return origCheck(font, text);
        };
    }
})();
"""

_SCREEN_FINGERPRINT_JS = """
(() => {
    const width = %d;
    const height = %d;
    const availWidth = %d;
    const availHeight = %d;
    const colorDepth = %d;
    const pixelDepth = %d;
    Object.defineProperty(screen, 'width', {get: () => width, configurable: false});
    Object.defineProperty(screen, 'height', {get: () => height, configurable: false});
    Object.defineProperty(screen, 'availWidth', {get: () => availWidth, configurable: false});
    Object.defineProperty(screen, 'availHeight', {get: () => availHeight, configurable: false});
    Object.defineProperty(screen, 'colorDepth', {get: () => colorDepth, configurable: false});
    Object.defineProperty(screen, 'pixelDepth', {get: () => pixelDepth, configurable: false});
    Object.defineProperty(screen, 'orientation', {
        get: () => ({angle: width > height ? 90 : 0, type: width > height ? 'landscape-primary' : 'portrait-primary'}),
        configurable: false,
    });
})();
"""

_BATTERY_API_JS = """
(() => {
    const charging = %s;
    const chargingTime = %d;
    const dischargingTime = %d;
    const level = %.4f;
    const listeners = new Set();
    const battery = {
        charging,
        chargingTime,
        dischargingTime,
        level,
        addEventListener: (type, fn) => listeners.add(fn),
        removeEventListener: (type, fn) => listeners.delete(fn),
        set onchargingchange(fn) { this._onchargingchange = fn; },
        get onchargingchange() { return this._onchargingchange; },
        set onchargingtimechange(fn) { this._onchargingtimechange = fn; },
        get onchargingtimechange() { return this._onchargingtimechange; },
        set ondischargingtimechange(fn) { this._ondischargingtimechange = fn; },
        get ondischargingtimechange() { return this._ondischargingtimechange; },
        set onlevelchange(fn) { this._onlevelchange = fn; },
        get onlevelchange() { return this._onlevelchange; },
    };
    const promise = Promise.resolve(battery);
    Object.defineProperty(navigator, 'getBattery', {
        value: () => promise,
        configurable: false,
    });
})();
"""

_CDP_DETECTION_JS = """
(() => {
    if (window.cdc_adoQpoasnfa76pfcZLmcfl_Array) delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
    if (window.cdc_adoQpoasnfa76pfcZLmcfl_Promise) delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
    if (window.cdc_adoQpoasnfa76pfcZLmcfl_Serial) delete window.cdc_adoQpoasnfa76pfcZLmcfl_Serial;
    const origDefineProperty = Object.defineProperty;
    const cdpProps = ['cdc_adoQpoasnfa76pfcZLmcfl_Array', 'cdc_adoQpoasnfa76pfcZLmcfl_Promise', 'cdc_adoQpoasnfa76pfcZLmcfl_Serial', '$cdc_lk', '$chrome_async', '__selenium_unwrapped', '__driver_evaluate', '__webdriver_unwrapped', '__fxdriver_evaluate', '_Selenium_IDE_Recorder'];
    for (const prop of cdpProps) {
        try {
            origDefineProperty(window, prop, {
                get: () => undefined,
                set: () => {},
                configurable: false,
            });
        } catch (e) {}
    }
    const origQuerySelector = Document.prototype.querySelector;
    Document.prototype.querySelector = function(selector) {
        if (selector && (selector.includes('[debugger]') || selector.includes('$cdc') || selector.includes('$chrome_async'))) {
            return null;
        }
        return origQuerySelector.call(this, selector);
    };
    if (navigator.plugins) {
        const origRefresh = navigator.plugins.refresh;
        if (origRefresh) {
            navigator.plugins.refresh = function() {};
        }
    }
})();
"""

_PERFORMANCE_TIMING_JS = """
(() => {
    const OFFSET = %f;
    if (window.performance && performance.now) {
        const origNow = performance.now.bind(performance);
        performance.now = function() {
            return origNow() + OFFSET;
        };
    }
    if (window.performance && performance.timeOrigin) {
        const origTimeOrigin = performance.timeOrigin;
        Object.defineProperty(performance, 'timeOrigin', {
            get: () => origTimeOrigin - OFFSET,
            configurable: false,
        });
    }
    if (window.performance && performance.timing) {
        const origTiming = performance.timing;
        const patched = Object.create(origTiming);
        const fields = ['navigationStart', 'unloadEventStart', 'unloadEventEnd', 'redirectStart', 'redirectEnd', 'fetchStart', 'domainLookupStart', 'domainLookupEnd', 'connectStart', 'connectEnd', 'secureConnectionStart', 'requestStart', 'responseStart', 'responseEnd', 'domLoading', 'domInteractive', 'domContentLoadedEventStart', 'domContentLoadedEventEnd', 'domComplete', 'loadEventStart', 'loadEventEnd'];
        for (const field of fields) {
            const origValue = origTiming[field];
            if (typeof origValue === 'number' && origValue > 0) {
                Object.defineProperty(patched, field, {
                    get: () => origValue + Math.round(OFFSET),
                    configurable: false,
                });
            }
        }
        Object.defineProperty(performance, 'timing', {
            get: () => patched,
            configurable: false,
        });
    }
})();
"""

_SERVICE_WORKER_STUB_JS = """
(() => {
    if (!('serviceWorker' in navigator)) return;
    const origRegister = navigator.serviceWorker.register;
    navigator.serviceWorker.register = async function(scriptURL, options) {
        try {
            return await origRegister.call(this, scriptURL, options);
        } catch (e) {
            return {
                active: null,
                installing: null,
                waiting: null,
                scope: scriptURL,
                updateViaCache: 'all',
                addEventListener: () => {},
                removeEventListener: () => {},
                postMessage: () => {},
                update: async () => {},
                unregister: async () => true,
            };
        }
    };
    if (!navigator.serviceWorker.controller) {
        Object.defineProperty(navigator.serviceWorker, 'controller', {
            get: () => null,
            configurable: false,
        });
    }
    if (window.CacheStorage && CacheStorage.prototype.open) {
        const origOpen = CacheStorage.prototype.open;
        CacheStorage.prototype.open = async function(cacheName) {
            try {
                return await origOpen.call(this, cacheName);
            } catch (e) {
                return {
                    match: async () => undefined,
                    matchAll: async () => [],
                    add: async () => {},
                    addAll: async () => {},
                    put: async () => {},
                    delete: async () => true,
                    keys: async () => [],
                };
            }
        };
    }
})();
"""

_HEADER_ORDER_JS = """
(() => {
    const CHROMIUM_HEADER_ORDER = [
        'Host', 'Connection', 'sec-ch-ua', 'sec-ch-ua-mobile', 'sec-ch-ua-platform',
        'Upgrade-Insecure-Requests', 'User-Agent', 'Accept', 'Sec-Fetch-Site',
        'Sec-Fetch-Mode', 'Sec-Fetch-User', 'Sec-Fetch-Dest', 'Referer',
        'Accept-Encoding', 'Accept-Language',
    ];
    if (window.fetch) {
        const origFetch = window.fetch;
        window.fetch = function(input, init) {
            if (init && init.headers) {
                const headers = init.headers;
                if (headers instanceof Headers) {
                    const ordered = new Headers();
                    for (const name of CHROMIUM_HEADER_ORDER) {
                        const value = headers.get(name);
                        if (value !== null) ordered.set(name, value);
                    }
                    headers.forEach((value, name) => {
                        if (!ordered.has(name)) ordered.set(name, value);
                    });
                    init.headers = ordered;
                }
            }
            return origFetch.call(this, input, init);
        };
        if (window.__markNative) window.__markNative(window.fetch, 'fetch');
    }
})();
"""

_CONNECTION_ISOLATION_JS = """
(() => {
    if (window.fetch) {
        const origFetch = window.fetch;
        const connectionPool = new Map();
        window.fetch = function(input, init) {
            const url = typeof input === 'string' ? input : input.url;
            let origin;
            try {
                origin = new URL(url, location.href).origin;
            } catch (e) {
                origin = location.origin;
            }
            if (!connectionPool.has(origin)) {
                connectionPool.set(origin, {lastUsed: Date.now()});
            }
            const pool = connectionPool.get(origin);
            pool.lastUsed = Date.now();
            const headers = new Headers((init && init.headers) || {});
            if (!headers.has('Connection')) {
                headers.set('Connection', 'keep-alive');
            }
            const finalInit = Object.assign({}, init || {}, {headers});
            return origFetch.call(this, input, finalInit);
        };
        if (window.__markNative) window.__markNative(window.fetch, 'fetch');
    }
    if (window.XMLHttpRequest) {
        const origOpen = XMLHttpRequest.prototype.open;
        XMLHttpRequest.prototype.open = function(method, url) {
            origOpen.apply(this, arguments);
            try {
                this.setRequestHeader('Connection', 'keep-alive');
            } catch (e) {}
        };
        if (window.__markNative) window.__markNative(XMLHttpRequest.prototype.open, 'open');
    }
})();
"""

_NAVIGATOR_VENDOR_JS = """
(() => {
    const vendor = '%s';
    Object.defineProperty(navigator, 'vendor', {
        get: () => vendor,
        configurable: false,
    });
})();
"""

_NAVIGATOR_OSCPU_JS = """
(() => {
    const oscpu = '%s';
    Object.defineProperty(navigator, 'oscpu', {
        get: () => oscpu,
        configurable: false,
    });
})();
"""

# navigator.hardwareConcurrency / deviceMemory are intentionally NOT spoofed:
# an init-script override does not reach dedicated Workers, so faking them in the
# window realm only creates a window-vs-worker divergence (defect A5). See the
# note at the injection site in ``apply_stealth_hardening``.

_FONT_SPACING_JS = """
(() => {
    const SEED = %d;
    const origMeasureText = CanvasRenderingContext2D.prototype.measureText;
    CanvasRenderingContext2D.prototype.measureText = function(text) {
        const metrics = origMeasureText.call(this, text);
        const origWidth = metrics.width;
        const noise = (Math.sin(SEED * text.length) * 0.001);
        Object.defineProperty(metrics, 'width', {
            get: () => origWidth * (1 + noise),
            configurable: false,
        });
        return metrics;
    };
})();
"""

_FONT_LIST_JS = """
(() => {
    const fontList = %s;
    const allowedFonts = new Set(fontList);
    if (document.fonts && document.fonts.check) {
        const origCheck = document.fonts.check.bind(document.fonts);
        document.fonts.check = function(font, text) {
            const family = font.replace(/['"\\d.]+/g, '').trim().split(',')[0].trim();
            if (!allowedFonts.has(family)) return false;
            return origCheck(font, text);
        };
    }
    if (document.fonts && document.fonts.entries) {
        const origEntries = document.fonts.entries.bind(document.fonts);
        document.fonts.entries = function*() {
            for (const font of origEntries()) {
                if (allowedFonts.has(font.family)) {
                    yield font;
                }
            }
        };
    }
})();
"""

_SPEECH_VOICES_JS = """
(() => {
    const voices = %s;
    if (window.speechSynthesis) {
        const origGetVoices = window.speechSynthesis.getVoices.bind(window.speechSynthesis);
        window.speechSynthesis.getVoices = function() {
            const allVoices = origGetVoices();
            if (allVoices.length === 0) return [];
            const filtered = allVoices.filter(v => voices.includes(v.name));
            return filtered.length > 0 ? filtered : allVoices.slice(0, 5);
        };
    }
})();
"""

_WEBRTC_IPV6_JS = """
(() => {
    const NativePC = window.RTCPeerConnection || window.webkitRTCPeerConnection;
    if (!NativePC) return;
    const GuardedPC = function(config, constraints) {
        const safe = Object.assign({}, config || {}, {
            iceTransportPolicy: 'relay',
            iceServers: (config && config.iceServers || []).map(server => ({
                ...server,
                urls: Array.isArray(server.urls)
                    ? server.urls.filter(u => !u.includes('['))
                    : (typeof server.urls === 'string' && server.urls.includes('['))
                        ? []
                        : server.urls,
            })),
        });
        return new NativePC(safe, constraints);
    };
    GuardedPC.prototype = NativePC.prototype;
    Object.setPrototypeOf(GuardedPC, NativePC);
    window.RTCPeerConnection = GuardedPC;
    if (window.webkitRTCPeerConnection) window.webkitRTCPeerConnection = GuardedPC;
    if (navigator.mediaDevices && navigator.mediaDevices.enumerateDevices) {
        const origEnumerate = navigator.mediaDevices.enumerateDevices.bind(navigator.mediaDevices);
        navigator.mediaDevices.enumerateDevices = async function() {
            const devices = await origEnumerate();
            return devices.filter(d => d.deviceId !== 'default' || d.kind !== 'audiooutput');
        };
    }
})();
"""

_HAIRLINE_FIX_JS = """
(() => {
    const origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
    CanvasRenderingContext2D.prototype.getImageData = function(sx, sy, sw, sh) {
        const imageData = origGetImageData.call(this, sx, sy, sw, sh);
        for (let i = 0; i < imageData.data.length; i += 4) {
            if (imageData.data[i] === 0 && imageData.data[i+1] === 0 && imageData.data[i+2] === 0 && imageData.data[i+3] === 0) {
                continue;
            }
            const alpha = imageData.data[i+3];
            if (alpha > 0 && alpha < 255) {
                imageData.data[i+3] = Math.min(255, alpha + 1);
            }
        }
        return imageData;
    };
})();
"""

_ERROR_PROTOTYPE_JS = """
(() => {
    const origError = window.Error;
    const origTypeError = window.TypeError;
    const origRangeError = window.RangeError;
    const origReferenceError = window.ReferenceError;
    const origSyntaxError = window.SyntaxError;
    const origURIError = window.URIError;
    const origEvalError = window.EvalError;
    function sanitizeStack(error) {
        if (error && error.stack && typeof error.stack === 'string') {
            const lines = error.stack.split('\\n');
            const filtered = lines.filter(line =>
                !line.includes('playwright') &&
                !line.includes('nodriver') &&
                !line.includes('camoufox') &&
                !line.includes('cloakbrowser') &&
                !line.includes('__playwright') &&
                !line.includes('__pw') &&
                !line.includes('puppeteer') &&
                !line.includes('selenium') &&
                !line.includes('webdriver')
            );
            Object.defineProperty(error, 'stack', {
                get: () => filtered.join('\\n'),
                configurable: true,
            });
        }
        return error;
    }
    window.Error = function(message) {
        const error = new origError(message);
        sanitizeStack(error);
        return error;
    };
    window.Error.prototype = origError.prototype;
    window.Error.captureStackTrace = origError.captureStackTrace;
})();
"""

_CHROME_RUNTIME_JS = """
(() => {
    if (!window.chrome) {
        Object.defineProperty(window, 'chrome', {
            value: {},
            configurable: false,
            enumerable: true,
        });
    }
    if (!window.chrome.runtime) {
        Object.defineProperty(window.chrome, 'runtime', {
            value: {
                OnInstalledReason: {
                    CHROME_UPDATE: 'chrome_update',
                    INSTALL: 'install',
                    SHARED_MODULE_UPDATE: 'shared_module_update',
                    UPDATE: 'update',
                },
                OnRestartRequiredReason: {
                    APP_UPDATE: 'app_update',
                    OS_UPDATE: 'os_update',
                    PERIODIC: 'periodic',
                },
                PlatformArch: {
                    ARM: 'arm',
                    MIPS: 'mips',
                    MIPS64: 'mips64',
                    X86_32: 'x86-32',
                    X86_64: 'x86-64',
                },
                PlatformNaclArch: {
                    ARM: 'arm',
                    MIPS: 'mips',
                    MIPS64: 'mips64',
                    X86_32: 'x86-32',
                    X86_64: 'x86-64',
                },
                PlatformOs: {
                    ANDROID: 'android',
                    CROS: 'cros',
                    LINUX: 'linux',
                    MAC: 'mac',
                    OPENBSD: 'openbsd',
                    WIN: 'win',
                },
                RequestUpdateCheckStatus: {
                    NO_UPDATE: 'no_update',
                    THROTTLED: 'throttled',
                    UPDATE_AVAILABLE: 'update_available',
                },
                connect: function() { return { onDisconnect: { addListener: function() {} }, onMessage: { addListener: function() {} }, postMessage: function() {} }; },
                sendMessage: function() { if (arguments.length > 0 && typeof arguments[arguments.length - 1] === 'function') { arguments[arguments.length - 1](); } },
            },
            configurable: false,
            enumerable: true,
        });
    }
})();
"""

_IFRAME_WEBDRIVER_JS = """
(() => {
    const origCreateElement = document.createElement;
    document.createElement = function(tagName) {
        const element = origCreateElement.call(document, tagName);
        if (tagName.toLowerCase() === 'iframe') {
            const origContentWindow = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'contentWindow');
            if (origContentWindow && origContentWindow.get) {
                Object.defineProperty(element, 'contentWindow', {
                    get: function() {
                        const win = origContentWindow.get.call(this);
                        if (win) {
                            try {
                                Object.defineProperty(win.navigator, 'webdriver', {
                                    get: () => undefined,
                                    configurable: false,
                                });
                            } catch (e) {}
                        }
                        return win;
                    },
                    configurable: false,
                });
            }
        }
        return element;
    };
    const origContentWindow = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'contentWindow');
    if (origContentWindow && origContentWindow.get) {
        Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
            get: function() {
                const win = origContentWindow.get.call(this);
                if (win && win.navigator && 'webdriver' in win.navigator) {
                    try {
                        Object.defineProperty(win.navigator, 'webdriver', {
                            get: () => undefined,
                            configurable: false,
                        });
                    } catch (e) {}
                }
                return win;
            },
            configurable: false,
        });
    }
})();
"""

_STACK_TRACE_JS = """
(() => {
    const origPrepareStackTrace = Error.prepareStackTrace;
    Error.prepareStackTrace = function(error, stack) {
        const filtered = stack.filter(callSite => {
            const fileName = callSite.getFileName();
            if (!fileName) return true;
            return !fileName.includes('playwright') &&
                   !fileName.includes('nodriver') &&
                   !fileName.includes('camoufox') &&
                   !fileName.includes('cloakbrowser') &&
                   !fileName.includes('__playwright') &&
                   !fileName.includes('__pw') &&
                   !fileName.includes('puppeteer') &&
                   !fileName.includes('selenium') &&
                   !fileName.includes('webdriver');
        });
        if (origPrepareStackTrace) {
            return origPrepareStackTrace.call(this, error, filtered);
        }
        return error.toString();
    };
})();
"""

_GAMEPAD_JS = """
(() => {
    if (navigator.getGamepads) {
        const origGetGamepads = navigator.getGamepads.bind(navigator);
        navigator.getGamepads = function() {
            const gamepads = origGetGamepads();
            return gamepads || [null, null, null, null];
        };
    }
})();
"""

_MEDIA_DEVICES_JS = """
(() => {
    if (navigator.mediaDevices && navigator.mediaDevices.enumerateDevices) {
        const origEnumerate = navigator.mediaDevices.enumerateDevices.bind(navigator.mediaDevices);
        navigator.mediaDevices.enumerateDevices = async function() {
            const devices = await origEnumerate();
            const fakeDevices = [
                { deviceId: 'default', kind: 'audioinput', label: 'Default - Microphone (Internal)', groupId: 'group1' },
                { deviceId: 'comm', kind: 'audioinput', label: 'Communications - Microphone (Internal)', groupId: 'group1' },
                { deviceId: 'internal-mic', kind: 'audioinput', label: 'Microphone (Internal)', groupId: 'group1' },
                { deviceId: 'default', kind: 'audiooutput', label: 'Default - Speakers (Internal)', groupId: 'group2' },
                { deviceId: 'comm', kind: 'audiooutput', label: 'Communications - Speakers (Internal)', groupId: 'group2' },
                { deviceId: 'internal-speakers', kind: 'audiooutput', label: 'Speakers (Internal)', groupId: 'group2' },
                { deviceId: 'internal-camera', kind: 'videoinput', label: 'Internal Camera', groupId: 'group3' },
            ];
            return fakeDevices.map(d => {
                const obj = Object.create(MediaDeviceInfo.prototype);
                Object.defineProperties(obj, {
                    deviceId: { get: () => d.deviceId },
                    kind: { get: () => d.kind },
                    label: { get: () => d.label },
                    groupId: { get: () => d.groupId },
                    toJSON: { value: () => ({ deviceId: d.deviceId, kind: d.kind, label: d.label, groupId: d.groupId }) },
                });
                return obj;
            });
        };
    }
})();
"""

_STORAGE_ESTIMATE_JS = """
(() => {
    if (navigator.storage && navigator.storage.estimate) {
        const origEstimate = navigator.storage.estimate.bind(navigator.storage);
        navigator.storage.estimate = async function() {
            const estimate = await origEstimate();
            return {
                quota: estimate.quota || 274877906944,
                usage: estimate.usage || 1234567,
                usageDetails: estimate.usageDetails || {},
            };
        };
    }
})();
"""

_INTL_LOCALE_JS = """
(() => {
    const locale = '%s';
    const origResolvedOptions = Intl.DateTimeFormat.prototype.resolvedOptions;
    Intl.DateTimeFormat.prototype.resolvedOptions = function() {
        const result = origResolvedOptions.call(this);
        result.locale = locale;
        return result;
    };
    if (Intl.NumberFormat && Intl.NumberFormat.prototype.resolvedOptions) {
        const origNumberOptions = Intl.NumberFormat.prototype.resolvedOptions;
        Intl.NumberFormat.prototype.resolvedOptions = function() {
            const result = origNumberOptions.call(this);
            result.locale = locale;
            return result;
        };
    }
})();
"""

_CLIPBOARD_JS = """
(() => {
    if (navigator.clipboard) {
        const origWriteText = navigator.clipboard.writeText;
        if (origWriteText) {
            navigator.clipboard.writeText = async function(text) {
                return await origWriteText.call(this, text);
            };
        }
        const origReadText = navigator.clipboard.readText;
        if (origReadText) {
            navigator.clipboard.readText = async function() {
                try {
                    return await origReadText.call(this);
                } catch (e) {
                    return '';
                }
            };
        }
    }
})();
"""

_MUTATION_OBSERVER_JS = """
(() => {
    const origObserve = MutationObserver.prototype.observe;
    MutationObserver.prototype.observe = function(target, options) {
        const wrappedCallback = this._wrappedCallback;
        if (!wrappedCallback && this._callback) {
            this._wrappedCallback = (mutations, observer) => {
                const filteredMutations = mutations.filter(m => {
                    if (m.type === 'childList') {
                        const addedNodes = Array.from(m.addedNodes);
                        const hasScript = addedNodes.some(node =>
                            node.nodeType === 1 &&
                            node.tagName === 'SCRIPT' &&
                            node.textContent &&
                            (node.textContent.includes('stealth') ||
                             node.textContent.includes('hardening') ||
                             node.textContent.includes('canvas_seed'))
                        );
                        if (hasScript) return false;
                    }
                    return true;
                });
                if (filteredMutations.length > 0) {
                    this._callback(filteredMutations, observer);
                }
            };
        }
        return origObserve.call(this, target, options);
    };
})();
"""


async def apply_stealth_hardening(
    page: Any,
    *,
    canvas_seed: int = 1234,
    webgl_renderer: str = "ANGLE (Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0)",
    timezone: str = "America/New_York",
    locale: str = "en-US",
    sec_ch_ua: str = '"Chromium";v="131", "Not?A_Brand";v="24", "Google Chrome";v="131"',
    sec_ch_ua_platform: str = '"Windows"',
    chrome_version: str = "131.0.0.0",
    browser_family: str = "chromium",
    proxy_active: bool = False,
    viewport_width: int = 1920,
    viewport_height: int = 1080,
    screen_width: int = 1920,
    screen_height: int = 1080,
    battery_charging: bool = True,
    battery_level: float = 0.85,
    performance_offset: float = 0.0,
    hardware_concurrency: int = 8,
    device_memory: int = 8,
    navigator_vendor: str = "Google Inc.",
    navigator_oscpu: str = "Windows NT 10.0; Win64; x64",
    font_spacing_seed: int = 42,
    font_list: list[str] | None = None,
    speech_voices: list[str] | None = None,
    platform_version: str = "10.0.0",
    architecture: str = "x86",
    bitness: str = "64",
) -> None:
    """Apply comprehensive stealth hardening to a Playwright/nodriver page.

    Should be called AFTER basic stealth patches and BEFORE navigating
    to the target URL (use page.add_init_script or evaluate before goto).
    """
    try:
        brands_js = _sec_ch_ua_to_brands_js(sec_ch_ua)
        webgl_patch = _WEBGL_NOISE_JS % (
            _webgl_vendor_for_renderer(webgl_renderer).replace("'", "\\'"),
            webgl_renderer.replace("'", "\\'"),
        )
        worker_identity_patch = "\n".join(
            [
                webgl_patch,
                _WORKER_NAVIGATOR_JS
                % (
                    locale.replace("'", "\\'"),
                    json.dumps([locale, locale.split("-")[0]]),
                ),
            ]
        )
        scripts = [
            _NATIVE_TOSTRING_GUARD_JS,
            _CANVAS_NOISE_JS % canvas_seed,
            _AUDIO_STABILITY_JS % canvas_seed,
            webgl_patch,
            _WORKER_WEBGL_BOOTSTRAP_JS % json.dumps(worker_identity_patch),
            # Timezone is deliberately NOT patched in JS here (defect A3). The
            # old ``_TIMEZONE_JS`` overrode only ``Intl.DateTimeFormat`` and left
            # ``Date.getTimezoneOffset()`` reporting the host tz, a divergence
            # anti-bot systems check within a 90-minute tolerance. Timezone is
            # now owned by the context-level ``timezone_id`` option, which
            # Playwright applies via CDP to every realm (window + workers) so
            # Intl and Date stay coherent by construction.
            _WEBDRIVER_HIDE_JS % locale,
            _FONT_SPACING_JS % font_spacing_seed,
            _WEB_API_SHAPE_JS,
            # navigator.hardwareConcurrency / deviceMemory are deliberately NOT
            # patched in JS (defect A5, same class as the A3 timezone fix). An
            # ``add_init_script`` override runs in the window and child frames but
            # NOT in dedicated Workers, so a Worker reading navigator.* would
            # report the real value while the window reports the spoofed one - a
            # window-vs-worker divergence anti-bot systems cross-check. These are
            # benign scalars, so reporting the real value in every realm is more
            # coherent than faking a value we cannot enforce in workers.
            _NAVIGATOR_VENDOR_JS % navigator_vendor.replace("'", "\\'"),
            _NAVIGATOR_OSCPU_JS % navigator_oscpu.replace("'", "\\'"),
            _ERROR_PROTOTYPE_JS,
            _IFRAME_WEBDRIVER_JS,
            _STACK_TRACE_JS,
            _GAMEPAD_JS,
            _MEDIA_DEVICES_JS,
            _STORAGE_ESTIMATE_JS,
            _INTL_LOCALE_JS % locale,
            _CLIPBOARD_JS,
            _MUTATION_OBSERVER_JS,
            _HAIRLINE_FIX_JS,
        ]
        if font_list:
            import json as _json

            scripts.append(_FONT_LIST_JS % _json.dumps(font_list))
        else:
            scripts.append(_FONT_MASK_JS)
        if speech_voices:
            import json as _json

            scripts.append(_SPEECH_VOICES_JS % _json.dumps(speech_voices))
        if browser_family == "chromium":
            scripts.extend(
                [
                    _CLIENT_HINTS_JS
                    % (
                        brands_js,
                        sec_ch_ua_platform,
                        brands_js,
                        sec_ch_ua_platform,
                        platform_version,
                        architecture,
                        bitness,
                        chrome_version,
                    ),
                    _CHROMIUM_SHAPE_JS,
                    _HEADER_ORDER_JS,
                    _CONNECTION_ISOLATION_JS,
                    _CHROME_RUNTIME_JS,
                ]
            )
        if proxy_active:
            scripts.append(_WEBRTC_PROXY_GUARD_JS)
            scripts.append(_WEBRTC_IPV6_JS)
        scripts.append(
            _SCREEN_FINGERPRINT_JS
            % (
                screen_width,
                screen_height,
                screen_width,
                screen_height - 40,
                24,
                24,
            )
        )
        scripts.append(
            _BATTERY_API_JS
            % (
                "true" if battery_charging else "false",
                0 if battery_charging else 3600,
                0 if not battery_charging else 0,
                battery_level,
            )
        )
        scripts.append(_CDP_DETECTION_JS)
        scripts.append(_PERFORMANCE_TIMING_JS % performance_offset)
        scripts.append(_SERVICE_WORKER_STUB_JS)
        # Idempotency guard (defect A2): ``add_init_script`` registers a script
        # that Playwright re-runs on every navigation, so calling this function
        # twice for one page stacks two blobs. Mark the page after the first
        # successful injection and make any later call a no-op; whichever caller
        # runs first wins and defines the page's single coherent identity.
        if getattr(page, "_job_ftch_hardening_done", False):
            return

        combined = "\n".join(scripts)

        if hasattr(page, "add_init_script"):
            await page.add_init_script(combined)
        elif hasattr(page, "evaluate"):
            await page.evaluate(combined)
        else:
            logger.debug("stealth_hardening_no_script_injection", page_type=type(page).__name__)

        with contextlib.suppress(Exception):
            page._job_ftch_hardening_done = True

    except Exception as exc:
        logger.warning("stealth_hardening_failed", error=str(exc))


def _sec_ch_ua_to_brands_js(sec_ch_ua: str) -> str:
    """Convert Sec-CH-UA header string to JS brands array."""
    brands: list[str] = []
    for part in sec_ch_ua.split(","):
        part = part.strip()
        if ";v=" in part:
            name, _, version = part.partition(";v=")
            name = name.strip().strip('"')
            version = version.strip().strip('"')
            brands.append(f'{{brand:"{name}",version:"{version}"}}')
    return f"[{','.join(brands)}]" if brands else "[]"


async def apply_persona_hardening(
    page: Any,
    persona: Any,
    *,
    proxy_active: bool = False,
    performance_offset: float = 0.0,
) -> None:
    """Apply stealth hardening using a BrowserPersona's attributes."""
    await apply_stealth_hardening(
        page,
        canvas_seed=getattr(persona, "canvas_seed", 1234),
        webgl_renderer=getattr(persona, "webgl_renderer", ""),
        timezone=getattr(persona, "timezone", "America/New_York"),
        locale=getattr(persona, "locale", "en-US"),
        sec_ch_ua=getattr(persona, "sec_ch_ua", ""),
        sec_ch_ua_platform=getattr(persona, "sec_ch_ua_platform", '"Windows"'),
        chrome_version=getattr(persona, "browser_version", "131.0.0.0"),
        browser_family=getattr(persona, "browser_family", "chromium"),
        proxy_active=proxy_active,
        viewport_width=getattr(persona, "viewport_width", 1920),
        viewport_height=getattr(persona, "viewport_height", 1080),
        screen_width=getattr(persona, "screen_width", 1920),
        screen_height=getattr(persona, "screen_height", 1080),
        battery_charging=getattr(persona, "battery_charging", True),
        battery_level=getattr(persona, "battery_level", 0.85),
        performance_offset=performance_offset,
        hardware_concurrency=getattr(persona, "hardware_concurrency", 8),
        device_memory=getattr(persona, "device_memory", 8),
        navigator_vendor=getattr(persona, "navigator_vendor", "Google Inc."),
        navigator_oscpu=getattr(persona, "navigator_oscpu", "Windows NT 10.0; Win64; x64"),
        font_spacing_seed=getattr(persona, "font_spacing_seed", 42),
        font_list=getattr(persona, "font_list", None),
        speech_voices=getattr(persona, "speech_voices", None),
        platform_version=getattr(persona, "platform_version", "10.0.0"),
        architecture=getattr(persona, "architecture", "x86"),
        bitness=getattr(persona, "bitness", "64"),
    )
