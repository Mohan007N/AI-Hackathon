import React, { useEffect, useMemo, useState } from 'react'
import axios from 'axios'
import {
  Activity, AlertCircle, AlertOctagon, AlertTriangle, ArrowRight, ArrowUpRight,
  Bot, Check, CheckCircle2, ChevronRight, Clock3, Eye, EyeOff, FileText,
  Globe2, History, Info, LayoutDashboard, Link2, ListChecks, LoaderCircle, LockKeyhole,
  LogOut, Menu, Plus, Search, Send, ShieldCheck, Sparkles, Trash2, User,
  UserPlus, X, Zap
} from 'lucide-react'

const API_BASE = (import.meta.env.VITE_AUTOMATION_API_URL || '').trim().replace(/\/$/, '')
const USERS_KEY = 'campuspilot-demo-users-v2'
const SESSION_KEY = 'campuspilot-demo-session-v2'
const RECORDS_PREFIX = 'campuspilot-records-v2:'
const LOGS_PREFIX = 'campuspilot-activity-v2:'
const APPROVALS_PREFIX = 'campuspilot-approvals-v2:'

const BLOCKED_DOMAINS = [
  { domain: 'pornhub.com', category: 'adult-content' },
  { domain: 'pornhub.org', category: 'adult-content' },
  { domain: 'xvideos.com', category: 'adult-content' },
  { domain: 'xnxx.com', category: 'adult-content' },
  { domain: 'xhamster.com', category: 'adult-content' },
  { domain: 'redtube.com', category: 'adult-content' },
  { domain: 'youporn.com', category: 'adult-content' },
  { domain: 'tube8.com', category: 'adult-content' },
  { domain: 'spankbang.com', category: 'adult-content' },
  { domain: 'fitgirl-repacks.site', category: 'piracy-or-cracked-software' },
  { domain: 'dodi-repacks.site', category: 'piracy-or-cracked-software' },
  { domain: 'dodi-repacks.download', category: 'piracy-or-cracked-software' },
  { domain: '1337x.to', category: 'piracy-or-cracked-software' },
  { domain: 'thepiratebay.org', category: 'piracy-or-cracked-software' },
  { domain: 'torrentgalaxy.to', category: 'piracy-or-cracked-software' },
  { domain: 'skidrowreloaded.com', category: 'piracy-or-cracked-software' },
  { domain: 'steamunlocked.net', category: 'piracy-or-cracked-software' },
]

const examples = [
  'Open my university portal and check my attendance for this semester.',
  'Search the website for the latest invoice and download the PDF.',
  'Find the return policy on this store and summarize it.',
  'Compare the listed product specifications on this website.',
  'Fill out this form with the information I provide, then ask me before submitting.'
]

const isoNow = () => new Date().toISOString()
const makeId = () => `CP-${Date.now().toString().slice(-7)}`
const formatDate = value => new Intl.DateTimeFormat('en-IN', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(value))
const hostOf = value => { try { return new URL(value).host } catch { return value || '—' } }
const safeUrlForRecords = value => {
  try {
    const parsed = new URL(value)
    parsed.username = ''
    parsed.password = ''
    for (const key of [...parsed.searchParams.keys()]) if (/(token|password|passwd|secret|api.?key|auth|session|credential|access.?key|code)/i.test(key)) parsed.searchParams.delete(key)
    parsed.hash = ''
    return parsed.href
  } catch { return value || '' }
}
const redactSecrets = (value, secrets = []) => secrets.filter(Boolean).reduce((text, secret) => text.split(String(secret)).join('[redacted]'), String(value ?? ''))
const recordsKey = email => `${RECORDS_PREFIX}${email.toLowerCase()}`
const logsKey = email => `${LOGS_PREFIX}${email.toLowerCase()}`
const approvalsKey = email => `${APPROVALS_PREFIX}${email.toLowerCase()}`

function readArray(key) {
  try { const parsed = JSON.parse(localStorage.getItem(key) || '[]'); return Array.isArray(parsed) ? parsed : [] }
  catch { return [] }
}
function loadUsers() {
  try { const parsed = JSON.parse(localStorage.getItem(USERS_KEY) || '[]'); return Array.isArray(parsed) ? parsed : [] }
  catch { return [] }
}
function saveArray(key, value) { localStorage.setItem(key, JSON.stringify(value)) }

async function passwordVerifier(password, saltHex) {
  const encoder = new TextEncoder()
  const salt = Uint8Array.from(saltHex.match(/.{1,2}/g).map(pair => parseInt(pair, 16)))
  const material = await crypto.subtle.importKey('raw', encoder.encode(password), 'PBKDF2', false, ['deriveBits'])
  const bits = await crypto.subtle.deriveBits({ name: 'PBKDF2', salt, iterations: 150000, hash: 'SHA-256' }, material, 256)
  return [...new Uint8Array(bits)].map(byte => byte.toString(16).padStart(2, '0')).join('')
}
function randomSalt() {
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  return [...bytes].map(byte => byte.toString(16).padStart(2, '0')).join('')
}

function checkWebsiteSafety(rawUrl) {
  let parsed
  try { parsed = new URL(rawUrl.trim()) } catch { return { safe: false, reason: 'Enter a valid website URL, including https:// or http://.' } }
  if (!['https:', 'http:'].includes(parsed.protocol)) return { safe: false, reason: 'Enter a valid website URL.' }
  if (parsed.username || parsed.password) return { safe: false, reason: 'Do not place login credentials inside the website URL. Use the secure sign-in details option instead.' }
  const privateQueryKey = [...parsed.searchParams.keys()].find(key => /(token|password|passwd|secret|api.?key|auth|session|credential|access.?key|code)/i.test(key))
  if (privateQueryKey) return { safe: false, reason: 'This URL appears to contain a sign-in token or secret in its query string. Remove it and use the one-time sign-in details option instead.' }

  const isPrivateOrLocal = ['localhost', '127.0.0.1'].includes(parsed.hostname) ||
    /^10\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(parsed.hostname) ||
    /^172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}$/.test(parsed.hostname) ||
    /^192\.168\.\d{1,3}\.\d{1,3}$/.test(parsed.hostname)

  if (parsed.protocol !== 'https:' && !isPrivateOrLocal) {
    return { safe: false, reason: 'Use an HTTPS website so information is protected in transit (HTTP is only allowed for local or intranet portals).' }
  }
  const host = parsed.hostname.toLowerCase().replace(/\.$/, '')
  const match = BLOCKED_DOMAINS.find(item => host === item.domain || host.endsWith(`.${item.domain}`))
  if (match) {
    const kind = match.category === 'adult-content' ? 'adult-content website' : 'piracy or cracked-software distribution website'
    return { safe: false, reason: `This destination is blocked by CampusPilot's safety rules because it is identified as an ${kind}. Browser automation will not be started for this website.` }
  }
  return { safe: true, host: parsed.host, normalizedUrl: parsed.href }
}

function classifyInstruction(instruction) {
  const text = instruction.toLowerCase()
  const action = /\b(open|visit|navigate|browse|search|find|look up|check|read|extract|summari[sz]e|compare|download|upload|fill|complete|submit|send|post|pay|purchase|book|order|cancel|delete|remove|edit|update|change|register|sign in|log in|sign out|export|print|select|review|verify|retrieve|fetch|collect|save)\b/i.test(text)
  if (!action || text.trim().length < 10) return { valid: false, sensitive: false, reason: 'I could not identify a clear action. Tell CampusPilot which website action you want, such as search, read, compare, download, or fill a form.' }
  const sensitivePattern = /\b(submit|send|post|pay|payment|purchase|buy|book|order|cancel|delete|remove|edit|update|change|register|transfer|withdraw|confirm|finali[sz]e|upload|fill|place an order|make a payment)\b/i
  const sensitive = sensitivePattern.test(text)
  let category = 'Website navigation and information'
  if (/download|export|print/i.test(text)) category = 'Download or export'
  else if (/submit|upload|send|post|fill|complete/i.test(text)) category = 'Form or content submission'
  else if (/pay|payment|purchase|buy|book|order|transfer/i.test(text)) category = 'Financial or booking action'
  else if (/delete|remove|edit|update|change|cancel|register/i.test(text)) category = 'Account or data modification'
  return { valid: true, sensitive, category }
}

function StatusPill({ status }) {
  const kind = status === 'Completed' ? 'success' : status === 'Needs approval' ? 'warning' : ['Invalid request', 'Blocked by safety policy', 'Failed'].includes(status) ? 'danger' : status === 'Not executed' || status === 'Cancelled' ? 'neutral' : 'info'
  return <span className={`status-pill ${kind}`}><i />{status}</span>
}

function Field({ label, children, hint }) {
  return <label className="field"><span className="field-label">{label}</span>{children}{hint && <small className="field-hint">{hint}</small>}</label>
}

function AuthScreen({ onAuthenticated }) {
  const [mode, setMode] = useState('login')
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  const changeMode = next => { setMode(next); setError(''); setMessage(''); setPassword(''); setConfirm('') }
  const submit = async event => {
    event.preventDefault(); setError(''); setMessage('')
    const cleanEmail = email.trim().toLowerCase()
    if (!/^\S+@\S+\.\S+$/.test(cleanEmail)) { setError('Enter a valid email address.'); return }
    if (password.length < 8) { setError('Use a password with at least 8 characters.'); return }
    setBusy(true)
    try {
      const users = loadUsers()
      if (mode === 'signup') {
        if (!name.trim()) { setError('Enter your name.'); return }
        if (password !== confirm) { setError('The passwords do not match.'); return }
        if (users.some(user => user.email === cleanEmail)) { setError('An account with this email already exists. Please sign in.'); return }
        const salt = randomSalt()
        const verifier = await passwordVerifier(password, salt)
        const user = { name: name.trim(), email: cleanEmail, salt, verifier, createdAt: isoNow() }
        saveArray(USERS_KEY, [...users, user])
        onAuthenticated({ name: user.name, email: user.email })
      } else {
        const user = users.find(item => item.email === cleanEmail)
        if (!user) { setError('No local demo account was found for this email. Create an account first.'); return }
        const verifier = await passwordVerifier(password, user.salt)
        if (verifier !== user.verifier) { setError('Incorrect password. Please try again.'); return }
        onAuthenticated({ name: user.name, email: user.email })
      }
    } catch (err) {
      setError(err?.message || 'Authentication could not be completed in this browser.')
    } finally { setBusy(false) }
  }

  return <main className="auth-shell">
    <div className="auth-brand"><div className="brand-mark"><Link2 size={19}/></div><div><strong>CampusPilot</strong><span>Personal web automation workspace</span></div></div>
    <div className="auth-content">
      <section className="auth-card">
        <div className="auth-icon"><Bot size={22}/></div>
        <div className="eyebrow">YOUR WEB TASK ASSISTANT</div>
        <h1>{mode === 'login' ? 'Welcome back' : 'Create your account'}</h1>
        <p className="auth-subtitle">{mode === 'login' ? 'Sign in to continue to your task workspace and saved records.' : 'Create a local demo account to keep your task records and activity in one place.'}</p>
        <div className="auth-tabs"><button className={mode === 'login' ? 'active' : ''} onClick={() => changeMode('login')} type="button">Sign in</button><button className={mode === 'signup' ? 'active' : ''} onClick={() => changeMode('signup')} type="button">Sign up</button></div>
        <form onSubmit={submit} className="auth-form">
          {mode === 'signup' && <Field label="Full name"><input value={name} onChange={e => setName(e.target.value)} placeholder="Your name" autoComplete="name" required/></Field>}
          <Field label="Email address"><input value={email} onChange={e => setEmail(e.target.value)} type="email" placeholder="you@example.com" autoComplete="email" required/></Field>
          <Field label="Password" hint={mode === 'signup' ? 'At least 8 characters.' : undefined}><div className="auth-password"><input value={password} onChange={e => setPassword(e.target.value)} type={showPassword ? 'text' : 'password'} placeholder="Enter your password" autoComplete={mode === 'signup' ? 'new-password' : 'current-password'} required/><button type="button" onClick={() => setShowPassword(v => !v)} aria-label="Toggle password visibility">{showPassword ? <EyeOff size={17}/> : <Eye size={17}/>}</button></div></Field>
          {mode === 'signup' && <Field label="Confirm password"><input value={confirm} onChange={e => setConfirm(e.target.value)} type="password" placeholder="Re-enter your password" autoComplete="new-password" required/></Field>}
          {error && <div className="alert alert-error"><AlertCircle size={16}/>{error}</div>}
          {message && <div className="alert alert-info"><InfoIcon/>{message}</div>}
          <button className="button primary auth-submit" type="submit" disabled={busy}>{busy ? <LoaderCircle className="spin" size={17}/> : mode === 'login' ? <LockKeyhole size={16}/> : <UserPlus size={16}/>} {mode === 'login' ? 'Sign in to CampusPilot' : 'Create account'} <ArrowRight size={16}/></button>
        </form>
        <div className="auth-footnote"><ShieldCheck size={15}/><span>Demo authentication is stored in this browser only. Production accounts require a secure backend.</span></div>
      </section>
      <aside className="auth-aside"><div className="aside-orb"><Sparkles size={26}/></div><div className="eyebrow">ONE PROMPT. MANY SITES.</div><h2>Your web tasks,<br/>under one roof.</h2><p>Enter a website, describe the outcome you need, and let your connected browser agent handle the repetitive steps.</p><div className="auth-feature"><span><Globe2 size={17}/></span><div><strong>Work across websites</strong><small>Not limited to college portals.</small></div></div><div className="auth-feature"><span><ShieldCheck size={17}/></span><div><strong>Safety and approvals</strong><small>Restricted destinations are blocked. Sensitive actions require review.</small></div></div><div className="auth-feature"><span><History size={17}/></span><div><strong>Your records stay separate</strong><small>Task records and a dedicated approval queue have separate views.</small></div></div></aside>
    </div>
    <div className="auth-footer">© 2026 CampusPilot <span>·</span> Frontend prototype</div>
  </main>
}

function InfoIcon() { return <Activity size={16}/> }

function ApprovalModal({ request, onApprove, onCancel }) {
  if (!request) return null
  return <div className="modal-backdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onCancel() }}>
    <section className="dialog-card" role="dialog" aria-modal="true" aria-labelledby="approval-title">
      <div className="dialog-icon warning"><AlertTriangle size={22}/></div>
      <div className="eyebrow">HUMAN APPROVAL REQUIRED</div>
      <h2 id="approval-title">Review before continuing</h2>
      <p className="dialog-copy">This instruction may submit information, spend money, or change an account or website record.</p>
      <div className="review-box"><div><span>WEBSITE</span><strong>{request.url}</strong></div><div><span>INSTRUCTION</span><strong>{request.prompt}</strong></div><div><span>WHY APPROVAL IS NEEDED</span><strong>{request.reason}</strong></div></div>
      <div className="alert alert-warning"><ShieldCheck size={16}/> Approval does not guarantee success. The connected automation service must still enforce permissions and report the actual result.</div>
      <div className="dialog-actions"><button className="button secondary" onClick={onCancel}>Cancel request</button><button className="button primary" onClick={onApprove}><Check size={16}/> Approve and continue</button></div>
    </section>
  </div>
}

function CredentialModal({ open, onClose, onSave }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [noLogin, setNoLogin] = useState(false)
  const [showPassword, setShowPassword] = useState(false)
  useEffect(() => { if (!open) { setUsername(''); setPassword(''); setNoLogin(false); setShowPassword(false) } }, [open])
  if (!open) return null
  const save = event => { event.preventDefault(); onSave({ username: noLogin ? '' : username.trim(), password: noLogin ? '' : password, noLogin }); onClose() }
  return <div className="modal-backdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}>
    <section className="dialog-card" role="dialog" aria-modal="true" aria-labelledby="credentials-title">
      <div className="dialog-icon"><LockKeyhole size={21}/></div><div className="eyebrow">OPTIONAL ONE-TIME SIGN-IN</div><h2 id="credentials-title">Website sign-in details</h2><p className="dialog-copy">These fields are only held temporarily for the current request. They are never included in saved task records or approval requests.</p>
      <form onSubmit={save} className="dialog-form">
        <Field label="Username / email"><input value={username} onChange={e => setUsername(e.target.value)} autoComplete="username" disabled={noLogin} placeholder="Website username or email"/></Field>
        <Field label="Password"><div className="auth-password"><input value={password} onChange={e => setPassword(e.target.value)} type={showPassword ? 'text' : 'password'} autoComplete="current-password" disabled={noLogin} placeholder="Website password"/><button type="button" onClick={() => setShowPassword(v => !v)} aria-label="Toggle password visibility">{showPassword ? <EyeOff size={17}/> : <Eye size={17}/>}</button></div></Field>
        <label className="check-line"><input type="checkbox" checked={noLogin} onChange={e => setNoLogin(e.target.checked)}/><span>This website does not require a sign-in</span></label>
        <div className="alert alert-info"><LockKeyhole size={15}/> In the frontend demo, credentials are not sent anywhere unless you configure an automation API.</div>
        <div className="dialog-actions"><button type="button" className="button secondary" onClick={onClose}>Cancel</button><button type="submit" className="button primary">Save for this request</button></div>
      </form>
    </section>
  </div>
}

function App() {
  const [user, setUser] = useState(() => { try { return JSON.parse(sessionStorage.getItem(SESSION_KEY) || 'null') } catch { return null } })
  const [view, setView] = useState('console')
  const [url, setUrl] = useState('')
  const [prompt, setPrompt] = useState('')
  const [credentials, setCredentials] = useState({ username: '', password: '', noLogin: false })
  const [credentialModal, setCredentialModal] = useState(false)
  const [records, setRecords] = useState([])
  const [logs, setLogs] = useState([])
  const [approvals, setApprovals] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [approval, setApproval] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [search, setSearch] = useState('')
  const [mobileMenu, setMobileMenu] = useState(false)

  useEffect(() => {
    if (user) {
      sessionStorage.setItem(SESSION_KEY, JSON.stringify(user))
      const savedRecords = readArray(recordsKey(user.email))
      setRecords(savedRecords)
      setLogs(readArray(logsKey(user.email)))
      setApprovals(readArray(approvalsKey(user.email)))
      setSelectedId(null)
      setView('console')
    } else {
      sessionStorage.removeItem(SESSION_KEY)
      setRecords([]); setLogs([]); setApprovals([]); setSelectedId(null)
    }
  }, [user?.email])

  const addLog = (event, details = {}) => {
    if (!user) return
    const item = { id: `LOG-${Date.now()}-${Math.random().toString(16).slice(2, 6)}`, at: isoNow(), event, website: safeUrlForRecords(details.website || ''), prompt: redactSecrets(details.prompt || '', details.secrets || [credentials.username, credentials.password]), detail: redactSecrets(details.detail || '', details.secrets || [credentials.username, credentials.password]), requestId: details.requestId || '' }
    setLogs(current => { const next = [item, ...current].slice(0, 300); saveArray(logsKey(user.email), next); return next })
  }

  const persistRecords = updater => {
    setRecords(current => {
      const next = typeof updater === 'function' ? updater(current) : updater
      saveArray(recordsKey(user.email), next)
      return next
    })
  }

  const persistApprovals = updater => {
    setApprovals(current => {
      const next = typeof updater === 'function' ? updater(current) : updater
      saveArray(approvalsKey(user.email), next)
      return next
    })
  }

  const selectedRecord = useMemo(() => records.find(record => record.id === selectedId) || null, [records, selectedId])
  const filteredRecords = useMemo(() => records.filter(record => `${record.url} ${record.prompt} ${record.status}`.toLowerCase().includes(search.toLowerCase())), [records, search])
  const filteredLogs = useMemo(() => logs.filter(log => `${log.event} ${log.website} ${log.prompt} ${log.detail} ${log.requestId}`.toLowerCase().includes(search.toLowerCase())), [logs, search])

  const authenticate = nextUser => {
    setUser(nextUser); sessionStorage.setItem(SESSION_KEY, JSON.stringify(nextUser))
  }

  const logout = () => {
    addLog('Signed out', { detail: 'The user ended the current CampusPilot session.' })
    setUser(null); setCredentials({ username: '', password: '', noLogin: false }); setView('console')
  }

  const validate = () => {
    setError(''); setNotice('')
    const safety = checkWebsiteSafety(url)
    if (!safety.safe) { setError(safety.reason); return { ok: false, safety } }
    if (prompt.trim().length < 10) { setError('Describe what you want CampusPilot to do in at least 10 characters.'); return { ok: false } }
    return { ok: true, safety }
  }

  const createRecord = (status, classification, safety, result, approved = false, steps = []) => ({
    id: makeId(), url: safeUrlForRecords(safety?.normalizedUrl || url.trim()), prompt: redactSecrets(prompt.trim(), [credentials.username, credentials.password]), status,
    createdAt: isoNow(), category: classification?.category || 'Safety review', result,
    approved, steps, host: safety?.host || hostOf(url)
  })

  const submitTask = async approved => {
    const checked = validate()
    if (!checked.ok) {
      if (checked.safety && !checked.safety.safe) {
        const blocked = createRecord('Blocked by safety policy', null, null, checked.safety.reason, false, ['Website URL checked', 'Destination matched a restricted category', 'Automation stopped before execution'])
        persistRecords(current => [blocked, ...current].slice(0, 200)); setSelectedId(blocked.id)
        addLog('Website blocked by safety rules', { website: url, prompt, detail: checked.safety.reason, requestId: blocked.id })
        setError(''); setNotice('This destination is blocked. No login details were used and no browser action was started.')
        setCredentials({ username: '', password: '', noLogin: false })
      }
      return
    }
    const classification = classifyInstruction(prompt)
    if (!classification.valid) {
      const invalid = createRecord('Invalid request', classification, checked.safety, classification.reason, false, ['Website URL validated', 'Instruction could not be mapped to a clear action'])
      persistRecords(current => [invalid, ...current].slice(0, 200)); setSelectedId(invalid.id)
      addLog('Invalid instruction rejected', { website: url, prompt, detail: classification.reason, requestId: invalid.id })
      setNotice('Request rejected before any browser interaction.'); setCredentials({ username: '', password: '', noLogin: false }); return
    }
    if (classification.sensitive && !approved) {
      const pending = createRecord('Needs approval', classification, checked.safety, 'Waiting for a human decision. No browser action has been started.', false, ['Website URL validated', 'Instruction classified as consequential', 'Waiting for human approval'])
      persistRecords(current => [pending, ...current].slice(0, 200))
      persistApprovals(current => [{ ...pending, reason: 'This instruction may submit information, change an account, make a payment, purchase, book, upload, or delete content.' }, ...current].slice(0, 100))
      setSelectedId(pending.id)
      addLog('Approval requested', { website: url, prompt, detail: 'Sensitive action saved to the Approval Queue. No browser action has been started.', requestId: pending.id })
      setCredentials({ username: '', password: '', noLogin: false })
      setPrompt('')
      setNotice('This task is waiting in Approvals. Review it there before any execution can be requested.')
      setView('approvals')
      return
    }
    const record = createRecord('Not executed', classification, checked.safety, '', approved, ['Website URL validated', 'Instruction classified', ...(classification.sensitive ? ['User approval recorded'] : []), 'Checking automation runtime'])
    persistRecords(current => [record, ...current].slice(0, 200)); setSelectedId(record.id)
    addLog(approved ? 'Sensitive action approved' : 'Task submitted', { website: url, prompt, detail: approved ? 'User approved the requested action. Runtime execution is still subject to backend checks.' : 'Task validated and submitted to the configured automation service, if available.', requestId: record.id })
    setBusy(true); setError(''); setNotice('')
    const oneTimeCredentials = { username: credentials.noLogin ? '' : credentials.username.trim(), password: credentials.noLogin ? '' : credentials.password }
    setCredentials({ username: '', password: '', noLogin: false })
    setPrompt('')

    if (API_BASE && !/^https:\/\//i.test(API_BASE) && !/^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?($|\/)/i.test(API_BASE)) {
      const message = 'The configured automation endpoint must use HTTPS. The request was not sent because the connection may expose sign-in details.'
      persistRecords(current => current.map(item => item.id === record.id ? { ...item, status: 'Failed', result: message, steps: [...record.steps, 'Insecure runtime endpoint rejected'] } : item))
      addLog('Insecure runtime endpoint rejected', { website: record.url, prompt: record.prompt, detail: message, requestId: record.id })
      setBusy(false); setNotice('Request not sent. Configure an HTTPS automation endpoint.'); return
    }

    if (!API_BASE) {
      const message = 'The website and instruction passed the local checks, but no browser automation runtime is connected. Nothing was opened or changed. Configure VITE_AUTOMATION_API_URL and a trusted backend to execute this request.'
      persistRecords(current => current.map(item => item.id === record.id ? { ...item, status: 'Not executed', result: message, steps: [...record.steps, 'Runtime unavailable — safely stopped'] } : item))
      addLog('Execution unavailable', { website: record.url, prompt: record.prompt, detail: 'No automation API endpoint is configured. No browser action was performed.', requestId: record.id })
      setBusy(false); setNotice('Request saved. Live browser automation is not connected.'); return
    }
    try {
      const payload = { portalUrl: checked.safety.normalizedUrl, url: checked.safety.normalizedUrl, ...oneTimeCredentials, instruction: record.prompt, prompt: record.prompt, userApproved: approved, source: 'campuspilot-dashboard' }
      const response = await axios.post(`${API_BASE}/api/automation/execute`, payload, { timeout: 120000, headers: { 'Content-Type': 'application/json' } })
      const data = response.data || {}
      const status = data.status === 'completed' ? 'Completed' : data.status === 'needs_approval' ? 'Needs approval' : data.status === 'invalid' ? 'Invalid request' : data.status === 'failed' ? 'Failed' : 'Not executed'
      const secrets = [oneTimeCredentials.username, oneTimeCredentials.password]
      const result = redactSecrets(data.message || data.summary || 'The automation service returned a response without a summary.', secrets)
      const steps = Array.isArray(data.steps) ? data.steps.map(step => redactSecrets(step, secrets)) : [...record.steps, 'Automation service responded']
      persistRecords(current => current.map(item => item.id === record.id ? { ...item, status, result, steps, runtimeTaskId: data.taskId || data.task_id || null } : item))
      if (data.status === 'needs_approval') {
        persistApprovals(current => [{ ...record, id: record.id, status: 'Needs approval', reason: result }, ...current.filter(a => a.id !== record.id)].slice(0, 100))
        setNotice(result)
        setView('approvals')
      }
      addLog(`Runtime result: ${status}`, { website: record.url, prompt: record.prompt, detail: result, requestId: record.id, secrets: [oneTimeCredentials.username, oneTimeCredentials.password] })
    } catch (runtimeError) {
      const detail = redactSecrets(runtimeError.response?.data?.message || runtimeError.message || 'Unknown connection error', [oneTimeCredentials.username, oneTimeCredentials.password])
      const result = `Could not reach the automation service: ${detail}. No success is assumed.`
      persistRecords(current => current.map(item => item.id === record.id ? { ...item, status: 'Failed', result, steps: [...record.steps, 'Backend request failed'] } : item))
      addLog('Automation runtime failed', { website: record.url, prompt: record.prompt, detail, requestId: record.id, secrets: [oneTimeCredentials.username, oneTimeCredentials.password] })
    } finally { setBusy(false) }
  }

  const onSubmit = event => {
    event.preventDefault()
    if (!busy) submitTask(false)
  }
  const decideApproval = async (item, decision) => {
    const nextStatus = decision === 'approved' ? 'Approved — not executed' : 'Rejected'
    persistApprovals(current => current.filter(entry => entry.id !== item.id))
    persistRecords(current => current.map(record => record.id === item.id ? {
      ...record,
      status: nextStatus,
      approved: decision === 'approved',
      result: decision === 'approved'
        ? 'Human approval recorded. Execution still requires a connected automation runtime and a separate backend authorization check.'
        : 'The request was rejected by the user. No browser action was performed.',
      steps: [...(record.steps || []), decision === 'approved' ? 'Human approval granted' : 'Human approval rejected']
    } : record))
    addLog(decision === 'approved' ? 'Human approval granted' : 'Human approval rejected', {
      website: item.url, prompt: item.prompt,
      detail: decision === 'approved'
        ? 'Approval recorded. No browser action is claimed; runtime execution remains separate.'
        : 'Request rejected. No browser action was performed.',
      requestId: item.id
    })
    if (decision === 'approved' && API_BASE) {
      try {
        const response = await axios.post(`${API_BASE}/api/automation/execute`, {
          url: item.url, portalUrl: item.url, instruction: item.prompt, prompt: item.prompt,
          userApproved: true, approvalRecordId: item.id, source: 'campuspilot-dashboard'
        }, { timeout: 120000, headers: { 'Content-Type': 'application/json' } })
        const data = response.data || {}
        const status = data.status === 'completed' ? 'Completed' : data.status === 'needs_approval' ? 'Needs approval' : data.status === 'invalid' ? 'Invalid request' : data.status === 'failed' ? 'Failed' : 'Not executed'
        persistRecords(current => current.map(record => record.id === item.id ? { ...record, status, result: data.message || data.summary || 'Automation service responded.', steps: [...(record.steps || []), ...(Array.isArray(data.steps) ? data.steps : ['Automation service responded'])] } : record))
        addLog(`Runtime result: ${status}`, { website: item.url, prompt: item.prompt, detail: data.message || data.summary || '', requestId: item.id })
      } catch (err) {
        const detail = err.response?.data?.message || err.message || 'Unknown runtime error'
        persistRecords(current => current.map(record => record.id === item.id ? { ...record, status: 'Failed', result: `Approval was recorded, but the runtime request failed: ${detail}` } : record))
        addLog('Approved task runtime failed', { website: item.url, prompt: item.prompt, detail, requestId: item.id })
      }
    }
  }
  const approve = () => { setApproval(null); submitTask(true) }
  const cancelApproval = () => { setApproval(null); setCredentials({ username: '', password: '', noLogin: false }); addLog('Approval declined', { website: url, prompt, detail: 'User cancelled the sensitive request.' }); setNotice('Request cancelled. No action was performed.') }
  const removeRecord = id => { persistRecords(current => current.filter(item => item.id !== id)); if (selectedId === id) setSelectedId(null); addLog('Task record deleted', { detail: `Record ${id} was removed from task records.` }) }
  const clearRecords = () => { persistRecords([]); setSelectedId(null); addLog('Task records cleared', { detail: 'All saved task records were cleared.' }) }
  const clearLogs = () => { saveArray(logsKey(user.email), []); setLogs([]) }

  if (!user) return <AuthScreen onAuthenticated={authenticate}/> 

  return <div className="app-shell">
    <aside className={`sidebar ${mobileMenu ? 'open' : ''}`}>
      <div className="brand-row"><div className="brand-mark"><Link2 size={18}/></div><div className="brand-copy"><strong>CampusPilot</strong><span>Web task assistant</span></div><button className="icon-button mobile-close" onClick={() => setMobileMenu(false)} aria-label="Close menu"><X size={18}/></button></div>
      <div className="side-label">WORKSPACE</div>
      <button className={`nav-item ${view === 'console' ? 'active' : ''}`} onClick={() => { setView('console'); setMobileMenu(false); setSearch('') }}><LayoutDashboard size={17}/><span>Task console</span></button>
      <button className={`nav-item ${view === 'records' ? 'active' : ''}`} onClick={() => { setView('records'); setMobileMenu(false); setSearch('') }}><FileText size={17}/><span>Past records</span><b>{records.length}</b></button>
      <button className={`nav-item ${view === 'approvals' ? 'active' : ''}`} onClick={() => { setView('approvals'); setMobileMenu(false); setSearch('') }}><ShieldCheck size={17}/><span>Approvals</span><b>{approvals.length}</b></button>
      <div className="sidebar-bottom"><div className="runtime-mini"><div className="runtime-mini-head"><Bot size={16}/><span>AGENT STATUS</span><i className={API_BASE ? 'online' : ''}/></div><strong>{API_BASE ? 'Runtime configured' : 'Runtime offline'}</strong><p>{API_BASE ? 'A backend endpoint is configured.' : 'Frontend mode · no live browser control.'}</p></div><div className="account-mini"><div className="user-avatar">{user.name.slice(0, 1).toUpperCase()}</div><div className="account-copy"><strong>{user.name}</strong><span>{user.email}</span></div><button className="icon-button logout-small" onClick={logout} title="Sign out"><LogOut size={16}/></button></div></div>
    </aside>
    {mobileMenu && <button className="mobile-scrim" onClick={() => setMobileMenu(false)} aria-label="Close navigation"/>}

    <main className="main-area">
      <header className="topbar"><div className="topbar-leading"><button className="icon-button mobile-menu" onClick={() => setMobileMenu(true)} aria-label="Open navigation"><Menu size={19}/></button><div className="crumb">Workspace <ChevronRight size={14}/> <strong>{view === 'console' ? 'Task console' : view === 'records' ? 'Past records' : 'Approvals'}</strong></div></div><div className="topbar-trailing"><span className={`runtime-badge ${API_BASE ? 'connected' : ''}`}><i/>{API_BASE ? 'Runtime configured' : 'Demo mode · runtime offline'}</span><div className="top-user"><div className="user-avatar small">{user.name.slice(0, 1).toUpperCase()}</div><span>{user.name}</span><button className="icon-button" onClick={logout} aria-label="Sign out" title="Sign out"><LogOut size={16}/></button></div></div></header>

      <div className="page-content">
        {view === 'console' && <>
          <section className="page-heading"><div><div className="eyebrow"><span/> ONE DASHBOARD. MANY WEBSITES.</div><h1>What would you like me to do?</h1><p>Enter any website and describe the task. CampusPilot checks the destination and requests approval for consequential actions.</p></div><div className="trust-chip"><ShieldCheck size={16}/> You stay in control</div></section>
          <div className="console-grid">
            <section className="panel task-panel">
              <div className="panel-head"><div className="panel-icon"><Globe2 size={19}/></div><div><h2>Create a browser task</h2><p>Website and natural-language instruction</p></div><span className="step-tag">NEW REQUEST</span></div>
              <form onSubmit={onSubmit}>
                <div className="panel-body">
                  <Field label="Website link" hint="Use the official website address. HTTPS is required for public sites."><div className="input-with-icon"><Link2 size={17}/><input type="url" value={url} onChange={event => { setUrl(event.target.value); setError('') }} placeholder="https://www.example.com" required/></div></Field>
                  <div className="prompt-topline"><span className="field-label">What should CampusPilot do?</span><span className="optional-label">NATURAL LANGUAGE</span></div>
                  <div className="prompt-box"><textarea value={prompt} onChange={event => setPrompt(event.target.value)} maxLength={1600} rows={5} placeholder="Example: Open the website, find the latest invoice, download it as a PDF, and tell me where it was saved." required/><div className="prompt-bottom"><span><Sparkles size={14}/> Clear instructions work best</span><span>{prompt.length}/1600</span></div></div>
                  <div className="credential-inline"><div className="credential-inline-icon"><LockKeyhole size={16}/></div><div><strong>Website sign-in details</strong><small>{credentials.noLogin ? 'No sign-in required for this request' : credentials.username || credentials.password ? 'Details added for this request · hidden from records' : 'Optional · only needed for sites that require login'}</small></div><button type="button" className="button subtle" onClick={() => setCredentialModal(true)}>{credentials.noLogin || credentials.username || credentials.password ? 'Edit details' : 'Add details'}</button></div>
                  <div className="example-section"><div className="example-title">TRY AN EXAMPLE <span>Select to fill the prompt</span></div><div className="example-list">{examples.map((example, index) => <button type="button" key={index} onClick={() => { setPrompt(example); setError('') }}>{example}<ArrowUpRight size={14}/></button>)}</div></div>
                  {error && <div className="alert alert-error"><AlertCircle size={16}/>{error}</div>}
                  {notice && <div className="alert alert-info"><CheckCircle2 size={16}/>{notice}</div>}
                  <div className="submit-bar"><div className="submit-note"><ShieldCheck size={16}/><span>Restricted sites are blocked. Sensitive actions pause for approval.</span></div><button type="submit" className="button primary" disabled={busy}>{busy ? <><LoaderCircle size={16} className="spin"/> Processing</> : <>Review task <ArrowRight size={16}/></>}</button></div>
                </div>
              </form>
            </section>
            <aside className="side-panels">
              <section className="panel runtime-panel"><div className="runtime-panel-head"><div className="panel-icon purple"><Bot size={18}/></div><div><h3>Automation runtime</h3><p>Browser execution service</p></div></div><div className={`runtime-state ${API_BASE ? 'ready' : 'offline'}`}><i/>{API_BASE ? 'API endpoint configured' : 'Not connected'}</div><p>{API_BASE ? `Requests are handed to ${API_BASE}.` : 'This is a frontend-only build. It can validate and save tasks, but it cannot control a real browser until a trusted Playwright or Selenium backend is connected.'}</p>{API_BASE ? <div className="runtime-hint ready"><CheckCircle2 size={15}/><span>Live backend connected at <code>{API_BASE}</code>. Automation engine ready.</span></div> : <div className="runtime-hint"><AlertCircle size={15}/><span>Set <code>VITE_AUTOMATION_API_URL</code> in <code>.env</code> when the backend is ready.</span></div>}</section>
              <section className="panel safety-panel"><div className="side-title"><ShieldCheck size={17}/><div><strong>Safety checks</strong><small>Before any browser action</small></div></div><div className="safety-item"><CheckCircle2 size={16}/><span>Validate the website URL</span></div><div className="safety-item"><CheckCircle2 size={16}/><span>Block restricted destination categories</span></div><div className="safety-item"><CheckCircle2 size={16}/><span>Require approval for sensitive actions</span></div><div className="safety-item"><CheckCircle2 size={16}/><span>Keep credentials out of saved records</span></div><p className="safety-disclaimer">The built-in restricted-domain list is limited and cannot detect every malicious or deceptive site. A production backend should also use a maintained threat-intelligence service.</p></section>
            </aside>
          </div>
          <section className="how-panel"><div className="how-heading"><div><div className="eyebrow"><span/> SIMPLE WORKFLOW</div><h2>From instruction to result</h2></div><span className="how-caption">No college-specific pages. Just the website you choose.</span></div><div className="workflow"><div><span className="workflow-icon"><Globe2 size={18}/></span><strong>01 · Website</strong><small>Choose the destination</small></div><div className="workflow-line"/><div><span className="workflow-icon"><MessageSymbol/></span><strong>02 · Instruction</strong><small>Describe the outcome</small></div><div className="workflow-line"/><div><span className="workflow-icon"><ShieldCheck size={18}/></span><strong>03 · Approval</strong><small>Review sensitive steps</small></div><div className="workflow-line"/><div><span className="workflow-icon"><Zap size={18}/></span><strong>04 · Execution</strong><small>Connected runtime acts</small></div></div></section>
        </>}

        {view === 'records' && <>
          <section className="page-heading compact"><div><div className="eyebrow"><span/> YOUR SAVED TASKS</div><h1>Past records</h1><p>Each record shows only the website link and the instruction. Sign-in details are never displayed here.</p></div><button className="button primary" onClick={() => setView('console')}><Plus size={16}/> New task</button></section>
          <section className="panel list-panel"><div className="list-toolbar"><div><h2>Task records</h2><p>{records.length} saved request{records.length === 1 ? '' : 's'}</p></div><div className="toolbar-actions"><div className="search-box"><Search size={16}/><input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search website or prompt"/></div><button className="button secondary small" onClick={clearRecords} disabled={!records.length}><Trash2 size={14}/> Clear records</button></div></div>
            {filteredRecords.length === 0 ? <div className="empty-state"><div><FileText size={23}/></div><strong>{records.length ? 'No matching records' : 'No past records yet'}</strong><p>Your submitted website tasks will appear here, without any login credentials.</p></div> : <div className="record-table"><div className="record-table-head"><span>WEBSITE LINK</span><span>PROMPT / INSTRUCTION</span><span>CREATED</span><span>STATUS</span><span/></div>{filteredRecords.map(record => <button key={record.id} className={`record-row ${selectedId === record.id ? 'selected' : ''}`} onClick={() => setSelectedId(selectedId === record.id ? null : record.id)}><span className="record-website"><Globe2 size={15}/><strong>{record.url}</strong></span><span className="record-prompt">{record.prompt}</span><span className="record-date">{formatDate(record.createdAt)}</span><span><StatusPill status={record.status}/></span><ChevronRight size={16}/></button>)}</div>}
            {selectedRecord && <div className="record-expanded"><div className="expanded-heading"><div><span>REQUEST {selectedRecord.id}</span><h3>Task details</h3></div><StatusPill status={selectedRecord.status}/></div><div className="expanded-field"><label>WEBSITE LINK</label><a href={selectedRecord.url} target="_blank" rel="noreferrer">{selectedRecord.url} <ArrowUpRight size={13}/></a></div><div className="expanded-field"><label>PROMPT / INSTRUCTION</label><p>{selectedRecord.prompt}</p></div><div className="expanded-meta"><span>Created: {formatDate(selectedRecord.createdAt)}</span><span>Approval: {selectedRecord.approved ? 'Granted' : 'Not granted / not required'}</span></div>{selectedRecord.result && <div className="record-result"><Info size={15}/><span>{selectedRecord.result}</span></div>}{selectedRecord.steps?.length > 0 && <div className="record-steps">{selectedRecord.steps.map((step, index) => <div key={index}><span>{index + 1}</span>{step}</div>)}</div>}<button className="button danger-ghost" onClick={() => removeRecord(selectedRecord.id)}><Trash2 size={14}/> Delete this record</button></div>}
          </section>
        </>}

        {view === 'approvals' && <>
          <section className="page-heading compact"><div><div className="eyebrow"><span/> HUMAN-IN-THE-LOOP CONTROL</div><h1>Approval queue</h1><p>Review consequential tasks before they can be handed to an automation runtime. Approving records consent; it does not bypass backend safety checks.</p></div><span className="trust-chip"><ShieldCheck size={16}/> {approvals.length} awaiting review</span></section>
          <section className="panel list-panel"><div className="list-toolbar"><div><h2>Tasks requiring your decision</h2><p>Website and instruction only — no website credentials are shown.</p></div><div className="search-box"><Search size={16}/><input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search approvals"/></div></div>
            {approvals.filter(item => `${item.url} ${item.prompt}`.toLowerCase().includes(search.toLowerCase())).length === 0
              ? <div className="empty-state"><div><ShieldCheck size={23}/></div><strong>{approvals.length ? 'No matching approvals' : 'Nothing waiting for approval'}</strong><p>Tasks involving payments, purchases, submissions, uploads, bookings or account changes will appear here for your review.</p></div>
              : <div className="approval-queue">{approvals.filter(item => `${item.url} ${item.prompt}`.toLowerCase().includes(search.toLowerCase())).map(item => <article className="approval-card" key={item.id}><div className="approval-card-top"><span className="approval-mark"><ShieldCheck size={18}/></span><div className="approval-card-main"><strong>{hostOf(item.url)}</strong><small>{formatDate(item.createdAt)} · {item.id}</small></div><StatusPill status="Needs approval"/></div><div className="approval-detail"><label>WEBSITE LINK</label><a href={item.url} target="_blank" rel="noreferrer">{item.url} <ArrowUpRight size={13}/></a></div><div className="approval-detail"><label>REQUESTED INSTRUCTION</label><p>{item.prompt}</p></div><div className="approval-reason"><AlertTriangle size={16}/><span>{item.reason}</span></div><div className="approval-actions"><button className="button secondary" onClick={() => decideApproval(item, 'rejected')}><X size={15}/> Reject request</button><button className="button primary" onClick={() => decideApproval(item, 'approved')}><Check size={15}/> Approve request</button></div></article>)}</div>}
          </section>
        </>}
        <footer className="app-footer"><span>© 2026 CampusPilot</span><span><span className="footer-status"/> Frontend prototype</span><span>Credentials never appear in past records or approval requests</span></footer>
      </div>
    </main>
    <ApprovalModal request={approval} onApprove={approve} onCancel={cancelApproval}/>
    <CredentialModal open={credentialModal} onClose={() => setCredentialModal(false)} onSave={setCredentials}/>
  </div>
}

function MessageSymbol() { return <Send size={18}/> }

export default App
