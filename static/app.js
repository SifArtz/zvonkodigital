// Shared logic for UPC checks, token storage, and playlist lookups (pure browser JS)

const AUTH_BASE = 'https://auth.zvonkodigital.ru';
const TOKEN_PATH = '/o/token/';
const CLIENT_ID = '75mwixlHmTIbzvREyUQt3Sk29lwpQfIw9bU948wJ';
const ALBUM_ENDPOINT = 'https://media.zvonkodigital.ru/api/albums_list';
const PLAYLIST_ENDPOINT = 'https://charts.zvonkodigital.ru/playlists/';

const PLAYLIST_PLATFORMS = {
  vk: 'ВКонтакте',
  yandex: 'Яндекс Музыка',
  mts: 'МТС Музыка',
  zvooq: 'Звук',
};

const STORAGE_KEYS = {
  tokens: 'zd_tokens',
  queue: 'zd_queue',
  hits: 'zd_hits',
};

function loadJson(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch (_err) {
    return fallback;
  }
}

function saveJson(key, value) {
  localStorage.setItem(key, JSON.stringify(value));
}

function addDays(date, days) {
  const copy = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()));
  copy.setUTCDate(copy.getUTCDate() + days);
  return copy;
}

function isoDate(date) {
  return date.toISOString().slice(0, 10);
}

function startOfWeekMonday(date) {
  const day = date.getUTCDay();
  const diff = (day === 0 ? -6 : 1 - day); // Monday as first day
  return addDays(date, diff);
}

function weekLabel(releaseDate) {
  const weekStart = startOfWeekMonday(releaseDate);
  const weekEnd = addDays(weekStart, 6);
  return `Неделя ${isoDate(weekStart).slice(5).replace('-', '.')} - ${isoDate(weekEnd).slice(5).replace('-', '.')}`;
}

function playlistDate(releaseDate, today) {
  const target = addDays(releaseDate, 7);
  return target > today ? today : target;
}

function releaseMatches(result, releaseTitle) {
  const normalized = (releaseTitle || '').toLowerCase();
  const track = (result.track_name || '').toLowerCase();
  const album = (result.album_name || '').toLowerCase();
  return track.includes(normalized) || album.includes(normalized);
}

class TokenManager {
  constructor() {
    this.state = loadJson(STORAGE_KEYS.tokens, null);
    this.refreshPromise = null;
  }

  setTokens({ access_token, refresh_token, expires_in }) {
    const expiresAt = Date.now() + Math.max(1, expires_in || 0) * 1000;
    this.state = { access_token, refresh_token, expires_at: expiresAt };
    saveJson(STORAGE_KEYS.tokens, this.state);
    return this.state;
  }

  clear() {
    this.state = null;
    saveJson(STORAGE_KEYS.tokens, null);
  }

  isExpired() {
    if (!this.state || !this.state.expires_at) return true;
    return Date.now() >= this.state.expires_at - 30_000; // refresh 30s early
  }

  async refresh() {
    if (!this.state?.refresh_token) throw new Error('Нет refresh_token — выполните вход вручную');
    if (this.refreshPromise) return this.refreshPromise;

    const body = new URLSearchParams({
      grant_type: 'refresh_token',
      client_id: CLIENT_ID,
      refresh_token: this.state.refresh_token,
    });

    this.refreshPromise = fetch(`${AUTH_BASE}${TOKEN_PATH}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body,
    })
      .then(async (response) => {
        if (!response.ok) throw new Error('Не удалось обновить токен');
        const data = await response.json();
        this.refreshPromise = null;
        return this.setTokens({
          access_token: data.access_token,
          refresh_token: data.refresh_token || this.state.refresh_token,
          expires_in: data.expires_in || 300,
        });
      })
      .catch((err) => {
        this.refreshPromise = null;
        this.clear();
        throw err;
      });

    return this.refreshPromise;
  }

  async ensureAccessToken() {
    if (this.state?.access_token && !this.isExpired()) return this.state.access_token;
    const refreshed = await this.refresh();
    return refreshed.access_token;
  }

  get snapshot() {
    return this.state;
  }
}

export const tokenManager = new TokenManager();

function getQueue() {
  return loadJson(STORAGE_KEYS.queue, []);
}

function saveQueue(queue) {
  saveJson(STORAGE_KEYS.queue, queue);
}

function upsertQueue(entry) {
  const queue = getQueue();
  const idx = queue.findIndex((item) => item.upc === entry.upc);
  if (idx >= 0) queue[idx] = entry;
  else queue.push(entry);
  saveQueue(queue);
}

function removeFromQueue(upc) {
  saveQueue(getQueue().filter((item) => item.upc !== upc));
}

function getHits() {
  return loadJson(STORAGE_KEYS.hits, []);
}

function saveHits(hits) {
  saveJson(STORAGE_KEYS.hits, hits);
}

function recordHit(hit) {
  const hits = getHits();
  const idx = hits.findIndex((h) => h.upc === hit.upc);
  if (idx >= 0) hits[idx] = hit;
  else hits.push(hit);
  saveHits(hits);
}

async function authedFetch(url, options = {}) {
  const token = await tokenManager.ensureAccessToken();
  const headers = options.headers ? { ...options.headers } : {};
  headers.Authorization = `Bearer ${token}`;
  return fetch(url, { ...options, headers });
}

function extractReleaseTitle(album) {
  return (
    album.album_name ||
    album.product_name ||
    album.title ||
    album.track_name ||
    'Без названия'
  );
}

async function fetchAlbum(upc) {
  const response = await authedFetch(`${ALBUM_ENDPOINT}?search=${encodeURIComponent(upc)}`);
  if (!response.ok) return null;
  const payload = await response.json();
  const albums = payload.albums || [];
  return albums[0] || null;
}

async function fetchPlaylists(platformKey, platformLabel, artistName, releaseTitle, playlistDate) {
  const params = new URLSearchParams({
    platform: platformKey,
    date: playlistDate,
    limit: '50',
    offset: '0',
    q: artistName,
  });
  try {
    const response = await authedFetch(`${PLAYLIST_ENDPOINT}?${params.toString()}`);
    if (!response.ok) return [];
    const payload = await response.json();
    const results = payload.results || [];
    const lines = [];
    results.forEach((result) => {
      const playlistName = result.playlist_name;
      if (!playlistName) return;
      if (!releaseMatches(result, releaseTitle)) return;
      const position = result.position;
      const note = position !== null && position !== undefined ? `(позиция ${position})` : '(Плейлист подборка)';
      lines.push(`«${playlistName}» (${platformLabel}) ${note}`);
    });
    return lines;
  } catch (_err) {
    return [];
  }
}

async function checkPlaylists({ artist, releaseTitle, playlistDate }) {
  const tasks = Object.entries(PLAYLIST_PLATFORMS).map(([key, label]) =>
    fetchPlaylists(key, label, artist, releaseTitle, playlistDate)
  );
  const results = await Promise.all(tasks);
  return results.flat();
}

function scheduleRecord(upc, artist, releaseTitle, releaseDate, today) {
  const attempts_remaining = 2;
  if (releaseDate > today) {
    const next_check = releaseDate;
    upsertQueue({
      upc,
      artist,
      release_title: releaseTitle,
      release_date: isoDate(releaseDate),
      next_check: isoDate(next_check),
      attempts_remaining,
    });
    return `${upc}: релиз ещё не вышел, проверим ${isoDate(next_check).split('-').reverse().join('.')}`;
  }
  upsertQueue({
    upc,
    artist,
    release_title: releaseTitle,
    release_date: isoDate(releaseDate),
    next_check: isoDate(today),
    attempts_remaining,
  });
  return null;
}

async function processSingleUpc(upc, today) {
  const queue = getQueue();
  const existing = queue.find((item) => item.upc === upc) || null;
  const album = await fetchAlbum(upc);
  if (!album) return { hit: null, note: `${upc}: альбом не найден` };

  const artistName = album.artist_name || 'Неизвестный исполнитель';
  const releaseTitle = extractReleaseTitle(album);
  const releaseDateRaw = album.sales_start_date || album.release_date;
  if (!releaseDateRaw) return { hit: null, note: `${upc}: нет даты начала продаж` };

  const releaseDate = new Date(`${releaseDateRaw.slice(0, 10)}T00:00:00Z`);
  if (!existing) {
    const scheduleNote = scheduleRecord(upc, artistName, releaseTitle, releaseDate, today);
    if (scheduleNote) return { hit: null, note: scheduleNote };
  }

  const targetDate = playlistDate(releaseDate, today);
  const playlistLines = await checkPlaylists({
    artist: artistName,
    releaseTitle,
    playlistDate: isoDate(targetDate),
  });

  if (playlistLines.length) {
    const hit = {
      upc,
      artist: artistName,
      release_title: releaseTitle,
      release_date: isoDate(releaseDate),
      week_label: weekLabel(releaseDate),
      playlists: playlistLines,
      recorded_at: new Date().toISOString(),
    };
    recordHit(hit);
    removeFromQueue(upc);
    return { hit, note: null };
  }

  const cutoffDate = addDays(releaseDate, 7);
  const record = existing || {
    upc,
    artist: artistName,
    release_title: releaseTitle,
    release_date: isoDate(releaseDate),
    next_check: isoDate(today),
    attempts_remaining: 2,
  };
  const attemptsLeft = record.attempts_remaining ?? 0;

  if (targetDate >= cutoffDate || attemptsLeft <= 0) {
    removeFromQueue(upc);
    return { hit: null, note: `${upc}: не найдено в окне релиза` };
  }

  const nextCheck = addDays(today, 7);
  const scheduled = {
    upc,
    artist: artistName,
    release_title: releaseTitle,
    release_date: isoDate(releaseDate),
    next_check: isoDate(nextCheck > cutoffDate ? cutoffDate : nextCheck),
    attempts_remaining: attemptsLeft - 1,
  };
  upsertQueue(scheduled);
  return { hit: null, note: `${upc}: повторная проверка запланирована на ${scheduled.next_check.split('-').reverse().join('.')}` };
}

let workerActive = false;

export async function processNewUpcs(upcs) {
  const today = new Date();
  const results = [];
  for (const upc of upcs) {
    // eslint-disable-next-line no-await-in-loop
    results.push(await processSingleUpc(upc, today));
  }
  return results;
}

export function allHits() {
  return getHits().sort((a, b) => (a.release_date < b.release_date ? 1 : -1));
}

export function tokenSnapshot() {
  const snap = tokenManager.snapshot;
  if (!snap) return null;
  return {
    ...snap,
    expires_in_ms: Math.max(0, snap.expires_at - Date.now()),
  };
}

export async function runQueueOnce() {
  if (workerActive) return [];
  workerActive = true;
  const today = new Date();
  const due = getQueue().filter((item) => new Date(`${item.next_check}T00:00:00Z`) <= today);
  const hits = [];
  try {
    for (const item of due) {
      // eslint-disable-next-line no-await-in-loop
      try {
        const res = await processSingleUpc(item.upc, today);
        if (res.hit) hits.push(res.hit);
      } catch (err) {
        // eslint-disable-next-line no-console
        console.error('Queue check failed for', item.upc, err);
      }
    }
    return hits;
  } finally {
    workerActive = false;
  }
}

export function startQueueWorker(onHits) {
  runQueueOnce()
    .then((hits) => {
      if (hits.length && onHits) onHits(hits);
    })
    .catch((err) => console.error('Initial queue run failed', err));

  setInterval(async () => {
    try {
      const hits = await runQueueOnce();
      if (hits.length && onHits) onHits(hits);
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error('Queue tick failed', err);
    }
  }, 60_000);
}

export function resetData() {
  saveQueue([]);
  saveHits([]);
}

export const DateUtils = { weekLabel, isoDate };
