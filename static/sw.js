/* RoomRoller service worker: makes the installed app start instantly.
   Only the app's own files and the public paint catalog are cached. Photos, saved rooms,
   favorites, sign-in, and shared links always go to the network. */

const VERSION = "__VERSION__";
const CACHE = `roomroller-${VERSION}`;
const SHELL = ["/", "/manifest.webmanifest", "/static/icons/icon-192.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  // A new deploy has a new version: drop old caches so nobody is stuck on an old copy.
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key.startsWith("roomroller-") && key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

function cacheable(url) {
  if (url.origin !== self.location.origin) return false;
  return url.pathname.startsWith("/static/") || url.pathname === "/api/colors" || url.pathname === "/manifest.webmanifest";
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);

  if (request.mode === "navigate" && url.origin === self.location.origin && url.pathname === "/") {
    // The page itself: always try for the newest version, fall back to the saved copy offline.
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put("/", copy));
          return response;
        })
        .catch(() => caches.match("/")),
    );
    return;
  }

  if (!cacheable(url)) return;
  // App files carry a version in their address, so a saved copy is safe to use right away.
  event.respondWith(
    caches.open(CACHE).then((cache) =>
      cache.match(request).then((saved) => {
        const fresh = fetch(request)
          .then((response) => {
            if (response.ok) cache.put(request, response.clone());
            return response;
          })
          .catch(() => saved);
        return saved || fresh;
      }),
    ),
  );
});
