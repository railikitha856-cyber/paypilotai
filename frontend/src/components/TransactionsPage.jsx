import { useEffect, useState } from 'react'
import { AlertCircle, ArrowDownLeft, ArrowUpRight, RefreshCw, Search, ShieldCheck } from 'lucide-react'
import { getPayPalTransactions } from '../services/api.js'
import AICategorizationPanel from './AICategorizationPanel.jsx'

function localDateString(date) {
  const offset = date.getTimezoneOffset() * 60000
  return new Date(date.getTime() - offset).toISOString().slice(0, 10)
}

function getDefaultRange() {
  const end = new Date()
  const start = new Date(end)
  start.setDate(start.getDate() - 30)
  return { start: localDateString(start), end: localDateString(end) }
}

function toUtcRange(start, end) {
  return {
    start: `${start}T00:00:00Z`,
    // Date inputs are displayed as UTC-midnight instants in the user's locale.
    // Keep the requested end boundary aligned with that displayed instant.
    end: `${end}T00:00:00Z`,
  }
}

function displayStatus(status) {
  return ({ S: 'Completed', P: 'Pending', D: 'Denied', V: 'Reversed' })[status] || status || 'Unknown'
}

function formatDate(value) {
  if (!value) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

export default function TransactionsPage() {
  const [draftRange, setDraftRange] = useState(getDefaultRange)
  const [appliedRange, setAppliedRange] = useState(getDefaultRange)
  const [refreshIndex, setRefreshIndex] = useState(0)
  const [transactions, setTransactions] = useState([])
  const [resultMeta, setResultMeta] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [filterError, setFilterError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    const { start, end } = toUtcRange(appliedRange.start, appliedRange.end)
    setLoading(true)
    setError(null)

    getPayPalTransactions(start, end, { signal: controller.signal })
      .then((result) => {
        setTransactions(result.transactions || [])
        setResultMeta(result)
      })
      .catch((reason) => {
        if (reason.name !== 'AbortError') {
          setTransactions([])
          setResultMeta(null)
          setError(reason)
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })

    return () => controller.abort()
  }, [appliedRange, refreshIndex])

  const applyFilters = (event) => {
    event.preventDefault()
    if (!draftRange.start || !draftRange.end || draftRange.start > draftRange.end) {
      setFilterError('Choose a valid date range with the start date on or before the end date.')
      return
    }
    setFilterError('')
    setAppliedRange({ ...draftRange })
  }

  const permissionError = error?.code === 'transaction_search_permission_missing'

  return (
    <div className="transactions-page">
      <AICategorizationPanel transactions={transactions} />
      <div className="transactions-page-heading">
        <div>
          <div className="eyebrow">PAYMENT ACTIVITY</div>
          <h1>Transactions</h1>
          <p className="subhead">Review payments retrieved directly from your PayPal account.</p>
        </div>
        <span className="sandbox-badge"><ShieldCheck size={14}/> PayPal Sandbox</span>
      </div>

      <form className="transactions-toolbar" onSubmit={applyFilters}>
        <label className="date-filter">Start date<input type="date" value={draftRange.start} max={draftRange.end || undefined} onChange={(event) => setDraftRange({ ...draftRange, start: event.target.value })}/></label>
        <span className="date-range-separator">to</span>
        <label className="date-filter">End date<input type="date" value={draftRange.end} min={draftRange.start || undefined} max={localDateString(new Date())} onChange={(event) => setDraftRange({ ...draftRange, end: event.target.value })}/></label>
        <button className="filter-button" type="submit"><Search size={15}/> Apply filters</button>
        <button className="refresh-button" type="button" onClick={() => setRefreshIndex((value) => value + 1)} disabled={loading} aria-label="Refresh transactions"><RefreshCw size={15} className={loading ? 'spin' : ''}/><span>Refresh</span></button>
      </form>
      {filterError && <div className="filter-error" role="alert">{filterError}</div>}

      <section className="panel transactions-panel live-transactions-panel">
        <div className="panel-heading transactions-heading">
          <div><h2>PayPal Sandbox transactions</h2><p>{resultMeta ? `${transactions.length} shown${resultMeta.total_items > transactions.length ? ` of ${resultMeta.total_items}` : ''} · ${formatDate(appliedRange.start)} – ${formatDate(appliedRange.end)}` : 'Results for the selected date range'}</p></div>
          {resultMeta && <span className="result-count">{resultMeta.environment.toUpperCase()}</span>}
        </div>

        {loading ? <div className="transactions-state" role="status"><span className="loading-spinner"/>Loading real Sandbox transactions…</div> : error ? (
          <div className="transactions-state error-state" role="alert">
            <div className="state-icon error-icon"><AlertCircle size={19}/></div>
            <h3>{permissionError ? 'Transaction Search permission is missing' : 'Transactions could not be loaded'}</h3>
            <p>{permissionError ? 'In PayPal Developer Dashboard, open your Sandbox app, enable “Transaction search” in Features, and save. Then wait for the current access token to expire or restart the backend to request a fresh token.' : error.message}</p>
            <button className="filter-button" type="button" onClick={() => setRefreshIndex((value) => value + 1)}><RefreshCw size={14}/> Try again</button>
          </div>
        ) : transactions.length === 0 ? (
          <div className="transactions-state empty-state">
            <div className="state-icon"><Search size={19}/></div>
            <h3>No Sandbox transactions found</h3>
            <p>PayPal returned no transactions for these dates. Newly executed transactions can take up to three hours to appear in Transaction Search.</p>
          </div>
        ) : (
          <div className="table-wrap"><table className="live-transactions-table"><thead><tr><th>TRANSACTION</th><th>DATE &amp; TIME</th><th>TYPE</th><th>AMOUNT</th><th>STATUS</th></tr></thead><tbody>
            {transactions.map((transaction, index) => <tr key={transaction.transaction_id || `${transaction.transaction_date}-${index}`}>
              <td><div className="customer"><span className={`customer-avatar avatar-${index % 5}`}>{transaction.status === 'V' ? <ArrowDownLeft size={14}/> : <ArrowUpRight size={14}/>}</span><span><b>{transaction.payer?.name || transaction.payer?.email || 'Payer not provided'}</b><small>{transaction.transaction_id || 'Transaction ID not provided'}</small></span></div></td>
              <td className="date-cell">{formatDate(transaction.transaction_date)}</td>
              <td className="type-cell">{transaction.type || '—'}</td>
              <td className="amount-cell">{transaction.currency ? `${transaction.currency} ` : ''}{transaction.amount ?? '—'}</td>
              <td><span className={`status ${displayStatus(transaction.status).toLowerCase()}`}><i/>{displayStatus(transaction.status)}</span></td>
            </tr>)}
          </tbody></table>{resultMeta?.total_items > transactions.length && <div className="pagination-note">Showing the first {transactions.length} results. Narrow the date range to view fewer transactions.</div>}</div>
        )}
      </section>
      <p className="sandbox-footnote"><ShieldCheck size={14}/> Live results from PayPal Sandbox · No sample transactions are shown on this page.</p>
    </div>
  )
}
