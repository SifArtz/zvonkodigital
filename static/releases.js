import { allHits } from './app.js';

const monthTabs = document.getElementById('monthTabs');
const weeksContainer = document.getElementById('weeks');

function monthLabel(dateStr) {
  const date = new Date(`${dateStr}T00:00:00Z`);
  return date.toLocaleString('ru-RU', { month: 'long', year: 'numeric' });
}

function groupHits(hits) {
  const byMonth = {};
  hits.forEach((hit) => {
    const mLabel = monthLabel(hit.release_date);
    if (!byMonth[mLabel]) byMonth[mLabel] = {};
    if (!byMonth[mLabel][hit.week_label]) byMonth[mLabel][hit.week_label] = [];
    byMonth[mLabel][hit.week_label].push(hit);
  });
  return byMonth;
}

function renderTabs(grouped) {
  monthTabs.innerHTML = '';
  const months = Object.keys(grouped);
  if (!months.length) return null;
  months.forEach((label, idx) => {
    const tab = document.createElement('div');
    tab.className = `tab ${idx === 0 ? 'active' : ''}`;
    tab.textContent = label;
    tab.dataset.month = label;
    tab.addEventListener('click', () => setActiveMonth(label, grouped));
    monthTabs.appendChild(tab);
  });
  return months[0];
}

function renderWeekBlock(weekLabel, hits) {
  const block = document.createElement('div');
  block.className = 'week-block';
  const title = document.createElement('h3');
  title.textContent = weekLabel;
  block.appendChild(title);

  hits.forEach((hit) => {
    const card = document.createElement('div');
    card.className = 'result-card';
    const header = document.createElement('div');
    header.innerHTML = `<strong>${hit.artist}</strong> — ${hit.release_title}`;
    card.appendChild(header);
    const list = document.createElement('ul');
    list.className = 'playlists';
    hit.playlists.forEach((p) => {
      const li = document.createElement('li');
      li.textContent = p;
      list.appendChild(li);
    });
    card.appendChild(list);
    block.appendChild(card);
  });

  return block;
}

function setActiveMonth(monthLabel, grouped) {
  document.querySelectorAll('.tab').forEach((tab) => {
    tab.classList.toggle('active', tab.dataset.month === monthLabel);
  });

  weeksContainer.innerHTML = '';
  const weeks = grouped[monthLabel];
  if (!weeks) return;
  Object.entries(weeks).forEach(([weekLabel, hits]) => {
    weeksContainer.appendChild(renderWeekBlock(weekLabel, hits));
  });
}

function loadHits() {
  weeksContainer.innerHTML = '<div class="muted">Загружаем…</div>';
  try {
    const hits = allHits();
    if (!hits.length) {
      weeksContainer.innerHTML = '<div class="empty-state">Пока нет попаданий в плейлисты</div>';
      return;
    }
    const grouped = groupHits(hits);
    const firstMonth = renderTabs(grouped);
    if (firstMonth) setActiveMonth(firstMonth, grouped);
  } catch (err) {
    console.error(err);
    weeksContainer.innerHTML = '<div class="empty-state">Не удалось загрузить данные</div>';
  }
}

loadHits();
