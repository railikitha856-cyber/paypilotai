import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertTriangle, Check, ChevronRight, CircleHelp, RefreshCw, Search, ShieldAlert, ShieldCheck, Sparkles } from 'lucide-react'
import { analyzeFraudTransactions, getFraudTransactions, updateFraudReview } from '../services/api.js'

function localDateString(date) {
  const offset = date.getTimezoneOffset() * 60000
  return new Date(date.getTime() - offset).toISOString().slice(0, 10)
}

function defaultRange() {
  const end = new Date()
  const start = new Date(end)
  start.setDate(start.getDate() - 30)
  return { start: localDateString(start), end: localDateString(end) }
}

function formatDate(value) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

function displayMoney(amount, currency) {
  if (amount == null || !currency) return '—'
  try { return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(Number(amount)) }
  catch { return `${currency} ${amount}` }
}

export default function FraudDetectionPage() {
  const [transactions, setTransactions] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [range, setRange] = useState(defaultRange)
  const [loading, setLoading] = useState(true)
  const [analyzing, setAnalyzing] = useState(false)
  const [updatingId, setUpdatingId] = useState(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [refreshIndex, setRefreshIndex] = useState(0)

  const loadTransactions = useCallback(async (signal) => {
    const response = await getFraudTransactions({ signal })
    const rows = response.transactions || []
    setTransactions(rows)
    setSelectedId((current) => rows.some((row) => row.transaction_id === current) ? current : (rows[0]?.transaction_id || null))
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError('')
    loadTransactions(controller.signal)
      .catch((reason) => { if (reason.name !== 'AbortError') setError(reason.message) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [loadTransactions, refreshIndex])

  const selected = useMemo(() => transactions.find((row) => row.transaction_id === selectedId), [transactions, selectedId])
  const counts = useMemo(() => ({
    analyzed: transactions.length,
    high: transactions.filter((row) => row.risk_level === 'High').length,
    medium: transactions.filter((row) => row.risk_level === 'Medium').length,
    low: transactions.filter((row) => row.risk_level === 'Low').length,
  }), [transactions])

  async function analyze() {
    if (!range.start || !range.end || range.start > range.end) {
      setError('Choose a valid date range before analyzing.')
      return
    }
    setAnalyzing(true)
    setError('')
    setNotice('')
    try {
      const response = await analyzeFraudTransactions({
        startDate: `${range.start}T00:00:00Z`,
        endDate: `${range.end}T23:59:59Z`,
      })
      await loadTransactions()
      setNotice(response.warnings?.join(' ') || `Analyzed ${response.transactions_analyzed} PayPal Sandbox transactions. This is a risk assessment, not a fraud determination.`)
    } catch (reason) {
      setError(reason.message)
    } finally {
      setAnalyzing(false)
    }
  }

  async function changeReviewStatus(transactionId, reviewStatus) {
    setUpdatingId(transactionId)
    setError('')
    try {
      const response = await updateFraudReview(transactionId, reviewStatus)
      const updated = response.transaction
      setTransactions((current) => current.map((row) => row.transaction_id === transactionId ? updated : row))
    } catch (reason) {
      setError(reason.message)
    } finally {
      setUpdatingId(null)
    }
  }

  const cards = [
    { label: 'Transactions Analyzed', value: counts.analyzed, tone: 'purple', Icon: Search },
    { label: 'High Risk', value: counts.high, tone: 'orange', Icon: ShieldAlert },
    { label: 'Medium Risk', value: counts.medium, tone: 'blue', Icon: AlertTriangle },
    { label: 'Low Risk', value: counts.low, tone: 'green', Icon: ShieldCheck },
  ]

  return <section className="fraud-page">
    <div className="welcome-row fraud-welcome">
      <div><div className="eyebrow">PAYPAL SANDBOX · RISK MONITORING</div><h1>Fraud Detection</h1><p className="subhead">Explainable rules help identify activity that may need review.</p></div>
      <span className="sandbox-badge"><ShieldCheck size={14}/> PayPal Sandbox</span>
    </div>

    <div className="fraud-disclaimer"><CircleHelp size={15}/><span>These are risk assessments, not confirmed fraud. No payments are automatically blocked, refunded or cancelled.</span></div>

    <div className="fraud-toolbar">
      <label className="date-filter">Start date<input type="date" value={range.start} max={range.end || undefined} onChange={(event) => setRange((current) => ({ ...current, start: event.target.value }))}/></label>
      <span className="date-range-separator">to</span>
      <label className="date-filter">End date<input type="date" value={range.end} min={range.start || undefined} max={localDateString(new Date())} onChange={(event) => setRange((current) => ({ ...current, end: event.target.value }))}/></label>
      <button className="filter-button fraud-analyze-button" type="button" onClick={analyze} disabled={analyzing || loading}>{analyzing ? <><span className="button-spinner"/> Analyzing…</> : <><Sparkles size={14}/> Analyze transactions</>}</button>
      <button className="refresh-button" type="button" onClick={() => setRefreshIndex((value) => value + 1)} disabled={loading || analyzing}><RefreshCw size={14} className={loading ? 'spin' : ''}/><span>Refresh</span></button>
    </div>

    {error && <div className="fraud-error" role="alert"><AlertTriangle size={15}/><span>{error}</span></div>}
    {notice && <div className="fraud-notice" role="status"><Check size={14}/><span>{notice}</span></div>}

    <section className="stats-grid fraud-stats" aria-label="Fraud risk overview">
      {cards.map(({ label, value, tone, Icon }) => <article className="stat-card" key={label}><div className="stat-top"><span>{label}</span><span className={`stat-icon ${tone}`}><Icon size={17} strokeWidth={1.8}/></span></div><div className="stat-value">{loading ? <span className="analytics-skeleton"/> : value}</div><div className="stat-bottom"><span className="stat-note">Saved Sandbox risk assessments</span></div></article>)}
    </section>

    <div className="fraud-layout">
      <section className="panel fraud-table-panel">
        <div className="panel-heading transactions-heading"><div><h2>Transaction risk assessments</h2><p>Rule scores, reasons and human review status</p></div><span className="result-count">{transactions.length} RECORDS</span></div>
        {loading ? <div className="transactions-state" role="status"><span className="loading-spinner"/>Loading saved risk assessments…</div> : transactions.length === 0 ? <div className="transactions-state"><div className="state-icon"><ShieldAlert size={19}/></div><h3>No risk assessments yet</h3><p>Analyze actual PayPal Sandbox transactions for the selected dates. PayPilot will not add demo transactions.</p></div> : <div className="table-wrap"><table className="fraud-table"><thead><tr><th>TRANSACTION</th><th>AMOUNT</th><th>RISK SCORE</th><th>RISK LEVEL</th><th>REASON</th><th>REVIEW</th><th/></tr></thead><tbody>
          {transactions.map((row) => <tr key={`${row.transaction_id}-${row.analysis_version}`} className={selectedId === row.transaction_id ? 'selected-row' : ''} onClick={() => setSelectedId(row.transaction_id)}>
            <td><button className="fraud-id-button" type="button" onClick={() => setSelectedId(row.transaction_id)}><b>{row.transaction_id}</b><small>{formatDate(row.transaction_date)}</small></button></td>
            <td className="amount-cell">{displayMoney(row.amount, row.currency)}</td>
            <td><div className="risk-score-cell"><b>{row.risk_score}</b><span className="risk-score-track"><i className={`risk-fill ${row.risk_level.toLowerCase()}`} style={{ width: `${row.risk_score}%` }}/></span></div></td>
            <td><span className={`risk-level ${row.risk_level.toLowerCase()}`}>{row.risk_level}</span></td>
            <td className="fraud-reason-cell">{row.reasons?.[0] || 'No configured rule patterns triggered.'}</td>
            <td><span className={`review-status ${row.review_status}`}>{row.review_status === 'unreviewed' ? 'Unreviewed' : row.review_status}</span></td>
            <td><ChevronRight size={15} className="fraud-row-chevron"/></td>
          </tr>)}
        </tbody></table></div>}
      </section>

      {selected && <aside className="panel fraud-detail-panel">
        <div className="fraud-detail-top"><div><div className="eyebrow">RISK ASSESSMENT</div><h2>{selected.transaction_id}</h2></div><span className={`risk-level ${selected.risk_level.toLowerCase()}`}>{selected.risk_level}</span></div>
        <div className="fraud-detail-score"><strong>{selected.risk_score}<small>/100</small></strong><div className="risk-score-track"><i className={`risk-fill ${selected.risk_level.toLowerCase()}`} style={{ width: `${selected.risk_score}%` }}/></div><span>Rule-based score</span></div>
        <div className="fraud-detail-meta"><div><span>Amount</span><b>{displayMoney(selected.amount, selected.currency)}</b></div><div><span>Date</span><b>{formatDate(selected.transaction_date)}</b></div></div>
        <div className="fraud-explanation"><h3><Sparkles size={14}/> Explanation</h3><p>{selected.explanation}</p></div>
        <div className="fraud-reasons"><h3>Rule findings</h3>{selected.reasons?.length ? <ul>{selected.reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul> : <p>No configured risk patterns were triggered. This assessment is not a guarantee that a transaction is safe.</p>}</div>
        <div className="recommended-action"><span>Recommended action</span><b>{selected.recommended_action}</b><small>Recommendation only. You decide what action to take.</small></div>
        <div className="manual-review"><h3>Manual review status</h3><div className="review-actions">
          {[['approved', 'Approve'], ['review', 'Review'], ['escalated', 'Escalate']].map(([status, label]) => <button key={status} type="button" className={`${selected.review_status === status ? 'active' : ''} ${status}`} onClick={() => changeReviewStatus(selected.transaction_id, status)} disabled={updatingId === selected.transaction_id}>{updatingId === selected.transaction_id ? <span className="button-spinner"/> : selected.review_status === status ? <Check size={12}/> : null}{label}</button>)}
        </div></div>
      </aside>}
    </div>
    <p className="sandbox-footnote"><ShieldCheck size={14}/> Risk assessments use actual PayPal Sandbox transaction fields and contain no payer identity signals.</p>
  </section>
}
