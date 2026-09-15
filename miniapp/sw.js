/* Service worker мини-аппа TTPU.

   Задача: приложение должно открываться без сети — со страницей, иконками и
   последним загруженным расписанием. Без этого Telegram просто не сможет
   загрузить сам адрес, и офлайн-режим не спасёт никакой localStorage.

   Стратегии:
   - страница и data.json — сначала сеть, при неудаче кеш: пока интернет есть,
     пользователь всегда видит свежее расписание;
   - иконки и файлы с CDN — сначала кеш с фоновым обновлением: они почти
     не меняются, а ждать их по сети незачем.
   Запросы не-GET не трогаем вообще. */

/* Имя меняется при сбросе данных: обработчик activate удаляет все кеши,
   кроме текущего, поэтому старая офлайн-копия страницы и расписания уходит. */
const CACHE = "ttpu-static-v2";
const SHELL = [
  "./", "index.html", "data.json",
  "icons/home-morph.json", "icons/calendar-morph.json", "icons/qr-code.json",
  "icons/bar-chart-morph.json", "icons/info-circle-morph.json"
];

self.addEventListener("install", e => {
  e.waitUntil(
    caches.open(CACHE)
      // addAll падает целиком, если хоть один файл не отдался, поэтому кладём поштучно
      .then(c => Promise.all(SHELL.map(u => c.add(u).catch(() => {}))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys()
      .then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// ignoreSearch: адреса приходят с ?v=… и без него — иначе кеш не совпадёт
const fromCache = req => caches.match(req, { ignoreSearch: true });

function store(req, res) {
  // opaque — ответ с чужого домена без CORS; .ok у него всегда false, но класть в кеш можно
  if (!res || !(res.ok || res.type === "opaque")) return res;
  const copy = res.clone();
  caches.open(CACHE).then(c => c.put(req, copy)).catch(() => {});
  return res;
}

/* Помечаем ответ, отданный из кеша. Иначе офлайн выглядит для приложения как
   обычный успешный запрос (кеш отдаётся с кодом 200), и оно не узнает, что
   показывает несвежее расписание. Пересобрать так можно только свой же ответ —
   у чужих доменов тело недоступно, но им пометка и не нужна. */
function markCached(res) {
  try {
    const h = new Headers(res.headers);
    h.set("X-From-Cache", "1");
    return new Response(res.body, { status: res.status, statusText: res.statusText, headers: h });
  } catch (e) { return res; }
}

async function networkFirst(req) {
  try {
    return store(req, await fetch(req));
  } catch (err) {
    const hit = await fromCache(req);
    if (hit) return markCached(hit);
    throw err;
  }
}

async function cacheFirst(req) {
  const hit = await fromCache(req);
  if (hit) {
    // отдаём из кеша сразу, а свежую версию подтягиваем в фоне к следующему запуску
    fetch(req).then(res => store(req, res)).catch(() => {});
    return hit;
  }
  return store(req, await fetch(req));
}

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;

  // саму страницу отдаём из кеша, только если сеть недоступна
  if (req.mode === "navigate") {
    e.respondWith(
      networkFirst(req).catch(() => fromCache("index.html").then(r => r || fromCache("./")))
    );
    return;
  }

  const url = new URL(req.url);

  // Ответы LMS не кешируем никогда: это личные данные студента и его токен,
  // им не место в общем кеше приложения.
  if (url.hostname === "edu.turin.uz") return;

  if (url.origin === self.location.origin && url.pathname.endsWith("data.json")) {
    e.respondWith(networkFirst(req));
    return;
  }

  e.respondWith(cacheFirst(req).catch(() => fromCache(req)));
});
