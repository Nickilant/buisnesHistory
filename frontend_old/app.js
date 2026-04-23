const API_URL = window.__API_URL__ || 'http://localhost:8000';
const PAGE_SIZE = 10;
let currentPage = 1;
let currentCases = [];

function parsePlacementOptions(raw) {
  if (!raw) return {};
  try {
    return JSON.parse(raw);
  } catch {
    return {};
  }
}

function getQuery() {
  return new URLSearchParams(window.location.search);
}

async function ensureAuth() {
  const query = getQuery();
  const memberId = query.get('member_id');
  const userId = query.get('user_id');
  const domain = query.get('DOMAIN') || query.get('domain');

  let token = localStorage.getItem('access_token');
  if (!token) {
    if (memberId && userId) {
      const resp = await fetch(`${API_URL}/auth/bitrix-auto`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ member_id: memberId, user_id: userId, domain }),
      });
      if (resp.ok) {
        const data = await resp.json();
        token = data.access_token;
        localStorage.setItem('access_token', token);
      } else {
        console.warn('Bitrix auto-login failed, trying local auth fallback');
      }
    }

    if (!token) {
      const localResp = await fetch(`${API_URL}/auth/local`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      if (localResp.ok) {
        const data = await localResp.json();
        token = data.access_token;
        localStorage.setItem('access_token', token);
      }
    }
  }

  if (!token) throw new Error('Нет access token. Для локального запуска включите ALLOW_LOCAL_DEV_AUTH=true.');
  return token;
}

function formatDate(value) {
  if (!value) return '—';
  return new Date(value).toLocaleString('ru-RU');
}

async function fetchCases(token, search = '') {
  const params = new URLSearchParams();
  if (search) params.set('search', search);
  const resp = await fetch(`${API_URL}/cases?${params.toString()}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!resp.ok) throw new Error('Ошибка загрузки дел');
  return resp.json();
}

async function fetchHistory(token, caseId) {
  const resp = await fetch(`${API_URL}/cases/${caseId}/history`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!resp.ok) throw new Error('Ошибка загрузки истории');
  return resp.json();
}

function createHistoryRow(item) {
  const row = document.createElement('div');
  row.className = 'history-row';
  row.innerHTML = `
    <div class="history-cell">
      <span class="history-label">Найдено</span>
      <span>${formatDate(item.findDate)}</span>
    </div>
    <div class="history-cell">
      <span class="history-label">Актуально</span>
      <span>${formatDate(item.actualDate)}</span>
    </div>
    <div class="history-cell">
      <span class="history-label">Тип события</span>
      <span>${item.eventType}</span>
    </div>
    <div class="history-cell">
      <span class="history-label">Документ</span>
      <span>${item.contentTypeName}</span>
    </div>
  `;
  return row;
}

function renderPagination(totalItems) {
  const pagination = document.getElementById('pagination');
  pagination.innerHTML = '';
  const totalPages = Math.max(1, Math.ceil(totalItems / PAGE_SIZE));
  if (totalItems <= PAGE_SIZE) return;

  const prev = document.createElement('button');
  prev.className = 'pager-btn';
  prev.textContent = '← Назад';
  prev.disabled = currentPage === 1;
  prev.addEventListener('click', () => {
    if (currentPage > 1) {
      currentPage -= 1;
      renderCases(currentCases, window.__token);
    }
  });

  const indicator = document.createElement('div');
  indicator.className = 'pager-indicator';
  indicator.textContent = `Страница ${currentPage} из ${totalPages}`;

  const next = document.createElement('button');
  next.className = 'pager-btn';
  next.textContent = 'Вперёд →';
  next.disabled = currentPage === totalPages;
  next.addEventListener('click', () => {
    if (currentPage < totalPages) {
      currentPage += 1;
      renderCases(currentCases, window.__token);
    }
  });

  pagination.appendChild(prev);
  pagination.appendChild(indicator);
  pagination.appendChild(next);
}

function renderCases(cases, token) {
  const container = document.getElementById('case-list');
  container.innerHTML = '';
  currentCases = cases;

  if (!cases.length) {
    container.innerHTML = '<div class="muted">Ничего не найдено.</div>';
    renderPagination(0);
    return;
  }

  const start = (currentPage - 1) * PAGE_SIZE;
  const visibleCases = cases.slice(start, start + PAGE_SIZE);

  visibleCases.forEach((item) => {
    const wrapper = document.createElement('article');
    wrapper.className = 'case-item';

    const row = document.createElement('button');
    row.className = 'case-row';
    row.type = 'button';
    row.setAttribute('aria-expanded', 'false');
    row.innerHTML = `
      <div class="case-meta">
        <div class="case-number">${item.caseNumber}</div>
        <div class="case-date">Последнее обновление: ${formatDate(item.latestFindDate)}</div>
      </div>
      <a class="case-link" target="_blank" href="${item.caseLink}">Открыть дело ↗</a>
      <span class="chevron" aria-hidden="true">⌄</span>
    `;

    const link = row.querySelector('.case-link');
    const history = document.createElement('div');
    history.className = 'history';
    history.style.display = 'none';

    link.addEventListener('click', (event) => {
      event.stopPropagation();
    });

    row.addEventListener('click', async () => {
      if (history.style.display === 'none') {
        if (!history.dataset.loaded) {
          const items = await fetchHistory(token, item.caseId);
          items.forEach((h) => history.appendChild(createHistoryRow(h)));
          history.dataset.loaded = '1';
        }
        history.style.display = 'block';
        row.classList.add('expanded');
        row.setAttribute('aria-expanded', 'true');
      } else {
        history.style.display = 'none';
        row.classList.remove('expanded');
        row.setAttribute('aria-expanded', 'false');
      }
    });

    wrapper.appendChild(row);
    wrapper.appendChild(history);
    container.appendChild(wrapper);
  });

  renderPagination(cases.length);
}

async function bootstrap() {
  const query = getQuery();
  const placement = query.get('PLACEMENT') || '';
  if (placement.startsWith('CRM_DEAL_DETAIL')) {
    document.body.classList.add('compact');
  }

  const placementOptions = parsePlacementOptions(query.get('PLACEMENT_OPTIONS'));
  if (placementOptions.deal_id) {
    console.info('deal_id from placement options', placementOptions.deal_id);
  }

  const token = await ensureAuth();
  window.__token = token;
  const searchInput = document.getElementById('search');

  async function refresh() {
    currentPage = 1;
    const list = await fetchCases(token, searchInput.value.trim());
    renderCases(list, token);
  }

  searchInput.addEventListener('input', () => {
    refresh().catch((err) => alert(err.message));
  });

  await refresh();
}

bootstrap().catch((err) => {
  const message = err instanceof TypeError
    ? 'Failed to fetch: проверьте, что API поднят на FRONTEND_API_URL и CORS_ALLOW_ORIGINS разрешает origin фронта.'
    : err.message;
  document.getElementById('case-list').innerHTML = `<div class="muted">${message}</div>`;
});
