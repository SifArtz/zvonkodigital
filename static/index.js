const input = document.getElementById('upcInput');
const submitBtn = document.getElementById('submitBtn');
const statusLine = document.getElementById('status');
const results = document.getElementById('results');

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
    const response = await fetch('/api/upcs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ upcs }),
    });
    if (!response.ok) {
      throw new Error(`Ошибка ${response.status}`);
    }
    const payload = await response.json();
    statusLine.textContent = 'Готово';
    const { hits = [], notes = [] } = payload;
    if (!hits.length && !notes.length) {
      results.appendChild(renderNote('Плейлисты не найдены или коды отложены до даты релиза.'));
    }
    hits.forEach((hit) => results.appendChild(renderHit(hit)));
    notes.forEach((note) => results.appendChild(renderNote(note)));
  } catch (err) {
    console.error(err);
    statusLine.textContent = 'Ошибка при проверке';
    results.appendChild(renderNote('Не удалось выполнить запрос.')); 
  } finally {
    submitBtn.disabled = false;
  }
}

submitBtn.addEventListener('click', submitUpcs);
input.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'enter') {
    submitUpcs();
  }
});
