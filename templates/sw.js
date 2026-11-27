// QopenCRM: service worker mínimo. Los datos van siempre a la red; solo se cachean los estáticos.
const CACHE = "scrm-static-v2";
// Sin cobertura, la app enseña su propia pantalla (no el error del navegador) y reintenta sola al volver la red.
const OFFLINE = `<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#12141a"><title>Sin conexión · QopenCRM</title>
<body style="margin:0;min-height:100dvh;display:grid;place-items:center;background:#0f1115;color:#e8eaf0;font:300 16px system-ui,sans-serif;text-align:center">
<div style="padding:28px;max-width:320px"><div style="font-size:44px;line-height:1">⌁</div><h1 style="font-weight:600;font-size:24px;margin:14px 0 8px">Sin conexión</h1>
<p style="color:#9aa3b5;margin:0 0 22px">No hay red ahora mismo. En cuanto vuelva, el CRM se abre solo.</p>
<button onclick="location.reload()" style="background:#4f46e5;color:#fff;border:0;border-radius:999px;padding:13px 26px;font:500 15px system-ui">Reintentar</button></div>
<script>addEventListener("online",()=>location.reload())</script></body></html>`;
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim())));
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.mode === "navigate") {
    e.respondWith(fetch(e.request).catch(() => new Response(OFFLINE, { status: 503, headers: { "Content-Type": "text/html; charset=utf-8" } })));
    return;
  }
  if (e.request.method !== "GET" || url.origin !== location.origin || !url.pathname.startsWith("/static/")) return;
  e.respondWith(caches.open(CACHE).then(c => fetch(e.request).then(r => { c.put(e.request, r.clone()); return r; }).catch(() => c.match(e.request))));
});
