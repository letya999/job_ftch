from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>bot parity lab</title>
  <link rel="preload" href="/static/app.js" as="script">
  <link rel="stylesheet" href="/static/style.css">
  <link rel="icon" href="/favicon.ico">
</head>
<body>
  <main>
    <h1>bot parity lab</h1>
    <form id="decoy" action="/captcha/submit" method="post" autocomplete="off" aria-hidden="true">
      <input name="company" tabindex="-1" style="position:absolute;left:-10000px" value="">
    </form>
    <button id="go" type="button">Run</button>
    <pre id="out">collecting...</pre>
  </main>
  <img src="/pixel?slot=html" width="1" height="1" alt="">
  <script src="/static/app.js"></script>
</body>
</html>
"""


STYLE = """
@font-face {
  font-family: LabProbe;
  src: url('/static/lab.woff2') format('woff2');
}
html { color-scheme: light dark; font-family: system-ui, sans-serif; }
body { margin: 0; min-height: 1600px; padding: 32px; }
main { max-width: 820px; margin: auto; }
pre { white-space: pre-wrap; border: 1px solid #9994; padding: 12px; }
"""


APP_JS = r"""
const interaction = {
  pointermove: 0,
  mousemove: 0,
  click: 0,
  keydown: 0,
  scroll: 0,
  focus: document.hasFocus(),
  firstEventMs: 0,
  lastEventMs: 0,
  pointerTrail: [],
  mouseTrail: [],
  keyTrail: [],
  scrollTrail: []
};

function markInteraction(kind, event) {
  const now = performance.now();
  interaction[kind] = (interaction[kind] || 0) + 1;
  if (!interaction.firstEventMs) interaction.firstEventMs = now;
  interaction.lastEventMs = now;
  if ((kind === 'pointermove' || kind === 'mousemove') && event) {
    const trail = kind === 'pointermove' ? interaction.pointerTrail : interaction.mouseTrail;
    trail.push({x: Math.round(event.clientX || 0), y: Math.round(event.clientY || 0), t: Math.round(now)});
    if (trail.length > 80) trail.shift();
  }
  if (kind === 'keydown' && event) {
    interaction.keyTrail.push({key: event.key || '', code: event.code || '', t: Math.round(now)});
    if (interaction.keyTrail.length > 30) interaction.keyTrail.shift();
  }
  if (kind === 'scroll') {
    interaction.scrollTrail.push({x: Math.round(scrollX), y: Math.round(scrollY), t: Math.round(now)});
    if (interaction.scrollTrail.length > 30) interaction.scrollTrail.shift();
  }
}

for (const type of ['pointermove', 'mousemove', 'click', 'keydown', 'scroll']) {
  addEventListener(type, (event) => markInteraction(type, event), {passive: true});
}
addEventListener('focus', () => { interaction.focus = true; });

function hashString(value) {
  let hash = 2166136261;
  const text = String(value || '');
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, '0');
}

function readWebGL() {
  try {
    const canvas = document.createElement('canvas');
    const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
    if (!gl) return {vendor: '', renderer: '', extensions: []};
    const dbg = gl.getExtension('WEBGL_debug_renderer_info');
    return {
      vendor: dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR),
      renderer: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER),
      version: gl.getParameter(gl.VERSION) || '',
      shadingLanguageVersion: gl.getParameter(gl.SHADING_LANGUAGE_VERSION) || '',
      maxTextureSize: gl.getParameter(gl.MAX_TEXTURE_SIZE) || 0,
      extensions: gl.getSupportedExtensions() || []
    };
  } catch (e) {
    return {vendor: '', renderer: '', extensions: [], error: String(e)};
  }
}

function readCanvas() {
  try {
    const canvas = document.createElement('canvas');
    canvas.width = 240;
    canvas.height = 60;
    const ctx = canvas.getContext('2d');
    ctx.textBaseline = 'top';
    ctx.fillStyle = '#f60';
    ctx.fillRect(0, 0, 80, 28);
    ctx.fillStyle = '#069';
    ctx.font = '16px Arial';
    ctx.fillText('Bot parity 0123456789', 4, 6);
    ctx.globalCompositeOperation = 'multiply';
    ctx.fillStyle = 'rgba(120, 200, 80, 0.7)';
    ctx.arc(56, 32, 24, 0, Math.PI * 2);
    ctx.fill();
    return {hash: hashString(canvas.toDataURL()), length: canvas.toDataURL().length};
  } catch (e) {
    return {hash: '', length: 0, error: String(e)};
  }
}

async function readAudio() {
  try {
    const AudioCtx = window.OfflineAudioContext || window.webkitOfflineAudioContext;
    if (!AudioCtx) return {hash: '', supported: false};
    const ctx = new AudioCtx(1, 5000, 44100);
    const osc = ctx.createOscillator();
    const comp = ctx.createDynamicsCompressor();
    osc.type = 'triangle';
    osc.frequency.value = 10000;
    comp.threshold.value = -50;
    comp.knee.value = 40;
    comp.ratio.value = 12;
    comp.attack.value = 0;
    comp.release.value = 0.25;
    osc.connect(comp);
    comp.connect(ctx.destination);
    osc.start(0);
    const buffer = await ctx.startRendering();
    const samples = buffer.getChannelData(0).slice(0, 256);
    return {hash: hashString(Array.from(samples).map((v) => v.toFixed(5)).join(',')), supported: true};
  } catch (e) {
    return {hash: '', supported: false, error: String(e)};
  }
}

function readFonts() {
  const base = document.createElement('span');
  base.style.cssText = 'position:absolute;left:-9999px;font-size:72px;';
  base.textContent = 'mmmmmmmmmmlli';
  document.body.appendChild(base);
  const families = ['Arial', 'Times New Roman', 'Segoe UI', 'Roboto', 'Apple Color Emoji', 'Noto Color Emoji', 'LabProbe'];
  const widths = {};
  for (const family of families) {
    base.style.fontFamily = `"${family}", monospace`;
    widths[family] = {width: base.offsetWidth, height: base.offsetHeight};
  }
  base.remove();
  return widths;
}

async function readPermissions() {
  const names = ['notifications', 'clipboard-read', 'clipboard-write', 'geolocation', 'camera', 'microphone'];
  const out = {};
  if (!navigator.permissions || !navigator.permissions.query) return {supported: false, values: out};
  for (const name of names) {
    try {
      out[name] = (await navigator.permissions.query({name})).state;
    } catch (e) {
      out[name] = 'error:' + String(e).slice(0, 80);
    }
  }
  return {supported: true, values: out};
}

async function readStorage() {
  const out = {};
  try { localStorage.setItem('__bot_parity_lab', '1'); out.localStorage = localStorage.getItem('__bot_parity_lab') === '1'; } catch (_) { out.localStorage = false; }
  try { sessionStorage.setItem('__bot_parity_lab', '1'); out.sessionStorage = sessionStorage.getItem('__bot_parity_lab') === '1'; } catch (_) { out.sessionStorage = false; }
  try {
    let sid = localStorage.getItem('__bot_parity_sid');
    if (!sid) {
      sid = String(Date.now()) + '-' + Math.random().toString(16).slice(2);
      localStorage.setItem('__bot_parity_sid', sid);
    }
    const visits = Number(localStorage.getItem('__bot_parity_visits') || '0') + 1;
    localStorage.setItem('__bot_parity_visits', String(visits));
    out.history = {sidHash: hashString(sid), visits};
  } catch (e) {
    out.history = {error: String(e)};
  }
  try { out.indexedDB = !!window.indexedDB; } catch (_) { out.indexedDB = false; }
  try { out.estimate = navigator.storage && navigator.storage.estimate ? await navigator.storage.estimate() : null; } catch (e) { out.estimate = {error: String(e)}; }
  return out;
}

async function readUAData() {
  if (!navigator.userAgentData) return null;
  const low = {
    brands: navigator.userAgentData.brands || [],
    mobile: navigator.userAgentData.mobile,
    platform: navigator.userAgentData.platform || ''
  };
  try {
    const high = await navigator.userAgentData.getHighEntropyValues([
      'architecture', 'bitness', 'model', 'platformVersion', 'uaFullVersion',
      'fullVersionList', 'wow64'
    ]);
    return {...low, high};
  } catch (e) {
    return {...low, highError: String(e)};
  }
}

function readNativeIntegrity() {
  const fnToString = Function.prototype.toString;
  const webdriverDesc = Object.getOwnPropertyDescriptor(Navigator.prototype, 'webdriver');
  const globals = Object.getOwnPropertyNames(window).filter((name) =>
    /playwright|puppeteer|selenium|webdriver|cdc_/i.test(name)
  );
  let stack = '';
  try { throw new Error('lab_stack_probe'); } catch (e) { stack = String(e && e.stack || e); }
  return {
    functionToStringNative: /\[native code\]/.test(fnToString.call(fnToString)),
    fetchNative: /\[native code\]/.test(fnToString.call(window.fetch)),
    webdriverDescriptor: webdriverDesc ? {
      enumerable: webdriverDesc.enumerable,
      configurable: webdriverDesc.configurable,
      hasGetter: typeof webdriverDesc.get === 'function',
      valueType: typeof webdriverDesc.value
    } : null,
    automationGlobals: globals,
    errorStackHash: hashString(stack),
    errorStackSample: stack.slice(0, 500)
  };
}

function readChromeShape() {
  return {
    hasChrome: !!window.chrome,
    keys: window.chrome ? Object.keys(window.chrome).sort() : [],
    hasRuntime: !!(window.chrome && window.chrome.runtime),
    runtimeType: typeof (window.chrome && window.chrome.runtime)
  };
}

async function readIframeRealm() {
  return new Promise((resolve) => {
    try {
      const iframe = document.createElement('iframe');
      iframe.style.display = 'none';
      iframe.src = 'about:blank';
      iframe.onload = () => {
        try {
          const w = iframe.contentWindow;
          const nav = w.navigator;
          resolve({
            userAgent: nav.userAgent || '',
            webdriver: nav.webdriver,
            platform: nav.platform || '',
            language: nav.language || '',
            languages: Array.from(nav.languages || []),
            hardwareConcurrency: nav.hardwareConcurrency || 0,
            deviceMemory: nav.deviceMemory || 0,
            timezone: w.Intl.DateTimeFormat().resolvedOptions().timeZone || '',
            vendor: nav.vendor || ''
          });
        } catch (e) {
          resolve({error: String(e)});
        } finally {
          iframe.remove();
        }
      };
      document.body.appendChild(iframe);
    } catch (e) {
      resolve({error: String(e)});
    }
  });
}

function readDeviceAndSensors() {
  const css = (query) => {
    try { return matchMedia(query).matches; } catch (_) { return false; }
  };
  return {
    touchEvent: 'TouchEvent' in window,
    pointerEvent: 'PointerEvent' in window,
    coarsePointer: css('(pointer: coarse)'),
    finePointer: css('(pointer: fine)'),
    hover: css('(hover: hover)'),
    anyHover: css('(any-hover: hover)'),
    orientationType: screen.orientation ? screen.orientation.type : '',
    orientationAngle: screen.orientation ? screen.orientation.angle : 0,
    deviceOrientationEvent: 'DeviceOrientationEvent' in window,
    deviceMotionEvent: 'DeviceMotionEvent' in window,
    ambientLightSensor: 'AmbientLightSensor' in window,
    accelerometer: 'Accelerometer' in window,
    gyroscope: 'Gyroscope' in window,
    magnetometer: 'Magnetometer' in window,
    connection: navigator.connection ? {
      effectiveType: navigator.connection.effectiveType || '',
      rtt: navigator.connection.rtt || 0,
      downlink: navigator.connection.downlink || 0,
      saveData: !!navigator.connection.saveData
    } : null
  };
}

function readDomAndCaptchaTripwires() {
  const decoy = document.querySelector('#decoy input[name="company"]');
  return {
    decoyValueLength: decoy ? decoy.value.length : -1,
    activeElementTag: document.activeElement ? document.activeElement.tagName : '',
    activeElementId: document.activeElement ? document.activeElement.id : '',
    visibilityState: document.visibilityState,
    hidden: document.hidden,
    hasFocus: document.hasFocus(),
    captchaTokenPresent: !!sessionStorage.getItem('__bot_parity_captcha_token')
  };
}

async function readMediaDevices() {
  try {
    const api = navigator.mediaDevices;
    if (!api || !api.enumerateDevices) return {supported: false, devices: []};
    const devices = await api.enumerateDevices();
    return {
      supported: true,
      count: devices.length,
      kinds: devices.map((device) => device.kind).sort()
    };
  } catch (e) {
    return {supported: false, error: String(e)};
  }
}

async function readNavigator() {
  const wg = readWebGL();
  let tz = '';
  try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (_) {}
  return {
    userAgent: navigator.userAgent || '',
    webdriver: navigator.webdriver,
    platform: navigator.platform || '',
    language: navigator.language || '',
    languages: Array.from(navigator.languages || []),
    hardwareConcurrency: navigator.hardwareConcurrency || 0,
    deviceMemory: navigator.deviceMemory || 0,
    timezone: tz,
    vendor: navigator.vendor || '',
    cookieEnabled: navigator.cookieEnabled,
    maxTouchPoints: navigator.maxTouchPoints || 0,
    plugins: Array.from(navigator.plugins || []).map((plugin) => plugin.name),
    mimeTypes: Array.from(navigator.mimeTypes || []).map((mime) => mime.type),
    userAgentData: await readUAData(),
    permissions: await readPermissions(),
    storage: await readStorage(),
    mediaDevices: await readMediaDevices(),
    deviceAndSensors: readDeviceAndSensors(),
    domTripwires: readDomAndCaptchaTripwires(),
    canvas: readCanvas(),
    audio: await readAudio(),
    fonts: readFonts(),
    chromeShape: readChromeShape(),
    nativeIntegrity: readNativeIntegrity(),
    userActivation: navigator.userActivation ? {
      isActive: navigator.userActivation.isActive,
      hasBeenActive: navigator.userActivation.hasBeenActive
    } : null,
    webdriverOwnDescriptor: Object.getOwnPropertyDescriptor(navigator, 'webdriver') || null,
    batterySupported: !!navigator.getBattery,
    clipboardSupported: !!navigator.clipboard,
    webRTCSupported: !!window.RTCPeerConnection,
    crossOriginIsolated,
    isSecureContext,
    screen: {
      width: screen.width,
      height: screen.height,
      availWidth: screen.availWidth,
      availHeight: screen.availHeight,
      colorDepth: screen.colorDepth
    },
    viewport: {width: innerWidth, height: innerHeight, dpr: devicePixelRatio},
    webglVendor: wg.vendor,
    webglRenderer: wg.renderer,
    webgl: wg
  };
}

const WORKER_SRC = `
self.onmessage = function () {
  var tz = '';
  try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (_) {}
  var wg = {vendor: '', renderer: ''};
  try {
    var oc = new OffscreenCanvas(1, 1);
    var gl = oc.getContext('webgl');
    if (gl) {
      var dbg = gl.getExtension('WEBGL_debug_renderer_info');
      wg.vendor = dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR);
      wg.renderer = dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
    }
  } catch (_) {}
  postMessage({
    userAgent: navigator.userAgent || '',
    platform: navigator.platform || '',
    language: navigator.language || '',
    languages: Array.from(navigator.languages || []),
    hardwareConcurrency: navigator.hardwareConcurrency || 0,
    deviceMemory: navigator.deviceMemory || 0,
    timezone: tz,
    webglVendor: wg.vendor,
    webglRenderer: wg.renderer
  });
};
`;

function collectWorker(options) {
  return new Promise((resolve) => {
    try {
      const blob = new Blob([WORKER_SRC], {type: 'application/javascript'});
      const worker = new Worker(URL.createObjectURL(blob), options || {});
      const timer = setTimeout(() => resolve({error: 'worker_timeout'}), 3000);
      worker.onmessage = (ev) => { clearTimeout(timer); resolve(ev.data); };
      worker.onerror = () => { clearTimeout(timer); resolve({error: 'worker_error'}); };
      worker.postMessage('go');
    } catch (e) {
      resolve({error: String(e)});
    }
  });
}

async function collectServiceWorker() {
  if (!('serviceWorker' in navigator)) return {supported: false};
  try {
    const reg = await navigator.serviceWorker.register('/sw.js', {scope: '/'});
    await navigator.serviceWorker.ready;
    const sw = reg.active || reg.waiting || reg.installing;
    if (!sw) return {supported: true, error: 'no_active_service_worker'};
    return await new Promise((resolve) => {
      const channel = new MessageChannel();
      const timer = setTimeout(() => resolve({supported: true, error: 'service_worker_timeout'}), 3000);
      channel.port1.onmessage = (ev) => { clearTimeout(timer); resolve(ev.data); };
      sw.postMessage({kind: 'probe'}, [channel.port2]);
    });
  } catch (e) {
    return {supported: true, error: String(e)};
  }
}

async function collectSharedWorker() {
  if (!('SharedWorker' in window)) return {supported: false};
  try {
    return await new Promise((resolve) => {
      const worker = new SharedWorker('/shared-worker.js');
      const timer = setTimeout(() => resolve({supported: true, error: 'shared_worker_timeout'}), 3000);
      worker.port.onmessage = (ev) => { clearTimeout(timer); resolve(ev.data); };
      worker.port.start();
      worker.port.postMessage({kind: 'probe'});
    });
  } catch (e) {
    return {supported: true, error: String(e)};
  }
}

async function postJSON(url, payload) {
  await fetch(url, {
    method: 'POST',
    headers: {'content-type': 'application/json'},
    body: JSON.stringify(payload),
    credentials: 'same-origin'
  });
}

async function runProbe() {
  const start = performance.now();
  const windowBag = await readNavigator();
  const workerBag = await collectWorker();
  const moduleWorkerBag = await collectWorker({type: 'module'});
  const serviceWorkerBag = await collectServiceWorker();
  const sharedWorkerBag = await collectSharedWorker();
  await fetch('/api/data?phase=warm', {credentials: 'same-origin'});
  await fetch('/api/echo-headers?phase=echo', {credentials: 'same-origin'});
  await fetch('/redirect?phase=redirect', {credentials: 'same-origin'});
  await fetch('/captcha/challenge?phase=issued', {credentials: 'same-origin'});
  new Image().src = '/favicon.ico?slot=js&ts=' + encodeURIComponent(String(Date.now()));
  await postJSON('/api/events', {
    kind: 'browser_probe',
    ts: Date.now(),
    elapsedMs: performance.now() - start,
    window: windowBag,
    iframe: await readIframeRealm(),
    worker: workerBag,
    moduleWorker: moduleWorkerBag,
    serviceWorker: serviceWorkerBag,
    sharedWorker: sharedWorkerBag,
    interaction: {...interaction},
    performance: {
      navType: performance.getEntriesByType('navigation')[0]?.type || '',
      resourceCount: performance.getEntriesByType('resource').length,
      resources: performance.getEntriesByType('resource').map((entry) => ({
        name: entry.name.replace(location.origin, ''),
        initiatorType: entry.initiatorType,
        duration: Math.round(entry.duration * 1000) / 1000,
        transferSize: entry.transferSize || 0
      })).slice(-30)
    }
  });
  new Image().src = '/pixel?slot=js&ts=' + encodeURIComponent(String(Date.now()));
  document.getElementById('out').textContent = JSON.stringify({window: windowBag, worker: workerBag, moduleWorker: moduleWorkerBag}, null, 2);
}

document.getElementById('go').addEventListener('click', runProbe);
runProbe().catch((error) => {
  document.getElementById('out').textContent = String(error);
});
"""

SW_JS = r"""
self.addEventListener('install', (event) => event.waitUntil(self.skipWaiting()));
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));
self.addEventListener('message', (event) => {
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || '';
  event.ports[0].postMessage({
    supported: true,
    userAgent: navigator.userAgent || '',
    platform: navigator.platform || '',
    language: navigator.language || '',
    languages: Array.from(navigator.languages || []),
    hardwareConcurrency: navigator.hardwareConcurrency || 0,
    deviceMemory: navigator.deviceMemory || 0,
    timezone: tz
  });
});
"""


SHARED_WORKER_JS = r"""
self.onconnect = (event) => {
  const port = event.ports[0];
  port.onmessage = () => {
    let tz = '';
    try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (_) {}
    port.postMessage({
      supported: true,
      userAgent: navigator.userAgent || '',
      platform: navigator.platform || '',
      language: navigator.language || '',
      languages: Array.from(navigator.languages || []),
      hardwareConcurrency: navigator.hardwareConcurrency || 0,
      deviceMemory: navigator.deviceMemory || 0,
      timezone: tz
    });
  };
  port.start();
};
"""


@dataclass(slots=True)
class RequestRecord:
    seq: int
    ts: float
    method: str
    request_version: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    header_order: list[str]
    client_host: str
    client_port: int
    body: str


@dataclass(slots=True)
class BrowserEvent:
    seq: int
    ts: float
    payload: dict[str, Any]


@dataclass(slots=True)
class Collector:
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _seq: int = 0
    requests: list[RequestRecord] = field(default_factory=list)
    events: list[BrowserEvent] = field(default_factory=list)

    def next_seq(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    def record_request(self, record: RequestRecord) -> None:
        with self._lock:
            self.requests.append(record)

    def record_event(self, event: BrowserEvent) -> None:
        with self._lock:
            self.events.append(event)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "requests": [asdict(record) for record in self.requests],
                "events": [asdict(event) for event in self.events],
            }


class LabHandler(BaseHTTPRequestHandler):
    collector: Collector

    protocol_version = "HTTP/1.1"
    server_version = "BotParityLab/0.1"

    def log_message(self, format: str, *args: object) -> None:
        return

    def handle(self) -> None:
        try:
            super().handle()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return

    def do_GET(self) -> None:
        self._record()
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_text(HTML, "text/html; charset=utf-8")
        elif parsed.path == "/static/app.js":
            self._send_text(APP_JS, "application/javascript; charset=utf-8")
        elif parsed.path == "/sw.js":
            self._send_text(SW_JS, "application/javascript; charset=utf-8")
        elif parsed.path == "/shared-worker.js":
            self._send_text(SHARED_WORKER_JS, "application/javascript; charset=utf-8")
        elif parsed.path == "/static/style.css":
            self._send_text(STYLE, "text/css; charset=utf-8")
        elif parsed.path == "/static/lab.woff2":
            self._send_bytes(b"", "font/woff2")
        elif parsed.path == "/favicon.ico":
            self._send_bytes(
                b"\x00\x00\x01\x00\x01\x00\x01\x01\x00\x00\x01\x00 \x00(\x00\x00\x00"
                b"\x16\x00\x00\x00(\x00\x00\x00\x01\x00\x00\x00\x02\x00\x00\x00"
                b"\x01\x00 \x00\x00\x00\x00\x00\x08\x00\x00\x00\x00\x00\x00\x00"
                b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00",
                "image/x-icon",
            )
        elif parsed.path == "/api/data":
            self._send_json({"ok": True, "items": [{"id": 1, "title": "local vacancy"}]})
        elif parsed.path == "/api/echo-headers":
            self._send_json(
                {"headers": {key.lower(): value for key, value in self.headers.items()}}
            )
        elif parsed.path == "/redirect":
            self.send_response(302)
            self.send_header("location", "/api/data?phase=redirect-final")
            self.send_header("cache-control", "no-store")
            self.send_header("content-length", "0")
            self.end_headers()
        elif parsed.path == "/captcha/challenge":
            self._send_json({"ok": True, "kind": "honeypot", "interactive": False})
        elif parsed.path == "/pixel":
            self._send_bytes(
                b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;",
                "image/gif",
            )
        elif parsed.path == "/robots.txt":
            self._send_text("User-agent: *\nAllow: /\n", "text/plain; charset=utf-8")
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        body = self._record()
        parsed = urlparse(self.path)
        if parsed.path == "/captcha/submit":
            self._send_json({"ok": False, "honeypot": True})
            return
        if parsed.path == "/api/events":
            try:
                payload = json.loads(body or "{}")
            except json.JSONDecodeError:
                payload = {"parse_error": True, "raw": body}
            self.collector.record_event(
                BrowserEvent(seq=self.collector.next_seq(), ts=time.time(), payload=payload)
            )
            self._send_json({"ok": True})
        else:
            self.send_error(404)

    def _record(self) -> str:
        parsed = urlparse(self.path)
        length = int(self.headers.get("content-length", "0") or "0")
        body = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""
        headers = {key.lower(): value for key, value in self.headers.items()}
        header_order = [key.lower() for key, _value in getattr(self.headers, "_headers", [])]
        self.collector.record_request(
            RequestRecord(
                seq=self.collector.next_seq(),
                ts=time.time(),
                method=self.command,
                request_version=self.request_version,
                path=parsed.path,
                query=parse_qs(parsed.query),
                headers=headers,
                header_order=header_order,
                client_host=str(self.client_address[0]),
                client_port=int(self.client_address[1]),
                body=body[:4096],
            )
        )
        return body

    def _send_text(self, text: str, content_type: str) -> None:
        self._send_bytes(text.encode("utf-8"), content_type)

    def _send_json(self, payload: dict[str, Any]) -> None:
        self._send_text(json.dumps(payload, sort_keys=True), "application/json; charset=utf-8")

    def _send_bytes(self, data: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("content-type", content_type)
        self.send_header("cache-control", "no-store")
        self.send_header(
            "accept-ch",
            "Sec-CH-UA, Sec-CH-UA-Platform, Sec-CH-UA-Mobile, "
            "Sec-CH-UA-Full-Version-List, Sec-CH-UA-Arch, Sec-CH-UA-Bitness, "
            "Sec-CH-UA-Platform-Version, Sec-CH-UA-Model, Sec-CH-UA-WoW64",
        )
        self.send_header(
            "permissions-policy",
            "ch-ua-high-entropy-values=(self), "
            "geolocation=(), camera=(), microphone=(), payment=()",
        )
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class LabServer:
    def __init__(self) -> None:
        self.collector = Collector()
        handler = type("_BoundLabHandler", (LabHandler,), {"collector": self.collector})
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}/"

    def __enter__(self) -> LabServer:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
