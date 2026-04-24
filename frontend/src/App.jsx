import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Search, ExternalLink, ChevronDown, Scale, Clock, FileText, Zap } from 'lucide-react'
import './App.css'

const API_URL = window.__API_URL__ || '/api'
const DEFAULT_PAGE_SIZE = 10
const PAGE_SIZE_OPTIONS = [5, 10, 25, 50]
const CASE_NUMBER_FIELDS = Array.isArray(window.__CASE_NUMBER_FIELDS__)
  ? window.__CASE_NUMBER_FIELDS__
  : ['UF_CRM_1708426613594', 'UF_CRM_CASE_NUMBER', 'UF_CRM_1699999999', 'CASE_NUMBER']
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
    console.warn('[Casebook widget] Failed to parse PLACEMENT_OPTIONS as JSON', { raw })
    return {}
  }
}

async function ensureBx24() {
  if (window.BX24 && typeof window.BX24.callMethod === 'function') {
    return window.BX24
  }

  const existingScript = document.querySelector('script[data-bx24-api="1"]')
  if (!existingScript) {
    const script = document.createElement('script')
    script.src = 'https://api.bitrix24.com/api/v1/'
    script.async = true
    script.dataset.bx24Api = '1'
    document.head.appendChild(script)
  }

  return new Promise((resolve) => {
    const startedAt = Date.now()
    const tick = () => {
      if (window.BX24 && typeof window.BX24.callMethod === 'function') {
        resolve(window.BX24)
        return
      }
      if (Date.now() - startedAt > 5000) {
        resolve(null)
        return
      }
      setTimeout(tick, 50)
    }
    tick()
  })
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

async function apiPost(path, token, payload = {}) {
  const headers = {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  }
  const resp = await fetch(`${API_URL}${path}`, {
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
  })
  if (!resp.ok) {
    const body = await resp.text()
    throw new Error(body || `Ошибка запроса ${path}`)
  }
  return resp.json()
}

async function fetchCasesPage(token, search = '', caseNumber = '', page = 1, pageSize = DEFAULT_PAGE_SIZE) {
  const params = new URLSearchParams()
  if (caseNumber) params.set('case_number', caseNumber)
  else if (search) params.set('search', search)
  params.set('page', String(page))
  params.set('page_size', String(pageSize))
  return apiGet(`/cases?${params.toString()}`, token)
}

async function fetchAllEventsPage(token, caseNumber = '', document = '', dateFrom = '', dateTo = '', page = 1, pageSize = DEFAULT_PAGE_SIZE) {
  const params = new URLSearchParams()
  if (caseNumber) params.set('case_number', caseNumber)
  if (document) params.set('document', document)
  if (dateFrom) params.set('date_from', dateFrom)
  if (dateTo) params.set('date_to', dateTo)
  params.set('page', String(page))
  params.set('page_size', String(pageSize))
  return apiGet(`/events/history?${params.toString()}`, token)
}

async function fetchHistory(token, caseId) {
  return apiGet(`/cases/${caseId}/history`, token)
}

async function triggerFullSync(token) {
  return apiPost('/admin/sync/full', token, {})
}

async function getEntityCaseNumber() {
  const query = getQuery()
  const placement = query.get('PLACEMENT') || ''
  const placementOptions = parsePlacementOptions(query.get('PLACEMENT_OPTIONS'))
  const entityId = query.get('ID') || query.get('ENTITY_ID') || placementOptions.ID || placementOptions.ENTITY_ID
  if (!entityId) {
    console.warn('[Casebook widget] Entity ID not found in query/PLACEMENT_OPTIONS', {
      placement,
      query: window.location.search,
      placementOptions,
    })
    return null
  }

  const presetCaseNumber = query.get('case_number') || placementOptions.case_number
  if (presetCaseNumber) return presetCaseNumber

  const bx24 = await ensureBx24()
  if (!bx24) {
    console.error('[Casebook widget] BX24 API is unavailable. Cannot load CRM entity fields.')
    return null
  }

  let method = 'crm.deal.get'
  if (placement.startsWith('CRM_CONTACT_DETAIL')) method = 'crm.contact.get'
  if (placement.startsWith('CRM_COMPANY_DETAIL')) method = 'crm.company.get'

  return new Promise((resolve) => {
    const runCall = () => {
      bx24.callMethod(method, { id: entityId }, (result) => {
        if (!result || !result.data) {
          console.warn('[Casebook widget] BX24.callMethod returned empty result', { method, entityId, result })
          resolve(null)
          return
        }
        const entity = result.data()
        try {
          console.groupCollapsed(`[Casebook widget] ${method} fields for entity ${entityId}`)
          console.log('Entity payload:', entity)
          console.log('Entity field keys:', Object.keys(entity || {}))
          console.log('CASE_NUMBER_FIELDS:', CASE_NUMBER_FIELDS)
          console.groupEnd()
        } catch {
          console.log('[Casebook widget] Entity payload:', entity)
        }
        const value = CASE_NUMBER_FIELDS.map((field) => entity[field]).find(Boolean)
        resolve(typeof value === 'string' ? value.trim() : value || null)
      })
    }

    if (typeof bx24.init === 'function') {
      bx24.init(() => runCall())
    } else {
      runCall()
    }
  })
}

function logWidgetBootstrap(mode) {
  if (mode !== 'widget') return
  const query = getQuery()
  console.groupCollapsed('[Casebook widget] bootstrap')
  console.log('Location:', window.location.href)
  console.log('PLACEMENT:', query.get('PLACEMENT'))
  console.log('PLACEMENT_OPTIONS(raw):', query.get('PLACEMENT_OPTIONS'))
  console.log('CASE_NUMBER_FIELDS:', CASE_NUMBER_FIELDS)
  console.groupEnd()
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

function Pagination({ total, current, pageSize, onChange }) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  if (total <= pageSize) return null

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

function normalizePagePayload(payload, fallbackPage, fallbackPageSize) {
  if (Array.isArray(payload)) {
    return {
      items: payload,
      pagination: {
        total: payload.length,
        page: fallbackPage,
        pageSize: fallbackPageSize,
      },
    }
  }

  return {
    items: payload?.items || [],
    pagination: {
      total: payload?.pagination?.total || 0,
      page: payload?.pagination?.page || fallbackPage,
      pageSize: payload?.pagination?.pageSize || fallbackPageSize,
    },
  }
}

function toDateTimeLocalValue(date) {
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000)
  return local.toISOString().slice(0, 16)
}

function applyMidnightDefault(value, previousValue) {
  if (!value || previousValue) return value
  const selectedTime = value.slice(11, 16)
  const currentTime = toDateTimeLocalValue(new Date()).slice(11, 16)
  if (selectedTime === currentTime) {
    return `${value.slice(0, 10)}T00:00`
  }
  return value
}

export default function App() {
  const [token, setToken] = useState(null)
  const [cases, setCases] = useState([])
  const [events, setEvents] = useState([])
  const [widgetEvents, setWidgetEvents] = useState([])
  const [totalItems, setTotalItems] = useState(0)
  const [widgetCaseLink, setWidgetCaseLink] = useState('')
  const [caseSearch, setCaseSearch] = useState('')
  const [documentSearch, setDocumentSearch] = useState('')
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE)
  const [page, setPage] = useState(1)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [compact, setCompact] = useState(false)
  const [widgetCaseNumber, setWidgetCaseNumber] = useState('')
  const [mode, setMode] = useState('local')
  const [tab, setTab] = useState('cases')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [secretTapCount, setSecretTapCount] = useState(0)
  const [secretTapStartedAt, setSecretTapStartedAt] = useState(0)
  const [syncAlert, setSyncAlert] = useState(null)
  const [debouncedCaseSearch, setDebouncedCaseSearch] = useState('')
  const [debouncedDocumentSearch, setDebouncedDocumentSearch] = useState('')
  const debounceRef = useRef(null)

  useEffect(() => {
    const query = getQuery()
    const placement = query.get('PLACEMENT') || ''
    const isWidget = WIDGET_PLACEMENTS.some((item) => placement.startsWith(item))

    setMode(isWidget ? 'widget' : 'local')
    if (isWidget) setCompact(true)
    logWidgetBootstrap(isWidget ? 'widget' : 'local')

    ensureAuth()
      .then(t => setToken(t || null))
      .catch(() => setToken(null))

    if (isWidget) {
      getEntityCaseNumber().then((value) => setWidgetCaseNumber(value || ''))
    }
  }, [])

  const load = useCallback(async (tok, caseQuery, documentQuery, currentTab, currentMode, caseNumber, fromDate, toDate, currentPage, currentPageSize) => {
    if (!tok) return
    setLoading(true)
    setError(null)
    try {
      if (currentMode === 'widget') {
        const payload = await fetchCasesPage(tok, caseQuery, caseNumber, 1, 1)
        const normalized = normalizePagePayload(payload, 1, 1)
        setCases(normalized.items)
        const widgetCase = normalized.items[0]
        setWidgetCaseLink(widgetCase?.caseLink || '')
        if (widgetCase?.caseId) {
          const history = await fetchHistory(tok, widgetCase.caseId)
          const sorted = [...history].sort((a, b) => new Date(b.findDate) - new Date(a.findDate))
          setWidgetEvents(sorted)
          setTotalItems(sorted.length)
        } else {
          setWidgetEvents([])
          setTotalItems(0)
        }
      } else if (currentTab === 'events' && currentMode === 'local') {
        const payload = await fetchAllEventsPage(tok, caseQuery, documentQuery, fromDate, toDate, currentPage, currentPageSize)
        const normalized = normalizePagePayload(payload, currentPage, currentPageSize)
        setEvents(normalized.items)
        setTotalItems(normalized.pagination.total)
      } else {
        const payload = await fetchCasesPage(tok, caseQuery, '', currentPage, currentPageSize)
        const normalized = normalizePagePayload(payload, currentPage, currentPageSize)
        setCases(normalized.items)
        setTotalItems(normalized.pagination.total)
      }
    } catch (e) {
      setError(e.message)
    }
    setLoading(false)
  }, [])

  useEffect(() => {
    clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      setDebouncedCaseSearch(caseSearch)
      setPage(1)
    }, 350)
    return () => clearTimeout(debounceRef.current)
  }, [caseSearch])

  useEffect(() => {
    const id = setTimeout(() => {
      setDebouncedDocumentSearch(documentSearch)
      setPage(1)
    }, 350)
    return () => clearTimeout(id)
  }, [documentSearch])

  useEffect(() => {
    if (mode === 'widget' && !widgetCaseNumber) return
    load(token, debouncedCaseSearch, debouncedDocumentSearch, tab, mode, widgetCaseNumber, dateFrom, dateTo, page, pageSize)
  }, [token, load, tab, mode, widgetCaseNumber, dateFrom, dateTo, debouncedCaseSearch, debouncedDocumentSearch, page, pageSize])

  const handleCaseSearch = (val) => setCaseSearch(val)

  const handleDocumentSearch = (val) => setDocumentSearch(val)

  const activeItems = useMemo(() => {
    if (mode === 'widget') return widgetEvents
    return tab === 'events' ? events : cases
  }, [mode, tab, events, cases, widgetEvents])
  const visibleItems = mode === 'local' ? activeItems : activeItems.slice((page - 1) * pageSize, page * pageSize)

  const setNow = (target) => {
    const now = toDateTimeLocalValue(new Date())
    if (target === 'from') setDateFrom(now)
    if (target === 'to') setDateTo(now)
    setPage(1)
  }

  const applyDatePreset = (preset) => {
    const now = new Date()
    let from = ''
    let to = toDateTimeLocalValue(now)

    if (preset === 'today') {
      const start = new Date(now)
      start.setHours(0, 0, 0, 0)
      from = toDateTimeLocalValue(start)
    }

    if (preset === 'last7') {
      const start = new Date(now)
      start.setDate(start.getDate() - 7)
      from = toDateTimeLocalValue(start)
    }

    if (preset === 'thisMonth') {
      const start = new Date(now.getFullYear(), now.getMonth(), 1, 0, 0, 0)
      from = toDateTimeLocalValue(start)
    }

    setDateFrom(from)
    setDateTo(to)
    setPage(1)
  }

  const handleSecretSyncTap = async () => {
    const now = Date.now()
    const withinWindow = now - secretTapStartedAt < 6000
    const nextCount = withinWindow ? secretTapCount + 1 : 1
    setSecretTapStartedAt(withinWindow ? secretTapStartedAt : now)
    setSecretTapCount(nextCount)

    if (nextCount < 7) return
    setSecretTapCount(0)
    setSecretTapStartedAt(0)

    setSyncAlert({ type: 'info', text: 'Запущено обновление всех данных. Это может занять несколько минут.' })
    try {
      const response = await triggerFullSync(token)
      const stats = response?.result || {}
      if (typeof stats.started === 'boolean') {
        setSyncAlert({
          type: stats.started ? 'success' : 'info',
          text: stats.message || (stats.started ? 'Полная синхронизация запущена.' : 'Полная синхронизация уже выполняется.'),
        })
        return
      }
      setSyncAlert({
        type: 'success',
        text: `Обновление завершено. Получено: ${stats.fetched ?? 0}, добавлено: ${stats.inserted ?? 0}, обновлено: ${stats.updated ?? 0}, пропущено: ${stats.skipped ?? 0}.`,
      })
    } catch {
      setSyncAlert({
        type: 'error',
        text: 'Не удалось запустить полную синхронизацию. Проверьте права доступа и настройки сервера.',
      })
    }
  }

  return (
    <div className={`app-root ${compact ? 'compact' : ''}`}>
      <div className="orb orb1" />
      <div className="orb orb2" />
      <div className="orb orb3" />

      <div className="container">
        <header className="header">
          <div className="brand">
            <div className="brand-icon">
              <button className="secret-sync-trigger" type="button" onClick={handleSecretSyncTap} aria-label="sync trigger">
                <Scale size={18} strokeWidth={1.5} />
              </button>
            </div>
            <div>
              <h1>{mode === 'widget' ? `Арбитражное дело ${widgetCaseNumber || ''}`.trim() : 'Арбитражные дела'}</h1>
              <p>{mode === 'widget' ? 'История по делу из карточки пользователя' : 'Мониторинг событий КАД в реальном времени'}</p>
              {mode === 'widget' && widgetCaseLink && (
                <a
                  className="case-link timeline-link"
                  href={widgetCaseLink}
                  target="_blank"
                  rel="noreferrer"
                >
                  Открыть дело в КАД <ExternalLink size={12} />
                </a>
              )}
            </div>
          </div>

          {mode === 'local' && (
            <div className="local-controls">
              <div className="tabs">
                <button className={`tab-btn ${tab === 'cases' ? 'active' : ''}`} onClick={() => { setTab('cases'); setPage(1) }}>Список дел</button>
                <button className={`tab-btn ${tab === 'events' ? 'active' : ''}`} onClick={() => { setTab('events'); setPage(1) }}>Общая лента</button>
              </div>

              <div className="search-row">
                <div className="search-wrap">
                  <Search size={14} className="search-icon-el" />
                  <input
                    className="search-input"
                    type="text"
                    value={caseSearch}
                    onChange={e => handleCaseSearch(e.target.value)}
                    placeholder="Поиск по номеру дела…"
                  />
                  <AnimatePresence>
                    {caseSearch && (
                      <motion.button
                        className="search-clear"
                        initial={{ opacity: 0, scale: 0.6 }}
                        animate={{ opacity: 1, scale: 1 }}
                        exit={{ opacity: 0, scale: 0.6 }}
                        onClick={() => handleCaseSearch('')}
                      >×</motion.button>
                    )}
                  </AnimatePresence>
                </div>

                {tab === 'events' && (
                  <div className="search-wrap">
                    <Search size={14} className="search-icon-el" />
                    <input
                      className="search-input"
                      type="text"
                      value={documentSearch}
                      onChange={e => handleDocumentSearch(e.target.value)}
                      placeholder="Поиск по документу…"
                    />
                    <AnimatePresence>
                      {documentSearch && (
                        <motion.button
                          className="search-clear"
                          initial={{ opacity: 0, scale: 0.6 }}
                          animate={{ opacity: 1, scale: 1 }}
                          exit={{ opacity: 0, scale: 0.6 }}
                          onClick={() => handleDocumentSearch('')}
                        >×</motion.button>
                      )}
                    </AnimatePresence>
                  </div>
                )}
              </div>

              <div className="meta-row">
                <label className="page-size-control">
                  На странице
                  <select
                    value={pageSize}
                    onChange={(e) => {
                      const nextSize = Number(e.target.value)
                      setPage(1)
                      setPageSize(nextSize)
                    }}
                  >
                    {PAGE_SIZE_OPTIONS.map((sizeOption) => (
                      <option key={sizeOption} value={sizeOption}>{sizeOption}</option>
                    ))}
                  </select>
                </label>

                <div className="cases-count">{totalItems} записей</div>
              </div>
            </div>
          )}
        </header>

        {mode === "local" && tab === "events" && (
          <section className="range-panel">
            <div className="range-presets">
              <button type="button" className="range-preset" onClick={() => applyDatePreset('today')}>Сегодня</button>
              <button type="button" className="range-preset" onClick={() => applyDatePreset('last7')}>7 дней</button>
              <button type="button" className="range-preset" onClick={() => applyDatePreset('thisMonth')}>Этот месяц</button>
            </div>
            <label className="range-field">
              С
              <input
                type="datetime-local"
                value={dateFrom}
                onChange={(e) => {
                  setDateFrom(applyMidnightDefault(e.target.value, dateFrom))
                  setPage(1)
                }}
              />
              <button type="button" className="range-now" onClick={() => setNow('from')}>Сейчас</button>
            </label>
            <label className="range-field">
              По
              <input
                type="datetime-local"
                value={dateTo}
                onChange={(e) => {
                  setDateTo(applyMidnightDefault(e.target.value, dateTo))
                  setPage(1)
                }}
              />
              <button type="button" className="range-now" onClick={() => setNow('to')}>Сейчас</button>
            </label>
            <button
              type="button"
              className="range-reset"
              onClick={() => {
                setDateFrom("")
                setDateTo("")
                setPage(1)
              }}
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
              <motion.div key={`page-${page}-${caseSearch}-${documentSearch}-${tab}-${pageSize}`}>
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
                  : mode === 'widget'
                    ? visibleItems.map((item, i) => (
                      <article key={`${item.caseId || 'widget'}-${i}`} className="case-item">
                        <div className="history-panel">
                          <HistoryRow item={item} index={i} />
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

        {!loading && !error && mode === 'local' && (
          <Pagination
            total={totalItems}
            current={page}
            pageSize={pageSize}
            onChange={p => { setPage(p); window.scrollTo({ top: 0, behavior: 'smooth' }) }}
          />
        )}
      </div>
      <AnimatePresence>
        {syncAlert && (
          <motion.div
            className={`sync-alert ${syncAlert.type}`}
            initial={{ opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 14 }}
          >
            <span>{syncAlert.text}</span>
            <button type="button" onClick={() => setSyncAlert(null)}>×</button>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
