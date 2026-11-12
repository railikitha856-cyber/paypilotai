import { useEffect, useMemo, useState } from 'react'
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Activity, CalendarDays, CircleDollarSign, CreditCard, RefreshCw, ShieldCheck, Wallet } from 'lucide-react'
import { getAnalyticsSummary } from '../services/api.js'

function localDate(date) {
  const offset = date.getTimezoneOffset() * 60000
  return new Date(date.getTime() - offset).toISOString().slice(0, 10)
}

function rangeFor(days) {
  const end = new Date()
  const start = new Date(end)
  start.setDate(start.getDate() - (days - 1))
  return { start: localDate(start), end: localDate(end) }
}

function utcRange({ start, end }) {
  return { start: `${start}T00:00:00Z`, end: `${end}T23:59:59Z` }
}

function money(value, currency) {
  if (value == null || !currency) return '—'
  return new Intl.NumberFormat(undefined, { style: 'currency', currency, maximumFractionDigits: 2 }).format(Number(value))
}

function ChartEmpty({ children = 'No data is available for this date range.' }) {
  return <div className="analytics-chart-empty"><Activity size={18}/><span>{children}</span></div>
}

export default function FinancialAnalytics() {
  const [preset, setPreset] = useState(30)
  const [draftRange, setDraftRange] = useState(() => rangeFor(30))
  const [appliedRange, setAppliedRange] = useState(() => rangeFor(30))
  const [data, setData] = useState(null)
  const [currency, setCurrency] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [refreshIndex, setRefreshIndex] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    const range = utcRange(appliedRange)
    setLoading(true)
    setError(null)
    getAnalyticsSummary(range.start, range.end, { signal: controller.signal })
      .then((result) => {
        setData(result)
        setCurrency((current) => result.currencies?.some((entry) => entry.currency === current)
          ? current : (result.currencies?.[0]?.currency || ''))
      })
      .catch((reason) => { if (reason.name !== 'AbortError') { setData(null); setError(reason) } })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [appliedRange, refreshIndex])

  const selected = useMemo(() => data?.currencies?.find((entry) => entry.currency === currency), [data, currency])
  const cards = [
    { label: 'Total Revenue', value: money(selected?.total_revenue, currency), Icon: CircleDollarSign, tone: 'purple' },
    { label: 'Total Refunds', value: money(selected?.total_refunds, currency), Icon: Wallet, tone: 'orange' },
    { label: 'Total Transactions', value: data?.total_transaction_count ?? '—', Icon: CreditCard, tone: 'blue' },
    { label: 'Average Transaction Value', value: money(selected?.average_transaction_value, currency), Icon: Activity, tone: 'green' },
  ]

  function choosePreset(days) {
    const range = rangeFor(days)
    setPreset(days)
    setDraftRange(range)
    setAppliedRange(range)
  }

  function applyCustomRange(event) {
    event.preventDefault()
    if (!draftRange.start || !draftRange.end || draftRange.start > draftRange.end) return
    setPreset('custom')
    setAppliedRange({ ...draftRange })
  }

  return <section className="analytics-page">
    <div className="welcome-row analytics-welcome">
      <div><div className="eyebrow">PAYPAL SANDBOX · FINANCIAL OPERATIONS</div><h1>Financial Analytics</h1><p className="subhead">Revenue, refunds and transaction trends from your PayPal Sandbox account.</p></div>
      <button className="refresh-button analytics-refresh" onClick={() => setRefreshIndex((value) => value + 1)} disabled={loading}><RefreshCw size={13} className={loading ? 'spin' : ''}/> Refresh</button>
    </div>

    <div className="analytics-toolbar">
      <div className="range-presets" aria-label="Date range presets">
        {[7, 30, 90].map((days) => <button key={days} type="button" className={preset === days ? 'selected' : ''} onClick={() => choosePreset(days)}>Last {days} days</button>)}
        <button type="button" className={preset === 'custom' ? 'selected' : ''} onClick={() => setPreset('custom')}><CalendarDays size={12}/> Custom</button>
      </div>
      <form className="analytics-custom-range" onSubmit={applyCustomRange}>
        <label>Start<input aria-label="Analytics start date" type="date" value={draftRange.start} max={draftRange.end || undefined} onChange={(event) => setDraftRange({ ...draftRange, start: event.target.value })}/></label>
        <label>End<input aria-label="Analytics end date" type="date" value={draftRange.end} min={draftRange.start || undefined} max={localDate(new Date())} onChange={(event) => setDraftRange({ ...draftRange, end: event.target.value })}/></label>
        <button className="filter-button" type="submit">Apply</button>
      </form>
    </div>

    {error && <div className="analytics-error"><b>Analytics unavailable</b><span>{error.message}</span><small>{error.code === 'transaction_search_permission_missing' ? 'Enable Transaction Search for your PayPal Sandbox app, then restart the backend to request a fresh token.' : 'Check your backend connection and PayPal Sandbox Transaction Search access, then refresh.'}</small></div>}
    {!error && data && <div className="sandbox-data-note"><span className="notice-dot"/><span><b>PayPal Sandbox</b> · Actual transactions only; currencies are kept separate.</span></div>}

    <section className="stats-grid analytics-stats" aria-label="Financial summary">
      {cards.map(({ label, value, Icon, tone }) => <article className="stat-card" key={label}>
        <div className="stat-top"><span>{label}</span><span className={`stat-icon ${tone}`}><Icon size={17} strokeWidth={1.8}/></span></div>
        <div className="stat-value">{loading ? <span className="analytics-skeleton"/> : value}</div>
        <div className="stat-bottom"><span className="stat-note">{label === 'Total Transactions' ? 'Across all returned currencies' : currency ? `Currency: ${currency}` : 'Awaiting transaction data'}</span></div>
      </article>)}
    </section>

    {data?.currencies?.length > 1 && <label className="currency-picker">Display currency<select value={currency} onChange={(event) => setCurrency(event.target.value)}>{data.currencies.map((entry) => <option key={entry.currency} value={entry.currency}>{entry.currency}</option>)}</select></label>}

    {!loading && !error && data?.total_transaction_count === 0 && <div className="analytics-empty"><span className="state-icon"><CircleDollarSign size={19}/></span><h2>No PayPal transactions for this period</h2><p>PayPilot received an empty result from PayPal Sandbox. Try another date range or refresh after transactions become available.</p></div>}

    {error && <div className="analytics-empty analytics-error-empty"><span className="state-icon error-icon"><Activity size={19}/></span><h2>Financial data could not be loaded</h2><p>Once the backend and PayPal Sandbox Transaction Search are available, refresh to load this period.</p></div>}

    {!loading && !error && data?.total_transaction_count > 0 && <>
      <div className="analytics-fee-summary panel"><div className="fee-icon"><Wallet size={17}/></div><div><span>Total PayPal fees</span><b>{money(selected?.total_paypal_fees, currency)}</b></div><small>{currency ? `Reported separately in ${currency}` : 'Select a currency to view fees'}</small></div>
      <div className="analytics-charts-grid">
        <section className="panel analytics-panel"><div className="panel-heading"><div><h2>Daily revenue</h2><p>Successful incoming payments only · {currency}</p></div></div>
          {selected?.daily_revenue?.length ? <div className="analytics-chart"><ResponsiveContainer width="100%" height="100%"><LineChart data={selected.daily_revenue} margin={{ top: 8, right: 12, left: -14, bottom: 0 }}><CartesianGrid stroke="#edf0f6" vertical={false}/><XAxis dataKey="date" axisLine={false} tickLine={false} tick={{ fill: '#97a0b1', fontSize: 9 }} minTickGap={24}/><YAxis axisLine={false} tickLine={false} tick={{ fill: '#97a0b1', fontSize: 9 }} tickFormatter={(v) => Number(v).toLocaleString()}/><Tooltip formatter={(value) => [money(value, currency), 'Revenue']}/><Line type="monotone" dataKey="revenue" stroke="#7167e9" strokeWidth={2.5} dot={false} activeDot={{ r: 4 }}/></LineChart></ResponsiveContainer></div> : <ChartEmpty/>}
        </section>
        <section className="panel analytics-panel"><div className="panel-heading"><div><h2>Monthly revenue</h2><p>Successful incoming payments only · {currency}</p></div></div>
          {selected?.monthly_revenue?.length ? <div className="analytics-chart"><ResponsiveContainer width="100%" height="100%"><BarChart data={selected.monthly_revenue} margin={{ top: 8, right: 12, left: -14, bottom: 0 }}><CartesianGrid stroke="#edf0f6" vertical={false}/><XAxis dataKey="month" axisLine={false} tickLine={false} tick={{ fill: '#97a0b1', fontSize: 9 }}/><YAxis axisLine={false} tickLine={false} tick={{ fill: '#97a0b1', fontSize: 9 }} tickFormatter={(v) => Number(v).toLocaleString()}/><Tooltip formatter={(value) => [money(value, currency), 'Revenue']}/><Bar dataKey="revenue" fill="#7167e9" radius={[5, 5, 0, 0]} maxBarSize={42}/></BarChart></ResponsiveContainer></div> : <ChartEmpty/>}
        </section>
      </div>
      <section className="panel analytics-category-panel"><div className="panel-heading"><div><h2>AI transaction categories</h2><p>Saved AI categorization results for successful transactions · {currency}</p></div></div>
        {selected?.category_breakdown?.length ? <div className="analytics-category-list">{selected.category_breakdown.map((item) => { const largest = Math.max(...selected.category_breakdown.map((entry) => Number(entry.amount)), 0); const width = largest ? Math.max(4, Math.min(100, Number(item.amount) / largest * 100)) : 4; return <div className="analytics-category-row" key={item.category}><span>{item.category}</span><div className="category-track"><i style={{ width: `${width}%` }}/></div><b>{money(item.amount, currency)}</b><small>{item.transaction_count} tx</small></div> })}</div> : <ChartEmpty>No saved AI categories for transactions in this period. Categorize live transactions from the Transactions page.</ChartEmpty>}
      </section>
    </>}

    <footer className="footer-note"><ShieldCheck size={15}/> Secure workspace <span>·</span> PayPilot AI</footer>
  </section>
}
