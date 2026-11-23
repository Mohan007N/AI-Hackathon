import React, { useEffect, useMemo, useRef, useState } from 'react'
import axios from 'axios'
import {
  Activity, AlertCircle, AlertOctagon, AlertTriangle, ArrowRight, ArrowUpRight,
  Bot, Check, CheckCircle2, ChevronRight, Clock3, Download, ExternalLink, Eye,
  EyeOff, FileText, Globe2, History, Info, LayoutDashboard, Link2, ListChecks,
  LoaderCircle, LockKeyhole, LogOut, Menu, Monitor, Play, Plus, RefreshCw,
  Search, Send, ShieldAlert, ShieldCheck, Sparkles, Square, Terminal, Trash2,
  User, UserPlus, X, Zap
} from 'lucide-react'

const API_BASE = (import.meta.env.VITE_AUTOMATION_API_URL || '').trim().replace(/\/$/, '')
const USERS_KEY = 'campuspilot-demo-users-v2'
const SESSION_KEY = 'campuspilot-demo-session-v2'
const RECORDS_PREFIX = 'campuspilot-records-v2:'
const LOGS_PREFIX = 'campuspilot-activity-v2:'
const APPROVALS_PREFIX = 'campuspilot-approvals-v2:'
const ACTIVE_TASK_KEY = 'campuspilot-active-task-v2'

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
const formatDate = value => {
  try {
    return new Intl.DateTimeFormat('en-IN', {
      day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit'
    }).format(new Date(value))
  } catch { return String(value || '') }
}

const hostOf = value => {
  try { return new URL(value).host } catch { return value || '—' }
}

const cleanUrl = value => {
  try {
    const parsed = new URL(String(value).trim())
    parsed.username = ''
    parsed.password = ''
    return parsed.href
  } catch { return value || '#' }
}

const safeUrlForRecords = value => {
  try {
    const parsed = new URL(String(value).trim())
    parsed.username = ''
    parsed.password = ''
    for (const key of [...parsed.searchParams.keys()]) {
      if (/(token|password|passwd|secret|api.?key|auth|session|credential|access.?key|code)/i.test(key)) {
        parsed.searchParams.delete(key)
      }
    }
    parsed.hash = ''
    return parsed.href
  } catch { return value || '' }
}

const redactSecrets = (value, secrets = []) => {
  return secrets.filter(Boolean).reduce((text, secret) => text.split(String(secret)).join('[redacted]'), String(value ?? ''))
}

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
  try { parsed = new URL(rawUrl.trim()) } catch { return { safe: false, reason: 'Enter a valid website URL starting with http:// or https://.' } }
  if (!['https:', 'http:'].includes(parsed.protocol)) return { safe: false, reason: 'Enter a valid web URL starting with http:// or https://.' }
  if (parsed.username || parsed.password) return { safe: false, reason: 'Do not place login credentials inside the website URL. Use the secure sign-in details option instead.' }
  const privateQueryKey = [...parsed.searchParams.keys()].find(key => /(token|password|passwd|secret|api.?key|auth|session|credential|access.?key|code)/i.test(key))
  if (privateQueryKey) return { safe: false, reason: 'This URL appears to contain a sign-in token or secret in its query string. Remove it and use the one-time sign-in details option instead.' }

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
  const action = /\b(open|visit|navigate|browse|search|find|look up|check|read|extract|summari[sz]e|compare|download|upload|fill|complete|submit|send|post|pay|purchase|book|order|cancel|delete|remove|edit|update|change|register|sign in|log in|sign out|export|print|select|review|verify|retrieve|fetch|collect|save|enroll)\b/i.test(text)
  if (!action || text.trim().length < 5) return { valid: false, sensitive: false, reason: 'I could not identify a clear action. Tell CampusPilot which website action you want, such as search, read, compare, download, or fill a form.' }
  const sensitivePattern = /\b(submit|send|post|pay|payment|purchase|buy|book|order|cancel|delete|remove|edit|update|change|register|transfer|withdraw|confirm|finali[sz]e|place an order|make a payment)\b/i
  const sensitive = sensitivePattern.test(text)
  let category = 'Website navigation and information'
  if (/download|export|print/i.test(text)) category = 'Download or export'
  else if (/submit|upload|send|post|fill|complete/i.test(text)) category = 'Form or content submission'
  else if (/pay|payment|purchase|buy|book|order|transfer/i.test(text)) category = 'Financial or booking action'
  else if (/delete|remove|edit|update|change|cancel|register|enroll/i.test(text)) category = 'Account or data modification'
  return { valid: true, sensitive, category }
}

function StatusPill({ status }) {
  const normalized = String(status || '').toLowerCase()
  let kind = 'info'
  let label = status
  if (['completed', 'done', 'success'].includes(normalized)) {
    kind = 'success'
    label = 'Completed'
  } else if (['needs_approval', 'needs approval', 'waiting for approval'].includes(normalized)) {
    kind = 'warning'
    label = 'Needs approval'
  } else if (['running', 'started', 'in_progress', 'in progress'].includes(normalized)) {
    kind = 'info'
    label = 'Running'
  } else if (['failed', 'invalid request', 'blocked by safety policy', 'blocked'].includes(normalized)) {
    kind = 'danger'
    label = status || 'Failed'
  } else if (['stopped', 'cancelled', 'not executed'].includes(normalized)) {
    kind = 'neutral'
    label = status || 'Stopped'
  }
  return <span className={`status-pill ${kind}`}><i />{label}</span>
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
        <p className="auth-subtitle">{mode === 'login' ? 'Sign in to continue to your live agent workspace and saved records.' : 'Create a local demo account to keep your live agent workspace and records in one place.'}</p>
        <div className="auth-tabs"><button className={mode === 'login' ? 'active' : ''} onClick={() => changeMode('login')} type="button">Sign in</button><button className={mode === 'signup' ? 'active' : ''} onClick={() => changeMode('signup')} type="button">Sign up</button></div>
        <form onSubmit={submit} className="auth-form">
          {mode === 'signup' && <Field label="Full name"><input value={name} onChange={e => setName(e.target.value)} placeholder="Your name" autoComplete="name" required/></Field>}
          <Field label="Email address"><input value={email} onChange={e => setEmail(e.target.value)} type="email" placeholder="you@example.com" autoComplete="email" required/></Field>
          <Field label="Password" hint={mode === 'signup' ? 'At least 8 characters.' : undefined}><div className="auth-password"><input value={password} onChange={e => setPassword(e.target.value)} type={showPassword ? 'text' : 'password'} placeholder="Enter your password" autoComplete={mode === 'signup' ? 'new-password' : 'current-password'} required/><button type="button" onClick={() => setShowPassword(v => !v)} aria-label="Toggle password visibility">{showPassword ? <EyeOff size={17}/> : <Eye size={17}/>}</button></div></Field>
          {mode === 'signup' && <Field label="Confirm password"><input value={confirm} onChange={e => setConfirm(e.target.value)} type="password" placeholder="Re-enter your password" autoComplete="new-password" required/></Field>}
          {error && <div className="alert alert-error"><AlertCircle size={16}/>{error}</div>}
          {message && <div className="alert alert-info"><Activity size={16}/>{message}</div>}
          <button className="button primary auth-submit" type="submit" disabled={busy}>{busy ? <LoaderCircle className="spin" size={17}/> : mode === 'login' ? <LockKeyhole size={16}/> : <UserPlus size={16}/>} {mode === 'login' ? 'Sign in to CampusPilot' : 'Create account'} <ArrowRight size={16}/></button>
        </form>
        <div className="auth-footnote"><ShieldCheck size={15}/><span>Demo authentication is stored in this browser only. Production accounts require a secure backend.</span></div>
      </section>
      <aside className="auth-aside"><div className="aside-orb"><Sparkles size={26}/></div><div className="eyebrow">ONE PROMPT. MANY SITES.</div><h2>Your web tasks,<br/>under one roof.</h2><p>Enter a website, describe the outcome you need, and let your connected browser agent handle the repetitive steps with live visual feedback.</p><div className="auth-feature"><span><Globe2 size={17}/></span><div><strong>Work across websites</strong><small>Runs in visible Playwright Chromium.</small></div></div><div className="auth-feature"><span><ShieldCheck size={17}/></span><div><strong>Safety and approvals</strong><small>Restricted destinations are blocked. Sensitive actions pause for in-session approval.</small></div></div><div className="auth-feature"><span><Monitor size={17}/></span><div><strong>Live visual workspace</strong><small>Watch actions, DOM structure, and browser screenshots in real time.</small></div></div></aside>
    </div>
    <div className="auth-footer">© 2026 CampusPilot <span>·</span> Live Agent Workspace</div>
  </main>
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
      <div className="dialog-icon"><LockKeyhole size={21}/></div><div className="eyebrow">OPTIONAL ONE-TIME SIGN-IN</div><h2 id="credentials-title">Website sign-in details</h2><p className="dialog-copy">These credentials are sent directly to the local backend session to log into the target portal. They are never saved in past records or browser localStorage.</p>
      <form onSubmit={save} className="dialog-form">
        <Field label="Username / email"><input value={username} onChange={e => setUsername(e.target.value)} autoComplete="username" disabled={noLogin} placeholder="Website username or email"/></Field>
        <Field label="Password"><div className="auth-password"><input value={password} onChange={e => setPassword(e.target.value)} type={showPassword ? 'text' : 'password'} autoComplete="current-password" disabled={noLogin} placeholder="Website password"/><button type="button" onClick={() => setShowPassword(v => !v)} aria-label="Toggle password visibility">{showPassword ? <EyeOff size={17}/> : <Eye size={17}/>}</button></div></Field>
        <label className="check-line"><input type="checkbox" checked={noLogin} onChange={e => setNoLogin(e.target.checked)}/><span>This website does not require a sign-in</span></label>
        <div className="alert alert-info"><LockKeyhole size={15}/> Credentials are provided only for this immediate session.</div>
        <div className="dialog-actions"><button type="button" className="button secondary" onClick={onClose}>Cancel</button><button type="submit" className="button primary">Save for this request</button></div>
      </form>
    </section>
  </div>
}

// ═════════════════════════════════════════════════════════════════════════════
// 4-PANE LIVE AGENT WORKSPACE (Issue 1, Issue 6, Issue 7)
// ═════════════════════════════════════════════════════════════════════════════

function AgentWorkspace({ task, onStop, onApprovalDecision, onNewTask, onViewRecords }) {
  const [screenshotTimestamp, setScreenshotTimestamp] = useState(Date.now())
  const [screenshotError, setScreenshotError] = useState(false)
  const [approvalSubmitting, setApprovalSubmitting] = useState(false)
  const [stopSubmitting, setStopSubmitting] = useState(false)
  const eventsBottomRef = useRef(null)

  // Auto-scroll event feed on new events
  useEffect(() => {
    if (eventsBottomRef.current) {
      eventsBottomRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [task?.events?.length])

  // Trigger screenshot reload when step or status updates
  useEffect(() => {
    setScreenshotTimestamp(Date.now())
    setScreenshotError(false)
  }, [task?.step, task?.current_action, task?.status, task?.updated_at])

  const screenshotUrl = `${API_BASE}/api/automation/${task.id}/screenshot?t=${screenshotTimestamp}`
  const isPendingApproval = task.status === 'needs_approval' || Boolean(task.pending_approval)
  const isFinished = ['completed', 'failed', 'stopped', 'not_done'].includes(task.status)

  const handleApprove = async () => {
    setApprovalSubmitting(true)
    try {
      await onApprovalDecision(true)
    } finally {
      setApprovalSubmitting(false)
    }
  }

  const handleReject = async () => {
    setApprovalSubmitting(true)
    try {
      await onApprovalDecision(false)
    } finally {
      setApprovalSubmitting(false)
    }
  }

  const handleStop = async () => {
    setStopSubmitting(true)
    try {
      await onStop()
    } finally {
      setStopSubmitting(false)
    }
  }

  const dom = task.latest_dom || {}
  const interactive = dom.interactive || {}
  const linksCount = interactive.links?.length ?? 0
  const buttonsCount = interactive.buttons?.length ?? 0
  const inputsCount = interactive.inputs?.length ?? 0
  const tablesCount = dom.tables_count ?? 0

  return (
    <div className="workspace-shell">
      {/* ── WORKSPACE TOP BAR ────────────────────────────────────────── */}
      <div className="workspace-header">
        <div className="workspace-title-box">
          <div className="eyebrow"><span /> LIVE AGENT WORKSPACE</div>
          <div className="workspace-headline">
            <h2>{task.prompt || 'Autonomous Browser Task'}</h2>
            <StatusPill status={task.status} />
          </div>
          <div className="workspace-meta">
            <span className="ws-task-id">SESSION: <strong>{task.id}</strong></span>
            <span className="ws-sep">·</span>
            <span className="ws-target-link">
              <Globe2 size={13} />
              <a href={cleanUrl(task.url)} target="_blank" rel="noreferrer">
                {task.url} <ArrowUpRight size={12} />
              </a>
            </span>
            {task.current_title && (
              <>
                <span className="ws-sep">·</span>
                <span className="ws-page-title" title={task.current_title}>
                  Page: <strong>{task.current_title}</strong>
                </span>
              </>
            )}
          </div>
        </div>

        <div className="workspace-actions">
          {!isFinished && (
            <button
              className="button danger-ghost"
              onClick={handleStop}
              disabled={stopSubmitting}
              title="Stop Playwright agent and clean up browser"
            >
              {stopSubmitting ? <LoaderCircle size={15} className="spin" /> : <Square size={14} />}
              Stop Agent
            </button>
          )}
          <button className="button secondary small" onClick={onViewRecords}>
            <FileText size={14} /> Records
          </button>
          <button className="button primary small" onClick={onNewTask}>
            <Plus size={14} /> New Task
          </button>
        </div>
      </div>

      {/* ── 3-COLUMN MAIN GRID ──────────────────────────────────────── */}
      <div className="workspace-grid">
        {/* ── LEFT PANE: Task & Status ──────────────────────────────── */}
        <section className="ws-panel ws-left-pane">
          <div className="ws-card">
            <div className="ws-card-header">
              <Terminal size={16} />
              <h3>Task Instruction</h3>
            </div>
            <div className="ws-instruction-box">
              <p>{task.prompt}</p>
            </div>
            <div className="ws-meta-row">
              <span className="ws-label">TARGET URL</span>
              <a href={cleanUrl(task.url)} target="_blank" rel="noreferrer" className="ws-url-val">
                {task.url} <ExternalLink size={12} />
              </a>
            </div>
            <div className="ws-meta-row">
              <span className="ws-label">STATUS</span>
              <StatusPill status={task.status} />
            </div>
            <div className="ws-meta-row">
              <span className="ws-label">STEP PROGRESS</span>
              <span className="ws-step-indicator">
                Step <strong>{task.step || 1}</strong> of 10
              </span>
            </div>
          </div>

          <div className="ws-card">
            <div className="ws-card-header">
              <ShieldCheck size={16} />
              <h3>Security & Verification</h3>
            </div>
            <div className="ws-sec-item">
              <CheckCircle2 size={15} className="sec-ok" />
              <div>
                <strong>Destination Verified</strong>
                <small>Destination cleared safety policies</small>
              </div>
            </div>
            <div className="ws-sec-item">
              <CheckCircle2 size={15} className="sec-ok" />
              <div>
                <strong>Strict URL / Prompt Separation</strong>
                <small>Credentials & prompt kept out of URL</small>
              </div>
            </div>
            <div className="ws-sec-item">
              <CheckCircle2 size={15} className={task.verified ? 'sec-ok' : 'sec-neutral'} />
              <div>
                <strong>Intent-Specific Verification</strong>
                <small>{task.verified ? 'Verified on real outcome' : 'Awaiting completion checks'}</small>
              </div>
            </div>
          </div>
        </section>

        {/* ── CENTER PANE: Live Playwright Browser ──────────────────── */}
        <section className="ws-panel ws-center-pane">
          <div className="browser-window">
            <div className="browser-chrome-bar">
              <div className="browser-dots">
                <span className="dot red" />
                <span className="dot yellow" />
                <span className="dot green" />
              </div>
              <div className="browser-address-bar">
                <LockKeyhole size={12} className="addr-lock" />
                <span className="browser-current-url" title={task.current_url || task.url}>
                  {task.current_url || task.url}
                </span>
                {!isFinished && <LoaderCircle size={13} className="spin addr-spinner" />}
              </div>
              <button
                className="icon-button browser-reload-btn"
                onClick={() => setScreenshotTimestamp(Date.now())}
                title="Refresh Screenshot"
              >
                <RefreshCw size={14} />
              </button>
            </div>

            <div className="browser-viewport">
              <div className="browser-watermark">
                <Monitor size={12} />
                <span>LIVE PLAYWRIGHT CHROMIUM (READ-ONLY VIEW)</span>
              </div>

              {!screenshotError ? (
                <img
                  src={screenshotUrl}
                  alt="Live Playwright Chromium View"
                  className="browser-screenshot-img"
                  onError={() => setScreenshotError(true)}
                />
              ) : (
                <div className="browser-fallback">
                  <Monitor size={42} />
                  <strong>Playwright Chromium Window Active</strong>
                  <p>Browser session is running on local desktop. Visible Chromium window is active.</p>
                  <button
                    className="button secondary small"
                    onClick={() => { setScreenshotError(false); setScreenshotTimestamp(Date.now()) }}
                  >
                    <RefreshCw size={13} /> Try reloading frame
                  </button>
                </div>
              )}
            </div>

            <div className="browser-footer">
              <span>{task.current_title ? `Title: ${task.current_title}` : 'Page loaded in Playwright'}</span>
              <span>Viewport: 1280 × 800</span>
            </div>
          </div>
        </section>

        {/* ── RIGHT PANE: DOM Observation & AI Reasoning ────────────── */}
        <section className="ws-panel ws-right-pane">
          {/* Human Approval Alert Card (Issue 6) */}
          {isPendingApproval && (
            <div className="ws-approval-alert-card">
              <div className="ws-approval-head">
                <AlertTriangle size={20} className="alert-icon" />
                <div>
                  <strong>Human Approval Required</strong>
                  <small>Agent paused before irreversible action</small>
                </div>
              </div>
              <div className="ws-approval-details">
                <p>
                  <strong>Proposed Action:</strong>{' '}
                  {task.pending_approval?.action || 'Consequential operation on portal'}
                </p>
                <small>
                  {task.pending_approval?.reason ||
                    'This action may submit data, change account records, or perform a transaction.'}
                </small>
              </div>
              <div className="ws-approval-actions">
                <button
                  className="button secondary small"
                  onClick={handleReject}
                  disabled={approvalSubmitting}
                >
                  <X size={14} /> Reject
                </button>
                <button
                  className="button primary small"
                  onClick={handleApprove}
                  disabled={approvalSubmitting}
                >
                  {approvalSubmitting ? <LoaderCircle size={14} className="spin" /> : <Check size={14} />}
                  Approve and Resume
                </button>
              </div>
              <p className="ws-approval-footnote">
                Resumes the <em>same</em> browser session without reloading or losing state.
              </p>
            </div>
          )}

          {/* DOM Observation Summary */}
          <div className="ws-card">
            <div className="ws-card-header">
              <Globe2 size={16} />
              <h3>DOM Observation</h3>
            </div>
            <div className="dom-metrics-grid">
              <div className="dom-metric">
                <span className="dom-num">{linksCount}</span>
                <span className="dom-label">Links</span>
              </div>
              <div className="dom-metric">
                <span className="dom-num">{buttonsCount}</span>
                <span className="dom-label">Buttons</span>
              </div>
              <div className="dom-metric">
                <span className="dom-num">{inputsCount}</span>
                <span className="dom-label">Inputs</span>
              </div>
              <div className="dom-metric">
                <span className="dom-num">{tablesCount}</span>
                <span className="dom-label">Tables</span>
              </div>
            </div>
          </div>

          {/* AI Reasoning & Action Proposal */}
          <div className="ws-card">
            <div className="ws-card-header">
              <Bot size={16} />
              <h3>AI Decision & Action</h3>
            </div>
            <div className="ai-decision-box">
              <div className="ai-action-tag">
                <Zap size={14} />
                <span>{task.current_action || 'Observing DOM...'}</span>
              </div>
              {task.last_decision && (
                <div className="ai-reason-text">
                  <span className="ai-reason-label">REASONING:</span>
                  <p>{task.last_decision.reasoning || 'Selecting optimal element based on user goal.'}</p>
                  {task.last_decision.target_element && (
                    <div className="ai-target-tag">
                      TARGET: <code>{task.last_decision.target_element}</code>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </section>
      </div>

      {/* ── BOTTOM PANE: Action Events & Artifacts (Issue 7) ───────── */}
      <div className="workspace-bottom-pane">
        <div className="ws-events-section">
          <div className="ws-card-header">
            <Activity size={16} />
            <h3>Execution Action Events & Playwright Log</h3>
            <span className="events-count-badge">{(task.events || []).length} events</span>
          </div>
          <div className="ws-events-stream">
            {(task.events || []).length === 0 ? (
              <div className="ws-events-empty">
                <LoaderCircle size={18} className="spin" />
                <span>Waiting for agent action events...</span>
              </div>
            ) : (
              (task.events || []).map((ev, idx) => (
                <div key={idx} className={`ws-event-row ${ev.type || 'info'}`}>
                  <span className="ws-event-time">
                    {ev.timestamp ? new Date(ev.timestamp).toLocaleTimeString() : '—'}
                  </span>
                  <span className={`ws-event-tag ${ev.type || 'info'}`}>
                    {ev.type?.toUpperCase() || 'INFO'}
                  </span>
                  <div className="ws-event-content">
                    <strong>{ev.title || ev.message || 'Action executed'}</strong>
                    {ev.details && typeof ev.details === 'object' && (
                      <div className="ws-event-meta">
                        {ev.details.url && <span>URL: {ev.details.url}</span>}
                        {ev.details.summary && <span>{ev.details.summary}</span>}
                        {ev.details.action_type && (
                          <span>
                            Action: <strong>{ev.details.action_type}</strong> (
                            {ev.details.target_element || ''})
                          </span>
                        )}
                        {ev.details.reason && <span>Reason: {ev.details.reason}</span>}
                      </div>
                    )}
                  </div>
                </div>
              ))
            )}
            <div ref={eventsBottomRef} />
          </div>
        </div>

        <div className="ws-artifacts-section">
          <div className="ws-card-header">
            <Download size={16} />
            <h3>Verification & Artifacts</h3>
          </div>
          <div className="ws-artifacts-body">
            {/* Outcome Verification Card (Issue 5) */}
            <div className={`ws-verify-card ${task.verified ? 'verified' : task.result_status === 'not_done' ? 'failed' : 'pending'}`}>
              <div className="ws-verify-head">
                {task.verified ? (
                  <CheckCircle2 size={18} className="sec-ok" />
                ) : task.result_status === 'not_done' ? (
                  <AlertOctagon size={18} className="sec-danger" />
                ) : (
                  <Clock3 size={18} className="sec-warn" />
                )}
                <strong>
                  {task.verified
                    ? 'Intent Verified Complete'
                    : task.result_status === 'not_done'
                    ? 'Verification Not Satisfied'
                    : 'Verification In Progress'}
                </strong>
              </div>
              <p className="ws-verify-details">
                {task.verification_details || task.result_summary || task.result || 'Agent is executing verification rules.'}
              </p>
            </div>

            {/* Downloaded Files List */}
            <div className="ws-downloads-box">
              <span className="ws-section-subtitle">DOWNLOADED FILES & EVIDENCE</span>
              {(task.downloaded_files || []).length === 0 ? (
                <div className="ws-no-files">
                  <small>No downloaded files in this session yet.</small>
                </div>
              ) : (
                <div className="ws-files-list">
                  {task.downloaded_files.map((file, fIdx) => {
                    const fileName = typeof file === 'string' ? file.split(/[\\/]/).pop() : file.name || file.path || `artifact_${fIdx + 1}`
                    const downloadUrl = `${API_BASE}/api/automation/${task.id}/download/${encodeURIComponent(fileName)}`
                    return (
                      <div key={fIdx} className="ws-file-item">
                        <FileText size={16} className="file-icon" />
                        <div className="file-info">
                          <strong>{fileName}</strong>
                          {file.size_bytes && <small>{Math.round(file.size_bytes / 1024)} KB</small>}
                        </div>
                        <a
                          href={downloadUrl}
                          className="button subtle small ws-dl-btn"
                          download={fileName}
                          target="_blank"
                          rel="noreferrer"
                        >
                          <Download size={13} /> Download
                        </a>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

// ═════════════════════════════════════════════════════════════════════════════
// MAIN APPLICATION COMPONENT
// ═════════════════════════════════════════════════════════════════════════════

function App() {
  const [user, setUser] = useState(() => {
    try { return JSON.parse(sessionStorage.getItem(SESSION_KEY) || 'null') } catch { return null }
  })
  const [view, setView] = useState('console') // 'console' | 'workspace' | 'records' | 'approvals'
  const [url, setUrl] = useState('')
  const [prompt, setPrompt] = useState('')
  const [credentials, setCredentials] = useState({ username: '', password: '', noLogin: false })
  const [credentialModal, setCredentialModal] = useState(false)
  const [records, setRecords] = useState([])
  const [logs, setLogs] = useState([])
  const [approvals, setApprovals] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [search, setSearch] = useState('')
  const [mobileMenu, setMobileMenu] = useState(false)

  // Active Live Agent Workspace Task State
  const [activeTask, setActiveTask] = useState(() => {
    try { return JSON.parse(sessionStorage.getItem(ACTIVE_TASK_KEY) || 'null') } catch { return null }
  })
  const [backendHealthy, setBackendHealthy] = useState(false)

  // Real HTTP probe for backend health (Section G: checked rather than inferred)
  useEffect(() => {
    if (!API_BASE) {
      setBackendHealthy(false)
      return
    }
    let isMounted = true
    const checkHealth = async () => {
      try {
        const resp = await axios.get(`${API_BASE}/health`, { timeout: 3500 })
        if (isMounted) setBackendHealthy(resp.status === 200)
      } catch {
        if (isMounted) setBackendHealthy(false)
      }
    }
    checkHealth()
    const interval = setInterval(checkHealth, 8000)
    return () => {
      isMounted = false
      clearInterval(interval)
    }
  }, [])

  // Sync session & local storage
  useEffect(() => {
    if (user) {
      sessionStorage.setItem(SESSION_KEY, JSON.stringify(user))
      setRecords(readArray(recordsKey(user.email)))
      setLogs(readArray(logsKey(user.email)))
      setApprovals(readArray(approvalsKey(user.email)))
    } else {
      sessionStorage.removeItem(SESSION_KEY)
      setRecords([])
      setLogs([])
      setApprovals([])
      setSelectedId(null)
    }
  }, [user?.email])

  // Sync active task to session storage
  useEffect(() => {
    if (activeTask) {
      sessionStorage.setItem(ACTIVE_TASK_KEY, JSON.stringify(activeTask))
    } else {
      sessionStorage.removeItem(ACTIVE_TASK_KEY)
    }
  }, [activeTask])

  // ── LIVE POLLING & SSE STREAM FOR ACTIVE TASK ──────────────────────────────
  useEffect(() => {
    if (!activeTask?.id || !API_BASE) return

    const taskId = activeTask.id
    const isFinished = ['completed', 'failed', 'stopped', 'not_done'].includes(activeTask.status)

    // Fallback & continuous polling for robust status sync
    let timer = null
    const pollStatus = async () => {
      try {
        const resp = await axios.get(`${API_BASE}/api/automation/${taskId}/status`)
        const data = resp.data
        if (data) {
          setActiveTask(prev => {
            if (!prev || prev.id !== taskId) return prev
            return {
              ...prev,
              status: data.status,
              step: data.step,
              current_url: data.current_url || prev.current_url,
              current_title: data.current_title || prev.current_title,
              current_action: data.current_action || prev.current_action,
              result_status: data.result_status,
              result_summary: data.result_summary,
              latest_dom: data.latest_dom || prev.latest_dom,
              pending_approval: data.pending_approval,
              downloaded_files: data.downloaded_files || prev.downloaded_files,
              events: data.events?.length ? data.events : prev.events,
              verified: data.verified,
              verification_details: data.verification_details,
              last_decision: data.last_decision || prev.last_decision,
              updated_at: data.updated_at || isoNow(),
            }
          })

          // If finished, sync final record to past records
          if (['completed', 'failed', 'stopped', 'not_done'].includes(data.status)) {
            const finalStatus = data.status === 'completed' ? 'Completed' : data.status === 'stopped' ? 'Stopped' : 'Failed'
            persistRecords(current => current.map(item =>
              item.id === taskId
                ? { ...item, status: finalStatus, result: data.result_summary || data.status, verified: data.verified }
                : item
            ))
          }
        }
      } catch (err) {
        // quiet poll errors
      }
    }

    if (!isFinished) {
      timer = setInterval(pollStatus, 1500)
    }

    // Set up SSE EventSource for real-time action events with auto-reconnection (Section G)
    let eventSource = null
    let reconnectTimeout = null
    let isCancelled = false

    const connectSSE = () => {
      if (isCancelled || isFinished) return
      try {
        eventSource = new EventSource(`${API_BASE}/api/automation/${taskId}/events`)
        eventSource.onmessage = e => {
          try {
            const payload = JSON.parse(e.data)
            if (payload) {
              setActiveTask(prev => {
                if (!prev || prev.id !== taskId) return prev
                const existingEvents = prev.events || []
                if (payload.id && existingEvents.some(ev => ev.id === payload.id)) {
                  return prev
                }
                return {
                  ...prev,
                  events: [...existingEvents, payload],
                  updated_at: isoNow(),
                }
              })
            }
          } catch {
            // parse error
          }
        }
        eventSource.onerror = () => {
          try { eventSource?.close() } catch {}
          eventSource = null
          if (!isCancelled && !isFinished) {
            reconnectTimeout = setTimeout(connectSSE, 2000)
          }
        }
      } catch {
        if (!isCancelled && !isFinished) {
          reconnectTimeout = setTimeout(connectSSE, 2500)
        }
      }
    }

    connectSSE()

    return () => {
      isCancelled = true
      if (timer) clearInterval(timer)
      if (reconnectTimeout) clearTimeout(reconnectTimeout)
      if (eventSource) {
        try { eventSource.close() } catch {}
      }
    }
  }, [activeTask?.id, activeTask?.status])

  const addLog = (event, details = {}) => {
    if (!user) return
    const item = {
      id: `LOG-${Date.now()}-${Math.random().toString(16).slice(2, 6)}`,
      at: isoNow(),
      event,
      website: safeUrlForRecords(details.website || ''),
      prompt: redactSecrets(details.prompt || '', details.secrets || [credentials.username, credentials.password]),
      detail: redactSecrets(details.detail || '', details.secrets || [credentials.username, credentials.password]),
      requestId: details.requestId || ''
    }
    setLogs(current => {
      const next = [item, ...current].slice(0, 300)
      saveArray(logsKey(user.email), next)
      return next
    })
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

  const selectedRecord = useMemo(() => records.find(r => r.id === selectedId) || null, [records, selectedId])
  const filteredRecords = useMemo(() => records.filter(r => `${r.url} ${r.prompt} ${r.status}`.toLowerCase().includes(search.toLowerCase())), [records, search])

  const authenticate = nextUser => {
    setUser(nextUser)
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(nextUser))
  }

  const logout = () => {
    addLog('Signed out', { detail: 'The user ended the current CampusPilot session.' })
    setUser(null)
    setActiveTask(null)
    setCredentials({ username: '', password: '', noLogin: false })
    setView('console')
  }

  const validate = () => {
    setError('')
    setNotice('')
    const safety = checkWebsiteSafety(url)
    if (!safety.safe) { setError(safety.reason); return { ok: false, safety } }
    if (prompt.trim().length < 5) { setError('Describe what you want CampusPilot to do in at least 5 characters.'); return { ok: false } }
    return { ok: true, safety }
  }

  // ── START TASK WORKSPACE (Issue 1, Issue 2, Issue 8) ───────────────────────
  const startAgentTask = async (approved = false) => {
    const checked = validate()
    if (!checked.ok) {
      if (checked.safety && !checked.safety.safe) {
        const blocked = {
          id: makeId(),
          url: safeUrlForRecords(checked.safety.normalizedUrl || url.trim()),
          prompt: redactSecrets(prompt.trim(), [credentials.username, credentials.password]),
          status: 'Blocked by safety policy',
          createdAt: isoNow(),
          category: 'Safety policy',
          result: checked.safety.reason,
          approved: false,
          steps: ['Destination checked against restricted categories', 'Automation stopped before browser launch'],
          host: checked.safety.host || hostOf(url)
        }
        persistRecords(c => [blocked, ...c].slice(0, 200))
        setSelectedId(blocked.id)
        addLog('Website blocked by safety rules', { website: url, prompt, detail: checked.safety.reason, requestId: blocked.id })
        setError('')
        setNotice('This destination is blocked. No login details were used and no browser action was started.')
        setCredentials({ username: '', password: '', noLogin: false })
      }
      return
    }

    const classification = classifyInstruction(prompt)
    if (!classification.valid) {
      const invalid = {
        id: makeId(),
        url: safeUrlForRecords(checked.safety.normalizedUrl),
        prompt: redactSecrets(prompt.trim(), [credentials.username, credentials.password]),
        status: 'Invalid request',
        createdAt: isoNow(),
        category: classification.category,
        result: classification.reason,
        approved: false,
        steps: ['Website URL validated', 'Instruction could not be mapped to a clear action'],
        host: checked.safety.host
      }
      persistRecords(c => [invalid, ...c].slice(0, 200))
      setSelectedId(invalid.id)
      addLog('Invalid instruction rejected', { website: url, prompt, detail: classification.reason, requestId: invalid.id })
      setNotice('Request rejected before any browser interaction.')
      setCredentials({ username: '', password: '', noLogin: false })
      return
    }

    // STRICT URL / PROMPT SEPARATION (Issue 8)
    const normalizedTargetUrl = checked.safety.normalizedUrl
    const naturalInstruction = prompt.trim()
    const oneTimeCredentials = {
      username: credentials.noLogin ? '' : credentials.username.trim(),
      password: credentials.noLogin ? '' : credentials.password
    }
    setCredentials({ username: '', password: '', noLogin: false })
    setPrompt('')
    setBusy(true)
    setError('')
    setNotice('')

    if (!API_BASE) {
      setError('Automation runtime is offline. Set VITE_AUTOMATION_API_URL in .env to connect the backend.')
      setBusy(false)
      return
    }

    try {
      // POST /api/automation/start (Issue 2)
      const payload = {
        url: normalizedTargetUrl,
        instruction: naturalInstruction,
        username: oneTimeCredentials.username,
        password: oneTimeCredentials.password,
        userApproved: approved,
      }

      const response = await axios.post(`${API_BASE}/api/automation/start`, payload, {
        headers: { 'Content-Type': 'application/json' },
        timeout: 20000,
      })

      const data = response.data || {}
      const taskId = data.task_id || makeId()

      const initialTask = {
        id: taskId,
        url: normalizedTargetUrl,
        prompt: naturalInstruction,
        status: data.status || 'started',
        step: 1,
        current_url: normalizedTargetUrl,
        current_title: 'Connecting to Chromium browser...',
        current_action: 'Initializing Playwright session...',
        result_status: null,
        result_summary: null,
        latest_dom: { interactive: { links: [], buttons: [], inputs: [] }, tables_count: 0 },
        pending_approval: data.status === 'needs_approval' ? { action: data.reason || 'Consequential action' } : null,
        downloaded_files: [],
        events: [
          {
            timestamp: isoNow(),
            type: 'info',
            title: 'Agent Session Initialized',
            details: { url: normalizedTargetUrl, summary: 'Launching Playwright Chromium engine' }
          }
        ],
        verified: false,
        verification_details: '',
        createdAt: isoNow(),
        updated_at: isoNow(),
      }

      // NAVIGATE IMMEDIATELY TO LIVE AGENT WORKSPACE (Issue 1)
      setActiveTask(initialTask)
      setView('workspace')

      // Record in past records
      const record = {
        id: taskId,
        url: safeUrlForRecords(normalizedTargetUrl),
        prompt: naturalInstruction,
        status: data.status === 'needs_approval' ? 'Needs approval' : 'In progress',
        createdAt: isoNow(),
        category: classification.category,
        result: data.message || 'Execution started in live workspace',
        approved,
        steps: ['Task initialized in live Agent Workspace'],
        host: hostOf(normalizedTargetUrl),
      }
      persistRecords(c => [record, ...c].slice(0, 200))
      addLog('Agent workspace started', { website: normalizedTargetUrl, prompt: naturalInstruction, requestId: taskId })

    } catch (err) {
      const detail = err.response?.data?.detail || err.response?.data?.message || err.message || 'Failed to start task.'
      setError(`Could not start agent session: ${detail}`)
    } finally {
      setBusy(false)
    }
  }

  // ── IN-SESSION HUMAN APPROVAL (Issue 6) ────────────────────────────────────
  const handleApprovalDecision = async (decision) => {
    if (!activeTask?.id || !API_BASE) return
    const taskId = activeTask.id

    try {
      const response = await axios.post(`${API_BASE}/api/automation/${taskId}/approval`, {
        approved: decision,
        reason: decision ? 'Approved via live agent workspace' : 'Rejected via live agent workspace',
      })

      const data = response.data || {}
      setActiveTask(prev => ({
        ...prev,
        status: decision ? 'running' : 'stopped',
        pending_approval: null,
        events: [
          ...(prev?.events || []),
          {
            timestamp: isoNow(),
            type: 'approval',
            title: decision ? 'Human Approval Granted' : 'Human Approval Rejected',
            details: { reason: data.message || (decision ? 'Resuming Playwright session' : 'Session stopped') }
          }
        ]
      }))
      addLog(decision ? 'Action approved in session' : 'Action rejected in session', {
        website: activeTask.url,
        prompt: activeTask.prompt,
        requestId: taskId
      })
    } catch (err) {
      const detail = err.response?.data?.detail || err.message
      setError(`Failed to submit approval: ${detail}`)
    }
  }

  // ── STOP ACTIVE TASK ──────────────────────────────────────────────────────
  const handleStopTask = async () => {
    if (!activeTask?.id || !API_BASE) return
    const taskId = activeTask.id
    try {
      await axios.post(`${API_BASE}/api/automation/${taskId}/stop`)
      setActiveTask(prev => ({
        ...prev,
        status: 'stopped',
        events: [
          ...(prev?.events || []),
          {
            timestamp: isoNow(),
            type: 'warning',
            title: 'Agent Stopped by User',
            details: { summary: 'Playwright browser closed and session cleaned up.' }
          }
        ]
      }))
      persistRecords(current => current.map(item =>
        item.id === taskId ? { ...item, status: 'Stopped', result: 'Stopped by user in workspace' } : item
      ))
      addLog('Agent stopped', { website: activeTask.url, prompt: activeTask.prompt, requestId: taskId })
    } catch (err) {
      // quiet
    }
  }

  const removeRecord = id => {
    persistRecords(current => current.filter(item => item.id !== id))
    if (selectedId === id) setSelectedId(null)
    addLog('Task record deleted', { detail: `Record ${id} was removed from task records.` })
  }
  const clearRecords = () => {
    persistRecords([])
    setSelectedId(null)
    addLog('Task records cleared', { detail: 'All saved task records were cleared.' })
  }

  if (!user) return <AuthScreen onAuthenticated={authenticate} />

  const isTaskRunning = activeTask && ['started', 'running', 'in_progress', 'needs_approval'].includes(activeTask.status)

  return (
    <div className="app-shell">
      {/* ── SIDEBAR NAVIGATION ────────────────────────────────────────── */}
      <aside className={`sidebar ${mobileMenu ? 'open' : ''}`}>
        <div className="brand-row">
          <div className="brand-mark"><Link2 size={18} /></div>
          <div className="brand-copy">
            <strong>CampusPilot</strong>
            <span>Autonomous Web Agent</span>
          </div>
          <button className="icon-button mobile-close" onClick={() => setMobileMenu(false)} aria-label="Close menu">
            <X size={18} />
          </button>
        </div>

        <div className="side-label">WORKSPACE</div>
        <button
          className={`nav-item ${view === 'console' ? 'active' : ''}`}
          onClick={() => { setView('console'); setMobileMenu(false) }}
        >
          <LayoutDashboard size={17} />
          <span>Task console</span>
        </button>

        {activeTask && (
          <button
            className={`nav-item ${view === 'workspace' ? 'active' : ''}`}
            onClick={() => { setView('workspace'); setMobileMenu(false) }}
          >
            <Monitor size={17} />
            <span>Agent workspace</span>
            {isTaskRunning ? <span className="live-dot-pulse" title="Live task running" /> : <b>Active</b>}
          </button>
        )}

        <button
          className={`nav-item ${view === 'records' ? 'active' : ''}`}
          onClick={() => { setView('records'); setMobileMenu(false) }}
        >
          <FileText size={17} />
          <span>Past records</span>
          <b>{records.length}</b>
        </button>

        <button
          className={`nav-item ${view === 'approvals' ? 'active' : ''}`}
          onClick={() => { setView('approvals'); setMobileMenu(false) }}
        >
          <ShieldCheck size={17} />
          <span>Approvals</span>
          <b>{approvals.length}</b>
        </button>

        <div className="sidebar-bottom">
          <div className="runtime-mini">
            <div className="runtime-mini-head">
              <Bot size={16} />
              <span>RUNTIME</span>
              <i className={backendHealthy ? 'online' : ''} />
            </div>
            <strong>{backendHealthy ? 'Playwright Connected' : 'Runtime Offline'}</strong>
            <p>{backendHealthy ? 'Local Chromium execution active' : API_BASE ? 'Probing backend at ' + API_BASE : 'Connect VITE_AUTOMATION_API_URL'}</p>
          </div>
          <div className="account-mini">
            <div className="user-avatar">{user.name.slice(0, 1).toUpperCase()}</div>
            <div className="account-copy">
              <strong>{user.name}</strong>
              <span>{user.email}</span>
            </div>
            <button className="icon-button logout-small" onClick={logout} title="Sign out">
              <LogOut size={16} />
            </button>
          </div>
        </div>
      </aside>

      {mobileMenu && <button className="mobile-scrim" onClick={() => setMobileMenu(false)} aria-label="Close navigation" />}

      {/* ── MAIN AREA ─────────────────────────────────────────────────── */}
      <main className="main-area">
        <header className="topbar">
          <div className="topbar-leading">
            <button className="icon-button mobile-menu" onClick={() => setMobileMenu(true)} aria-label="Open navigation">
              <Menu size={19} />
            </button>
            <div className="crumb">
              CampusPilot <ChevronRight size={14} />{' '}
              <strong>
                {view === 'console'
                  ? 'Task console'
                  : view === 'workspace'
                  ? 'Agent workspace'
                  : view === 'records'
                  ? 'Past records'
                  : 'Approvals'}
              </strong>
            </div>
          </div>
          <div className="topbar-trailing">
            <span className={`runtime-badge ${backendHealthy ? 'connected' : ''}`}>
              <i />
              {backendHealthy ? 'Playwright Chromium Connected' : 'Backend Offline'}
            </span>
            <div className="top-user">
              <div className="user-avatar small">{user.name.slice(0, 1).toUpperCase()}</div>
              <span>{user.name}</span>
              <button className="icon-button" onClick={logout} aria-label="Sign out" title="Sign out">
                <LogOut size={16} />
              </button>
            </div>
          </div>
        </header>

        <div className="page-content">
          {/* ════════════ VIEW 1: TASK CONSOLE ══════════════════════════ */}
          {view === 'console' && (
            <>
              <section className="page-heading">
                <div>
                  <div className="eyebrow"><span /> AUTONOMOUS BROWSER ASSISTANT</div>
                  <h1>What would you like me to do?</h1>
                  <p>Enter any website and natural-language instruction. CampusPilot executes it in visible Playwright Chromium with real-time visual feedback.</p>
                </div>
                <div className="trust-chip"><ShieldCheck size={16} /> Human In The Loop</div>
              </section>

              <div className="console-grid">
                <section className="panel task-panel">
                  <div className="panel-head">
                    <div className="panel-icon"><Globe2 size={19} /></div>
                    <div>
                      <h2>Create a browser task</h2>
                      <p>Website and natural-language instruction</p>
                    </div>
                    <span className="step-tag">NEW REQUEST</span>
                  </div>
                  <form onSubmit={e => { e.preventDefault(); if (!busy) startAgentTask(false) }}>
                    <div className="panel-body">
                      <Field label="Website link" hint="Use the official website address (HTTP and HTTPS supported).">
                        <div className="input-with-icon">
                          <Link2 size={17} />
                          <input
                            type="url"
                            value={url}
                            onChange={event => { setUrl(event.target.value); setError('') }}
                            placeholder="http://172.16.37.58:8000/... or https://..."
                            required
                          />
                        </div>
                      </Field>

                      <div className="prompt-topline">
                        <span className="field-label">What should CampusPilot do?</span>
                        <span className="optional-label">NATURAL LANGUAGE</span>
                      </div>
                      <div className="prompt-box">
                        <textarea
                          value={prompt}
                          onChange={event => setPrompt(event.target.value)}
                          maxLength={1600}
                          rows={5}
                          placeholder="Example: Download the hall ticket for semester exams."
                          required
                        />
                        <div className="prompt-bottom">
                          <span><Sparkles size={14} /> Clear instructions work best</span>
                          <span>{prompt.length}/1600</span>
                        </div>
                      </div>

                      <div className="credential-inline">
                        <div className="credential-inline-icon"><LockKeyhole size={16} /></div>
                        <div>
                          <strong>Website sign-in details</strong>
                          <small>
                            {credentials.noLogin
                              ? 'No sign-in required for this request'
                              : credentials.username || credentials.password
                              ? 'Credentials added for this session · never stored in records'
                              : 'Optional · only needed for sites that require login'}
                          </small>
                        </div>
                        <button type="button" className="button subtle" onClick={() => setCredentialModal(true)}>
                          {credentials.noLogin || credentials.username || credentials.password ? 'Edit details' : 'Add details'}
                        </button>
                      </div>

                      <div className="example-section">
                        <div className="example-title">TRY AN EXAMPLE <span>Select to fill prompt</span></div>
                        <div className="example-list">
                          {examples.map((example, index) => (
                            <button
                              type="button"
                              key={index}
                              onClick={() => { setPrompt(example); setError('') }}
                            >
                              {example}
                              <ArrowUpRight size={14} />
                            </button>
                          ))}
                        </div>
                      </div>

                      {error && <div className="alert alert-error"><AlertCircle size={16} />{error}</div>}
                      {notice && <div className="alert alert-info"><CheckCircle2 size={16} />{notice}</div>}

                      <div className="submit-bar">
                        <div className="submit-note">
                          <ShieldCheck size={16} />
                          <span>Visible Playwright Chromium launches immediately upon confirmation.</span>
                        </div>
                        <button type="submit" className="button primary" disabled={busy}>
                          {busy ? (
                            <><LoaderCircle size={16} className="spin" /> Launching agent...</>
                          ) : (
                            <>Review & Start Agent <ArrowRight size={16} /></>
                          )}
                        </button>
                      </div>
                    </div>
                  </form>
                </section>

                <aside className="side-panels">
                  <section className="panel runtime-panel">
                    <div className="runtime-panel-head">
                      <div className="panel-icon purple"><Bot size={18} /></div>
                      <div>
                        <h3>Automation Runtime</h3>
                        <p>Playwright Browser Service</p>
                      </div>
                    </div>
                    <div className={`runtime-state ${backendHealthy ? 'ready' : 'offline'}`}>
                      <i />
                      {backendHealthy ? 'Playwright Backend Connected' : 'Backend Not Reachable'}
                    </div>
                    <p>
                      {backendHealthy
                        ? `Live backend healthy at ${API_BASE}. Visible desktop Chromium active.`
                        : `Probing backend at ${API_BASE || 'http://localhost:8000'}. Make sure FastAPI is running.`}
                    </p>
                    {activeTask && (
                      <button
                        className="button secondary small"
                        style={{ width: '100%', marginTop: '10px' }}
                        onClick={() => setView('workspace')}
                      >
                        <Monitor size={14} /> Resume Active Workspace
                      </button>
                    )}
                  </section>

                  <section className="panel safety-panel">
                    <div className="side-title">
                      <ShieldCheck size={17} />
                      <div>
                        <strong>Safety Guarantees</strong>
                        <small>Strict separation & validation</small>
                      </div>
                    </div>
                    <div className="safety-item"><CheckCircle2 size={16} /><span>URL and instruction strictly separated</span></div>
                    <div className="safety-item"><CheckCircle2 size={16} /><span>Observe-Reason-Validate-Act-Verify loop</span></div>
                    <div className="safety-item"><CheckCircle2 size={16} /><span>In-session human approval for irreversible actions</span></div>
                    <div className="safety-item"><CheckCircle2 size={16} /><span>Intent-specific outcome verification</span></div>
                  </section>
                </aside>
              </div>
            </>
          )}

          {/* ════════════ VIEW 2: LIVE AGENT WORKSPACE (Issue 1 & 7) ═══════ */}
          {view === 'workspace' && activeTask && (
            <AgentWorkspace
              task={activeTask}
              onStop={handleStopTask}
              onApprovalDecision={handleApprovalDecision}
              onNewTask={() => setView('console')}
              onViewRecords={() => setView('records')}
            />
          )}

          {/* ════════════ VIEW 3: PAST RECORDS ═══════════════════════════ */}
          {view === 'records' && (
            <>
              <section className="page-heading compact">
                <div>
                  <div className="eyebrow"><span /> YOUR TASK HISTORY</div>
                  <h1>Past records</h1>
                  <p>Each record shows only the website link and instruction. Credentials are never saved.</p>
                </div>
                <button className="button primary" onClick={() => setView('console')}>
                  <Plus size={16} /> New task
                </button>
              </section>

              <section className="panel list-panel">
                <div className="list-toolbar">
                  <div>
                    <h2>Task records</h2>
                    <p>{records.length} saved request{records.length === 1 ? '' : 's'}</p>
                  </div>
                  <div className="toolbar-actions">
                    <div className="search-box">
                      <Search size={16} />
                      <input
                        value={search}
                        onChange={e => setSearch(e.target.value)}
                        placeholder="Search website or prompt"
                      />
                    </div>
                    <button
                      className="button secondary small"
                      onClick={clearRecords}
                      disabled={!records.length}
                    >
                      <Trash2 size={14} /> Clear records
                    </button>
                  </div>
                </div>

                {filteredRecords.length === 0 ? (
                  <div className="empty-state">
                    <div><FileText size={23} /></div>
                    <strong>{records.length ? 'No matching records' : 'No past records yet'}</strong>
                    <p>Submitted website tasks will appear here, safely decoupled from login credentials.</p>
                  </div>
                ) : (
                  <div className="record-table">
                    <div className="record-table-head">
                      <span>WEBSITE LINK</span>
                      <span>PROMPT / INSTRUCTION</span>
                      <span>CREATED</span>
                      <span>STATUS</span>
                      <span />
                    </div>
                    {filteredRecords.map(record => (
                      <button
                        key={record.id}
                        className={`record-row ${selectedId === record.id ? 'selected' : ''}`}
                        onClick={() => setSelectedId(selectedId === record.id ? null : record.id)}
                      >
                        <span className="record-website">
                          <Globe2 size={15} />
                          <strong>{record.url}</strong>
                        </span>
                        <span className="record-prompt">{record.prompt}</span>
                        <span className="record-date">{formatDate(record.createdAt)}</span>
                        <span><StatusPill status={record.status} /></span>
                        <ChevronRight size={16} />
                      </button>
                    ))}
                  </div>
                )}

                {selectedRecord && (
                  <div className="record-expanded">
                    <div className="expanded-heading">
                      <div>
                        <span>REQUEST {selectedRecord.id}</span>
                        <h3>Task details</h3>
                      </div>
                      <StatusPill status={selectedRecord.status} />
                    </div>
                    <div className="expanded-field">
                      <label>WEBSITE LINK</label>
                      <a href={cleanUrl(selectedRecord.url)} target="_blank" rel="noreferrer">
                        {selectedRecord.url} <ArrowUpRight size={13} />
                      </a>
                    </div>
                    <div className="expanded-field">
                      <label>PROMPT / INSTRUCTION</label>
                      <p>{selectedRecord.prompt}</p>
                    </div>
                    <div className="expanded-meta">
                      <span>Created: {formatDate(selectedRecord.createdAt)}</span>
                      <span>Verification: {selectedRecord.verified ? 'Verified complete' : 'Standard'}</span>
                    </div>
                    {selectedRecord.result && (
                      <div className="record-result">
                        <Info size={15} />
                        <span>{selectedRecord.result}</span>
                      </div>
                    )}
                    <button className="button danger-ghost" onClick={() => removeRecord(selectedRecord.id)}>
                      <Trash2 size={14} /> Delete this record
                    </button>
                  </div>
                )}
              </section>
            </>
          )}

          {/* ════════════ VIEW 4: APPROVALS ══════════════════════════════ */}
          {view === 'approvals' && (
            <>
              <section className="page-heading compact">
                <div>
                  <div className="eyebrow"><span /> HUMAN-IN-THE-LOOP QUEUE</div>
                  <h1>Approval queue</h1>
                  <p>Pending consequential actions waiting for human decision.</p>
                </div>
              </section>

              <section className="panel list-panel">
                {activeTask?.status === 'needs_approval' ? (
                  <div className="approval-queue" style={{ padding: '20px' }}>
                    <article className="approval-card">
                      <div className="approval-card-top">
                        <span className="approval-mark"><ShieldCheck size={18} /></span>
                        <div className="approval-card-main">
                          <strong>Active Workspace Session</strong>
                          <small>{activeTask.id}</small>
                        </div>
                        <StatusPill status="Needs approval" />
                      </div>
                      <div className="approval-detail">
                        <label>TARGET SITE</label>
                        <a href={cleanUrl(activeTask.url)} target="_blank" rel="noreferrer">
                          {activeTask.url} <ArrowUpRight size={13} />
                        </a>
                      </div>
                      <div className="approval-detail">
                        <label>INSTRUCTION</label>
                        <p>{activeTask.prompt}</p>
                      </div>
                      <div className="approval-reason">
                        <AlertTriangle size={16} />
                        <span>{activeTask.pending_approval?.reason || 'Irreversible operation paused.'}</span>
                      </div>
                      <div className="approval-actions">
                        <button className="button primary" onClick={() => setView('workspace')}>
                          <Monitor size={15} /> Open Live Workspace to Approve
                        </button>
                      </div>
                    </article>
                  </div>
                ) : (
                  <div className="empty-state">
                    <div><ShieldCheck size={23} /></div>
                    <strong>No tasks currently waiting for approval</strong>
                    <p>When an active agent encounters an irreversible step, it will pause and appear here and in the live workspace.</p>
                  </div>
                )}
              </section>
            </>
          )}

          <footer className="app-footer">
            <span>© 2026 CampusPilot</span>
            <span><span className="footer-status" /> Live Playwright Chromium Agent</span>
            <span>Credentials strictly separated from URL & records</span>
          </footer>
        </div>
      </main>

      <CredentialModal
        open={credentialModal}
        onClose={() => setCredentialModal(false)}
        onSave={setCredentials}
      />
    </div>
  )
}

export default App
