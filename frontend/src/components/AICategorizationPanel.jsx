import { useState } from 'react'
import { AlertCircle, Bot, Check, Sparkles } from 'lucide-react'
import { categorizeTransaction } from '../services/api.js'

const categories = [
  'Sales', 'Refunds', 'Subscriptions', 'Software', 'Supplies',
  'Payroll', 'Payment Fees', 'Services', 'Other',
]

// Clearly fictional examples for demonstrating the categorization workflow.
const sampleTransactions = [
  { id: 'sample-1', description: 'Online store customer order', amount: '84.50', currency: 'USD', transaction_type: 'payment' },
  { id: 'sample-2', description: 'Refund for returned demo order', amount: '-32.00', currency: 'USD', transaction_type: 'refund' },
  { id: 'sample-3', description: 'Monthly customer plan renewal', amount: '29.00', currency: 'USD', transaction_type: 'subscription_payment' },
  { id: 'sample-4', description: 'Cloud hosting service charge', amount: '-45.00', currency: 'USD', transaction_type: 'payment' },
]

export default function AICategorizationPanel({ transactions = [] }) {
  const [results, setResults] = useState({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const liveTransactions = transactions
    .filter((transaction) => transaction.transaction_id && transaction.description)
    .map((transaction) => ({
      id: transaction.transaction_id,
      transaction_id: transaction.transaction_id,
      description: transaction.description,
      amount: transaction.amount,
      currency: transaction.currency,
      transaction_type: transaction.type || 'unknown',
    }))
  const usingSamples = liveTransactions.length === 0
  const displayedTransactions = usingSamples ? sampleTransactions : liveTransactions

  const categorizeAll = async () => {
    setLoading(true)
    setError('')
    const responses = await Promise.allSettled(
      displayedTransactions.map(({ id, transaction_id, description, amount, currency, transaction_type }) =>
        categorizeTransaction({ ...(transaction_id ? { transaction_id } : {}), description, amount, currency, transaction_type }),
      ),
    )
    const nextResults = {}
    let failureCount = 0
    responses.forEach((response, index) => {
      if (response.status === 'fulfilled') {
        nextResults[displayedTransactions[index].id] = { result: response.value, manuallyChanged: false }
      } else {
        failureCount += 1
        nextResults[displayedTransactions[index].id] = { error: response.reason.message }
      }
    })
    setResults(nextResults)
    if (failureCount) {
      const message = Object.values(nextResults).find((entry) => entry.error)?.error
      setError(failureCount === displayedTransactions.length ? message : `${failureCount} transaction${failureCount === 1 ? '' : 's'} could not be categorized. ${message}`)
    }
    setLoading(false)
  }

  const changeCategory = (id, category) => {
    setResults((current) => ({
      ...current,
      [id]: { ...current[id], result: { ...current[id].result, category }, manuallyChanged: true },
    }))
  }

  return (
    <section className="panel ai-categorization-panel">
      <div className="panel-heading ai-panel-heading">
        <div className="ai-panel-title"><span className="ai-panel-icon"><Bot size={17}/></span><div><h2>AI Categorization</h2><p>Classify transaction details with Groq</p></div></div>
        <button className="filter-button categorize-button" type="button" onClick={categorizeAll} disabled={loading}>
          {loading ? <><span className="button-spinner"/> Categorizing…</> : <><Sparkles size={14}/> Categorize transactions</>}
        </button>
      </div>
      <div className="sample-data-banner"><span className="sample-data-dot"/><span>{usingSamples ? <><b>Sample dataset</b> · These illustrative examples are not PayPal transactions.</> : <><b>PayPal Sandbox</b> · Categorizing transactions with available descriptions.</>}</span></div>
      {error && <div className="ai-error-message" role="alert"><AlertCircle size={15}/>{error}</div>}
      <div className="ai-samples-list">
        {displayedTransactions.map((transaction) => {
          const entry = results[transaction.id]
          const result = entry?.result
          return (
            <article className="ai-sample-row" key={transaction.id}>
              <div className="ai-transaction-main">
                <span className="ai-transaction-icon"><Sparkles size={15}/></span>
                <div className="ai-transaction-copy"><b>{transaction.description}</b><small>{transaction.transaction_type.replaceAll('_', ' ')} · {transaction.currency} {transaction.amount}</small></div>
              </div>
              <div className="ai-category-control">
                {result ? <>
                  <label className="sr-only" htmlFor={`category-${transaction.id}`}>Category for {transaction.description}</label>
                  <select id={`category-${transaction.id}`} value={result.category} onChange={(event) => changeCategory(transaction.id, event.target.value)}>
                    {categories.map((category) => <option key={category}>{category}</option>)}
                  </select>
                  {entry.manuallyChanged && <span className="manual-tag"><Check size={11}/> Manual</span>}
                </> : <span className={`uncategorized-tag ${entry?.error ? 'failed-tag' : ''}`}>{entry?.error ? 'Failed' : 'Not categorized'}</span>}
              </div>
              <div className="ai-confidence">
                {result ? <><div><span>Confidence</span><b>{result.confidence}%</b></div><span className="confidence-track"><i style={{ width: `${result.confidence}%` }}/></span></> : <span className="confidence-placeholder">—</span>}
              </div>
              <div className="ai-direction"><span className={`direction-tag ${result?.direction || 'unknown'}`}>{result?.direction || 'unknown'}</span></div>
              <p className="ai-explanation">{result?.explanation || entry?.error || 'Run categorization to see the model’s explanation.'}</p>
            </article>
          )
        })}
      </div>
      <div className="ai-panel-footnote"><Sparkles size={13}/> AI suggestions can be reviewed and manually changed.</div>
    </section>
  )
}
