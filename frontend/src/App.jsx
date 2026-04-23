import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Search, ExternalLink, ChevronDown, Scale, Clock, FileText, Zap } from 'lucide-react'
import './App.css'

const API_URL = window.__API_URL__ || '/api'
const PAGE_SIZE = 10
const CASE_NUMBER_FIELDS = ['UF_CRM_CASE_NUMBER', 'UF_CRM_1699999999', 'CASE_NUMBER']
const WIDGET_PLACEMENTS = [
  'CRM_DEAL_DETAIL',
  'CRM_DEAL_DETAIL_TAB',
  'CRM_CONTACT_DETAIL',
  'CRM_CONTACT_DETAIL_TAB',
  'CRM_COMPANY_DETAIL',
  'CRM_COMPANY_DETAIL_TAB',
]

function getQuery() {
  return new URLSearchParams(window.location.search)
}

function parsePlacementOptions(raw) {
  if (!raw) return {}
  try {
    return JSON.parse(raw)
  } catch {
    return {}
  }
}

async function ensureAuth() {
  const query = getQuery()
  const memberId = query.get('member_id')
  const userId = query.get('user_id')
  const domain = query.get('DOMAIN') || query.get('domain')

  let token = localStorage.getItem('access_token')
  if (!token && memberId && userId) {
    const resp = await fetch(`${API_URL}/auth/bitrix-auto`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ member_id: memberId, user_id: userId, domain }),
    })
    if (resp.ok) {
      const data = await resp.json()
      token = data.access_token
      localStorage.setItem('access_token', token)
    }
  }

  if (!token) {
    const localResp = await fetch(`${API_URL}/auth/local`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    })
    if (localResp.ok) {
      const data = await localResp.json()
      token = data.access_token
      localStorage.setItem('access_token', token)
    }
  }

  return token
}

function formatDate(value) {
  if (!value) return '—'
  return new Date(value).toLocaleString('ru-RU', {
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit'
  })
}

async function apiGet(path, token) {
  const headers = token ? { Authorization: `Bearer ${token}` } : {}
  const resp = await fetch(`${API_URL}${path}`, { headers })
  if (!resp.ok) {
    const body = await resp.text()
    throw new Error(body || `Ошибка запроса ${path}`)
  }
  return resp.json()
}

async function fetchCases(token, search = '', caseNumber = '') {
  const params = new URLSearchParams()
  if (caseNumber) params.set('case_number', caseNumber)
  else if (search) params.set('search', search)
  return apiGet(`/cases?${params.toString()}`, token)
}

async function fetchAllEvents(token, search = '', dateFrom = '', dateTo = '') {
  const params = new URLSearchParams()
  if (search) params.set('search', search)
  if (dateFrom) params.set('date_from', dateFrom)
  if (dateTo) params.set('date_to', dateTo)
  return apiGet(`/events/history?${params.toString()}`, token)
}

async function fetchHistory(token, caseId) {
  return apiGet(`/cases/${caseId}/history`, token)
}

async function getEntityCaseNumber() {
  const query = getQuery()
  const placement = query.get('PLACEMENT') || ''
  const placementOptions = parsePlacementOptions(query.get('PLACEMENT_OPTIONS'))
  const entityId = query.get('ID') || query.get('ENTITY_ID') || placementOptions.ID || placementOptions.ENTITY_ID
  if (!entityId) return null

  const presetCaseNumber = query.get('case_number') || placementOptions.case_number
  if (presetCaseNumber) return presetCaseNumber

  if (!window.BX24 || typeof window.BX24.callMethod !== 'function') return null

  let method = 'crm.deal.get'
  if (placement.startsWith('CRM_CONTACT_DETAIL')) method = 'crm.contact.get'
  if (placement.startsWith('CRM_COMPANY_DETAIL')) method = 'crm.company.get'

  return new Promise((resolve) => {
    window.BX24.callMethod(method, { id: entityId }, (result) => {
      if (!result || !result.data) {
        resolve(null)
        return
      }
      const entity = result.data()
      const value = CASE_NUMBER_FIELDS.map((field) => entity[field]).find(Boolean)
      resolve(typeof value === 'string' ? value.trim() : value || null)
    })
  })
}

function HistoryRow({ item, index, showCase = false }) {
  return (
    <motion.div
      className={`history-row ${showCase ? 'with-case' : ''}`}
      initial={{ opacity: 0, x: -12 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ delay: index * 0.055, duration: 0.3, ease: 'easeOut' }}
    >
      {showCase && (
        <div className="history-cell">
          <span className="history-icon"><Scale size={11} /></span>
          <span className="history-label">Дело</span>
          <span className="history-value">{item.caseNumber}</span>
        </div>
      )}
      <div className="history-cell">
        <span className="history-icon"><Zap size={11} /></span>
        <span className="history-label">Найдено</span>
        <span className="history-value">{formatDate(item.findDate)}</span>
      </div>
      <div className="history-cell">
        <span className="history-icon"><Clock size={11} /></span>
        <span className="history-label">Актуально до</span>
        <span className="history-value">{formatDate(item.actualDate)}</span>
      </div>
      <div className="history-cell">
        <span className="history-icon"><Scale size={11} /></span>
        <span className="history-label">Тип события</span>
        <span className="history-value">{item.eventType}</span>
      </div>
      <div className="history-cell">
        <span className="history-icon"><FileText size={11} /></span>
        <span className="history-label">Документ</span>
        <span className="history-value">{item.contentTypeName}</span>
      </div>
    </motion.div>
  )
}

function CaseItem({ item, token, index }) {
  const [expanded, setExpanded] = useState(false)
  const [history, setHistory] = useState([])
  const [loaded, setLoaded] = useState(false)
  const [loading, setLoading] = useState(false)

  const toggle = async () => {
    if (!expanded && !loaded) {
      setLoading(true)
      try {
        const items = await fetchHistory(token, item.caseId)
        setHistory(items)
        setLoaded(true)
      } catch (e) {
        console.error(e)
      }
      setLoading(false)
    }
    setExpanded(v => !v)
  }

  return (
    <motion.article
      className="case-item"
      initial={{ opacity: 0, y: 18 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.065, duration: 0.45, ease: [0.22, 1, 0.36, 1] }}
      layout
    >
      <button
        className={`case-row ${expanded ? 'expanded' : ''}`}
        onClick={toggle}
        aria-expanded={expanded}
      >
        <div className="case-left">
          <div className="case-number">
            <span className="case-dot" />
            {item.caseNumber}
          </div>
          <div className="case-date">
            <Clock size={11} />
            {formatDate(item.latestFindDate)}
          </div>
        </div>

        <a
          className="case-link"
          href={item.caseLink}
          target="_blank"
          rel="noreferrer"
          onClick={e => e.stopPropagation()}
        >
          Открыть в КАД <ExternalLink size={12} />
        </a>

        <motion.span
          className="chevron"
          animate={{ rotate: expanded ? 180 : 0 }}
          transition={{ duration: 0.22, ease: 'easeInOut' }}
        >
          <ChevronDown size={17} />
        </motion.span>
      </button>

      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            key="history"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.38, ease: [0.22, 1, 0.36, 1] }}
            style={{ overflow: 'hidden' }}
          >
            <div className="history-panel">
              {loading && (
                <div className="shimmer-list">
                  {[1,2,3].map(i => <div key={i} className="shimmer-row" />)}
                </div>
              )}
              {!loading && loaded && history.length === 0 && (
                <div className="history-empty">История событий пуста</div>
              )}
              {!loading && history.map((h, i) => (
                <HistoryRow key={i} item={h} index={i} />
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.article>
  )
}

function Pagination({ total, current, onChange }) {
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  if (total <= PAGE_SIZE) return null

  const pages = []
  for (let i = 1; i <= totalPages; i++) {
    if (i === 1 || i === totalPages || (i >= current - 1 && i <= current + 1)) {
      pages.push(i)
    } else if (pages[pages.length - 1] !== '…') {
      pages.push('…')
    }
  }

  return (
    <motion.div
      className="pagination"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ delay: 0.4 }}
    >
      <button className="pager-btn" disabled={current === 1} onClick={() => onChange(current - 1)}>
        ←
      </button>
      <div className="pager-pills">
        {pages.map((p, i) =>
          p === '…'
            ? <span key={`e${i}`} className="pager-ellipsis">…</span>
            : <button
                key={p}
                className={`pager-pill ${p === current ? 'active' : ''}`}
                onClick={() => onChange(p)}
              >{p}</button>
        )}
      </div>
      <button className="pager-btn" disabled={current === totalPages} onClick={() => onChange(current + 1)}>
        →
      </button>
    </motion.div>
  )
}

export default function App() {
  const [token, setToken] = useState(null)
  const [cases, setCases] = useState([])
  const [events, setEvents] = useState([])
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [compact, setCompact] = useState(false)
  const [widgetCaseNumber, setWidgetCaseNumber] = useState('')
  const [mode, setMode] = useState('local')
  const [tab, setTab] = useState('cases')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const debounceRef = useRef(null)

  useEffect(() => {
    const query = getQuery()
    const placement = query.get('PLACEMENT') || ''
    const isWidget = WIDGET_PLACEMENTS.some((item) => placement.startsWith(item))

    setMode(isWidget ? 'widget' : 'local')
    if (isWidget) setCompact(true)

    ensureAuth()
      .then(t => setToken(t || null))
      .catch(() => setToken(null))

    if (isWidget) {
      getEntityCaseNumber().then((value) => setWidgetCaseNumber(value || ''))
    }
  }, [])

  const load = useCallback(async (tok, q, currentTab, currentMode, caseNumber, fromDate, toDate) => {
    setLoading(true)
    setError(null)
    try {
      if (currentTab === 'events' && currentMode === 'local') {
        const list = await fetchAllEvents(tok, q, fromDate, toDate)
        setEvents(list)
      } else {
        const list = await fetchCases(tok, q, currentMode === 'widget' ? caseNumber : '')
        setCases(list)
      }
      setPage(1)
    } catch (e) {
      setError(e.message)
    }
    setLoading(false)
  }, [])

  useEffect(() => {
    if (mode === 'widget' && !widgetCaseNumber) return
    load(token, search, tab, mode, widgetCaseNumber, dateFrom, dateTo)
  }, [token, load, tab, mode, widgetCaseNumber, dateFrom, dateTo])

  const handleSearch = (val) => {
    setSearch(val)
    clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      load(token, val, tab, mode, widgetCaseNumber, dateFrom, dateTo)
    }, 350)
  }

  const activeItems = useMemo(() => (tab === 'events' ? events : cases), [tab, events, cases])
  const visibleItems = activeItems.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)

  return (
    <div className={`app-root ${compact ? 'compact' : ''}`}>
      <div className="orb orb1" />
      <div className="orb orb2" />
      <div className="orb orb3" />

      <div className="container">
        <header className="header">
          <div className="brand">
            <div className="brand-icon">
              <Scale size={18} strokeWidth={1.5} />
            </div>
            <div>
              <h1>Арбитражные дела</h1>
              <p>{mode === 'widget' ? 'История по делу из карточки пользователя' : 'Мониторинг событий КАД в реальном времени'}</p>
            </div>
          </div>

          {mode === 'local' && (
            <div className="tabs">
              <button className={`tab-btn ${tab === 'cases' ? 'active' : ''}`} onClick={() => setTab('cases')}>Список дел</button>
              <button className={`tab-btn ${tab === 'events' ? 'active' : ''}`} onClick={() => setTab('events')}>Общая лента</button>
            </div>
          )}

          <div className="search-wrap">
            <Search size={14} className="search-icon-el" />
            <input
              className="search-input"
              type="text"
              value={search}
              onChange={e => handleSearch(e.target.value)}
              placeholder="Поиск по номеру дела…"
            />
            <AnimatePresence>
              {search && (
                <motion.button
                  className="search-clear"
                  initial={{ opacity: 0, scale: 0.6 }}
                  animate={{ opacity: 1, scale: 1 }}
                  exit={{ opacity: 0, scale: 0.6 }}
                  onClick={() => handleSearch('')}
                >×</motion.button>
              )}
            </AnimatePresence>
          </div>

          <div className="cases-count">{activeItems.length} записей</div>
        </header>

        {mode === "local" && tab === "events" && (
          <section className="range-panel">
            <label className="range-field">
              С
              <input
                type="datetime-local"
                value={dateFrom}
                onChange={(e) => setDateFrom(e.target.value)}
              />
            </label>
            <label className="range-field">
              По
              <input
                type="datetime-local"
                value={dateTo}
                onChange={(e) => setDateTo(e.target.value)}
              />
            </label>
            <button
              type="button"
              className="range-reset"
              onClick={() => { setDateFrom(""); setDateTo(""); }}
            >
              Сбросить
            </button>
          </section>
        )}

        <main className="main">
          {mode === 'widget' && !widgetCaseNumber && !loading && (
            <div className="error-card">⚠️ Не удалось определить номер дела из карточки. Проверьте поле сделки и/или CASE_NUMBER_FIELDS.</div>
          )}

          {error && (
            <motion.div className="error-card" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
              ⚠️ {error}
            </motion.div>
          )}

          {loading && !error && (
            <div className="skeleton-list">
              {[1,2,3,4,5].map(i => (
                <div key={i} className="skeleton-item" style={{ animationDelay: `${i * 0.07}s` }} />
              ))}
            </div>
          )}

          {!loading && !error && activeItems.length === 0 && (
            <motion.div className="empty-state" initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }}>
              <Scale size={44} strokeWidth={0.8} />
              <p>Ничего не найдено</p>
            </motion.div>
          )}

          <AnimatePresence mode="wait">
            {!loading && !error && visibleItems.length > 0 && (
              <motion.div key={`page-${page}-${search}-${tab}`}>
                {tab === 'events'
                  ? visibleItems.map((item, i) => (
                    <article key={`${item.caseId}-${i}`} className="case-item">
                      <div className="history-panel">
                        <HistoryRow item={item} index={i} showCase />
                        <a className="case-link timeline-link" href={item.caseLink} target="_blank" rel="noreferrer">
                          Открыть дело <ExternalLink size={12} />
                        </a>
                      </div>
                    </article>
                  ))
                  : visibleItems.map((item, i) => (
                    <CaseItem key={item.caseId} item={item} token={token} index={i} />
                  ))}
              </motion.div>
            )}
          </AnimatePresence>
        </main>

        {!loading && !error && (
          <Pagination
            total={activeItems.length}
            current={page}
            onChange={p => { setPage(p); window.scrollTo({ top: 0, behavior: 'smooth' }) }}
          />
        )}
      </div>
    </div>
  )
}
