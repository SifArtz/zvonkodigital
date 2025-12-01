import { processNewUpcs, startQueueWorker, allHits, tokenManager, tokenSnapshot, resetData } from './app.js';

const input = document.getElementById('upcInput');
const submitBtn = document.getElementById('submitBtn');
const resetBtn = document.getElementById('resetBtn');
const statusLine = document.getElementById('status');
const results = document.getElementById('results');

const accessInput = document.getElementById('accessInput');
const refreshInput = document.getElementById('refreshInput');
const expiresInput = document.getElementById('expiresInput');
const saveTokenBtn = document.getElementById('saveTokenBtn');
const clearTokenBtn = document.getElementById('clearTokenBtn');
const tokenStatus = document.getElementById('tokenStatus');

function parseUpcs(raw) {
  return raw
    .replace(/\s+/g, ' ')
    .trim()
    .split(' ')
    .filter(Boolean);
}

function renderHit(hit) {
  const card = document.createElement('div');
  card.className = 'result-card';
  const header = document.createElement('div');
  header.innerHTML = `<strong>${hit.artist}</strong> — ${hit.release_title}`;
  const week = document.createElement('div');
  week.className = 'pill';
  week.textContent = hit.week_label;
  const list = document.createElement('ul');
  list.className = 'playlists';
  hit.playlists.forEach((p) => {
    const li = document.createElement('li');
    li.textContent = p;
    list.appendChild(li);
  });
  card.appendChild(header);
  card.appendChild(week);
  card.appendChild(list);
  return card;
}

function renderNote(text) {
  const card = document.createElement('div');
  card.className = 'result-card';
  card.innerHTML = `<span class="muted">${text}</span>`;
  return card;
}

function updateTokenStatus(snapshot = tokenSnapshot()) {
  if (!snapshot) {
    tokenStatus.textContent = 'Токен не задан';
    return;
  }
  const minutes = Math.round(snapshot.expires_in_ms / 60000);
  tokenStatus.textContent = `Токен сохранён, обновление через ~${minutes} мин.`;
  accessInput.value = snapshot.access_token || '';
  refreshInput.value = snapshot.refresh_token || '';
}

async function submitUpcs() {
  const upcs = parseUpcs(input.value);
  if (!upcs.length) {
    statusLine.textContent = 'Введите хотя бы один UPC';
    return;
  }
  statusLine.textContent = 'Проверяем…';
  submitBtn.disabled = true;
  results.innerHTML = '';

  try {
    const responses = await processNewUpcs(upcs);
    const hits = responses.filter((r) => r.hit).map((r) => r.hit);
    const notes = responses.filter((r) => r.note).map((r) => r.note);

    if (!hits.length && !notes.length) {
      results.appendChild(renderNote('Плейлисты не найдены или коды отложены до даты релиза.'));
    }
    hits.forEach((hit) => results.appendChild(renderHit(hit)));
    notes.forEach((note) => results.appendChild(renderNote(note)));
    statusLine.textContent = 'Готово';
  } catch (err) {
    console.error(err);
    statusLine.textContent = err.message || 'Ошибка при проверке';
    results.appendChild(renderNote('Не удалось выполнить запрос.')); 
  } finally {
    submitBtn.disabled = false;
  }
}

function renderStoredHits() {
  results.innerHTML = '';
  const hits = allHits();
  if (!hits.length) {
    results.appendChild(renderNote('Пока нет попаданий в плейлисты.'));
    return;
  }
  hits.forEach((hit) => results.appendChild(renderHit(hit)));
}

function hydrateTokenInputs() {
  const snap = tokenSnapshot();
  if (!snap) return;
  accessInput.value = snap.access_token || '';
  refreshInput.value = snap.refresh_token || '';
  updateTokenStatus(snap);
}

function saveTokens() {
  try {
    const expires = Number(expiresInput.value || '300');
    tokenManager.setTokens({
      access_token: accessInput.value.trim(),
      refresh_token: refreshInput.value.trim(),
      expires_in: expires,
    });
    updateTokenStatus();
    statusLine.textContent = 'Токен сохранён';
  } catch (err) {
    statusLine.textContent = 'Не удалось сохранить токен';
    console.error(err);
  }
}

function clearTokens() {
  tokenManager.clear();
  accessInput.value = '';
  refreshInput.value = '';
  tokenStatus.textContent = 'Токен сброшен';
}

function handleQueueHits(hits) {
  hits.forEach((hit) => results.prepend(renderHit(hit)));
}

submitBtn.addEventListener('click', submitUpcs);
input.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'enter') {
    submitUpcs();
  }
});
resetBtn.addEventListener('click', () => {
  resetData();
  results.innerHTML = '';
  statusLine.textContent = 'Очередь и результаты очищены';
});
saveTokenBtn.addEventListener('click', saveTokens);
clearTokenBtn.addEventListener('click', clearTokens);

hydrateTokenInputs();
renderStoredHits();
startQueueWorker(handleQueueHits);
updateTokenStatus();
