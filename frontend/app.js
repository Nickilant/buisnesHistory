const API_URL = window.__API_URL__ || 'http://localhost:8000';

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
    <div>${formatDate(item.findDate)}</div>
    <div>${formatDate(item.actualDate)}</div>
    <div>${item.eventType}</div>
    <div>${item.contentTypeName}</div>
  `;
  return row;
}

function renderCases(cases, token) {
  const container = document.getElementById('case-list');
  container.innerHTML = '';

  if (!cases.length) {
    container.innerHTML = '<div class="muted">Ничего не найдено.</div>';
    return;
  }

  cases.forEach((item) => {
    const wrapper = document.createElement('article');
    wrapper.className = 'case-item';

    const row = document.createElement('div');
    row.className = 'case-row';
    row.innerHTML = `
      <div>${item.caseNumber}</div>
      <a class="case-link" target="_blank" href="${item.caseLink}">ссылка на дело</a>
      <button>История</button>
    `;

    const btn = row.querySelector('button');
    const history = document.createElement('div');
    history.className = 'history';
    history.style.display = 'none';

    btn.addEventListener('click', async () => {
      if (history.style.display === 'none') {
        if (!history.dataset.loaded) {
          const items = await fetchHistory(token, item.caseId);
          items.forEach((h) => history.appendChild(createHistoryRow(h)));
          history.dataset.loaded = '1';
        }
        history.style.display = 'block';
        btn.textContent = 'Скрыть';
      } else {
        history.style.display = 'none';
        btn.textContent = 'История';
      }
    });

    wrapper.appendChild(row);
    wrapper.appendChild(history);
    container.appendChild(wrapper);
  });
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
  const searchInput = document.getElementById('search');

  async function refresh() {
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
