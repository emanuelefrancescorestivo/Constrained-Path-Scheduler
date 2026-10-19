// The service worker (DECISIONS.md D33): what makes the app installable and lets it
// open without a network. Served from /sw.js so that it may answer for the whole
// site. It decides nothing about a plan: it only keeps copies.
//
// - The app's own files (styles, scripts, icons) and the offline page are kept at
//   install, and refreshed in the background each time they are used; so is the
//   live icon of each day and sky (/icon/..., D74) once it has been shown.
// - A plan's pages (/p/...) and the calendar's data are always asked of the server
//   first; a copy of the last answer is kept, on this device only, and shown when
//   there is no network. The page then says it is offline (pwa.js).
// - Anything else (the home page, forms sent, the review page) goes to the network
//   as if this worker were not here; a page that cannot be reached and has no copy
//   is the offline page.
// - A reminder pushed by the server (D38) is shown as a notification; tapping it
//   opens the plan, in a window already open if there is one.

const VERSION = "v5";
// The offline page in the language of the page that registered this worker
// (pwa.js passes it); a new language registers the worker again.
const LANG = new URL(self.location).searchParams.get("lang") || "en";
const OFFLINE = `/offline?lang=${encodeURIComponent(LANG)}`;
const SHELL = `shell-${VERSION}-${LANG}`;
const PAGES = "pages";
const KEEP_PAGES = 60; // a plan's pages kept for offline, the oldest dropped first
const SHELL_FILES = [
  OFFLINE,
  "/static/style.css",
  "/static/errors.js",
  "/static/motion.js",
  "/static/ui.js",
  "/static/app.js",
  "/static/calendar.js",
  "/static/i18n.js",
  "/static/pwa.js",
  "/static/icons/icon.svg",
  "/static/icons/icon-192.png",
  "/static/fonts/figtree-latin.woff2",
  "/static/fonts/bricolage-grotesque-latin.woff2",
  "/static/garden/seedling.svg",
  "/static/garden/sprout.svg",
  "/static/garden/vine.svg",
  "/static/garden/bud.svg",
  "/static/garden/flower.svg",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((cache) => cache.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((names) => Promise.all(names.filter((n) => n.startsWith("shell-") && n !== SHELL).map((n) => caches.delete(n))))
      .then(() => self.clients.claim()),
  );
});

// A page asks the worker to forget a plan's copies (after "Delete my plan").
self.addEventListener("message", (event) => {
  const token = event.data && event.data.forget;
  if (!token) return;
  event.waitUntil(
    caches.open(PAGES).then(async (cache) => {
      for (const request of await cache.keys()) {
        if (new URL(request.url).pathname.startsWith(`/p/${token}`)) await cache.delete(request);
      }
    }),
  );
});

async function keep(request, response) {
  const cache = await caches.open(PAGES);
  await cache.put(request, response);
  const kept = await cache.keys();
  for (const old of kept.slice(0, Math.max(0, kept.length - KEEP_PAGES))) await cache.delete(old);
}

async function networkFirst(request, isPage) {
  try {
    const response = await fetch(request);
    if (response.ok) keep(request, response.clone());
    return response;
  } catch {
    const copy = await caches.match(request, { ignoreVary: true });
    if (copy) return copy;
    if (isPage) return (await caches.match(OFFLINE)) || Response.error();
    return Response.error();
  }
}

async function staleWhileRevalidate(request) {
  const cache = await caches.open(SHELL);
  const copy = await cache.match(request);
  const fresh = fetch(request)
    .then((response) => {
      if (response.ok) cache.put(request, response.clone());
      return response;
    })
    .catch(() => copy);
  return copy || fresh;
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/static/") || url.pathname.startsWith("/icon/")) {
    event.respondWith(staleWhileRevalidate(request));
  } else if (url.pathname.startsWith("/p/")) {
    event.respondWith(networkFirst(request, request.mode === "navigate"));
  } else if (request.mode === "navigate") {
    event.respondWith(fetch(request).catch(async () => (await caches.match(OFFLINE)) || Response.error()));
  }
});

// -- reminders (D38) ---------------------------------------------------------------

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { body: event.data ? event.data.text() : "" };
  }
  event.waitUntil(
    self.registration.showNotification(data.title || "Study plan", {
      body: data.body || "",
      tag: data.tag || "",
      icon: "/static/icons/icon-192.png",
      data: { url: data.url || "/" },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  let url = new URL((event.notification.data && event.notification.data.url) || "/", self.location.origin);
  if (url.origin !== self.location.origin) url = new URL("/", self.location.origin); // only this site's pages
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((windows) => {
      const open = windows.find((w) => new URL(w.url).pathname.startsWith(url.pathname) && "focus" in w);
      return open ? open.focus() : self.clients.openWindow(url.href);
    }),
  );
});
