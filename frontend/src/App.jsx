import { useState, useEffect, useCallback, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Search, ExternalLink, ChevronDown, Scale, Clock, FileText, Zap } from 'lucide-react'
import './App.css'

const API_URL = window.__API_URL__ || 'http://localhost:8000'
const PAGE_SIZE = 10

function getQuery() {
  return new URLSearchParams(window.location.search)
}

async function ensureAuth() {
  const query = getQuery()
  const memberId = query.get('member_id')
  const userId = query.get('user_id')
  const domain = query.get('DOMAIN') || query.get('domain')

  let token = localStorage.getItem('access_token')
  if (!token) {
    if (memberId && userId) {
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
  }
  if (!token) throw new Error('Нет access token. Для локального запуска включите ALLOW_LOCAL_DEV_AUTH=true.')
  return token
}

function formatDate(value) {
  if (!value) return '—'
  return new Date(value).toLocaleString('ru-RU', {
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit'
  })
}

async function fetchCases(token, search = '') {
  const params = new URLSearchParams()
  if (search) params.set('search', search)
  const resp = await fetch(`${API_URL}/cases?${params.toString()}`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!resp.ok) throw new Error('Ошибка загрузки дел')
  return resp.json()
}

async function fetchHistory(token, caseId) {
  const resp = await fetch(`${API_URL}/cases/${caseId}/history`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!resp.ok) throw new Error('Ошибка загрузки истории')
  return resp.json()
}

function HistoryRow({ item, index }) {
  return (
    <motion.div
      className="history-row"
      initial={{ opacity: 0, x: -12 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ delay: index * 0.055, duration: 0.3, ease: 'easeOut' }}
    >
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
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [compact, setCompact] = useState(false)
  const debounceRef = useRef(null)

  useEffect(() => {
    const query = getQuery()
    const placement = query.get('PLACEMENT') || ''
    if (placement.startsWith('CRM_DEAL_DETAIL')) setCompact(true)

    ensureAuth()
      .then(t => setToken(t))
      .catch(e => { setError(e.message); setLoading(false) })
  }, [])

  const load = useCallback(async (tok, q) => {
    setLoading(true)
    try {
      const list = await fetchCases(tok, q)
      setCases(list)
      setPage(1)
    } catch (e) {
      setError(e.message)
    }
    setLoading(false)
  }, [])

  useEffect(() => {
    if (!token) return
    load(token, '')
  }, [token, load])

  const handleSearch = (val) => {
    setSearch(val)
    clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      if (token) load(token, val)
    }, 350)
  }

  const visibleCases = cases.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)

  return (
    <div className={`app-root ${compact ? 'compact' : ''}`}>
      <div className="orb orb1" />
      <div className="orb orb2" />
      <div className="orb orb3" />

      <div className="container">
        <motion.header
          className="header"
          initial={{ opacity: 0, y: -20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
        >
          <div className="brand">
            <div className="brand-icon">
              <Scale size={18} strokeWidth={1.5} />
            </div>
            <div>
              <h1>Арбитражные дела</h1>
              <p>Мониторинг событий КАД в реальном времени</p>
            </div>
          </div>

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

          {!loading && !error && (
            <motion.div
              className="cases-count"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: 0.5 }}
            >
              {cases.length} {cases.length === 1 ? 'дело' : cases.length < 5 ? 'дела' : 'дел'}
            </motion.div>
          )}
        </motion.header>

        <main className="main">
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

          {!loading && !error && cases.length === 0 && (
            <motion.div className="empty-state" initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }}>
              <Scale size={44} strokeWidth={0.8} />
              <p>Ничего не найдено</p>
            </motion.div>
          )}

          <AnimatePresence mode="wait">
            {!loading && !error && visibleCases.length > 0 && (
              <motion.div key={`page-${page}-${search}`}>
                {visibleCases.map((item, i) => (
                  <CaseItem key={item.caseId} item={item} token={token} index={i} />
                ))}
              </motion.div>
            )}
          </AnimatePresence>
        </main>

        {!loading && !error && (
          <Pagination
            total={cases.length}
            current={page}
            onChange={p => { setPage(p); window.scrollTo({ top: 0, behavior: 'smooth' }) }}
          />
        )}
      </div>
    </div>
  )
}
