const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

export async function getHealth({ signal } = {}) {
  let response
  try {
    response = await fetch(`${API_BASE_URL}/health`, { signal })
  } catch (error) {
    if (error.name === 'AbortError') throw error
    throw new Error('The backend could not be reached.')
  }

  if (!response.ok) throw new Error(`The backend returned HTTP ${response.status}.`)

  const health = await response.json().catch(() => {
    throw new Error('The backend returned an invalid health response.')
  })

  if (health.status !== 'healthy' || health.service !== 'PayPilot AI API') {
    throw new Error('The backend health response was unexpected.')
  }

  return health
}

export async function getPayPalTransactions(startDate, endDate, { signal } = {}) {
  const params = new URLSearchParams({ start_date: startDate, end_date: endDate })
  let response
  try {
    response = await fetch(`${API_BASE_URL}/api/paypal/transactions?${params}`, { signal, cache: 'no-store' })
  } catch (error) {
    if (error.name === 'AbortError') throw error
    throw new Error('Could not reach the PayPilot API. Check that the backend is running.')
  }

  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = payload.detail
    const error = new Error(typeof detail === 'string' ? detail : detail?.message || 'Could not retrieve PayPal Sandbox transactions.')
    error.code = detail?.code
    error.status = response.status
    throw error
  }
  return payload
}

export async function getAnalyticsSummary(startDate, endDate, { signal } = {}) {
  const params = new URLSearchParams({ start_date: startDate, end_date: endDate })
  let response
  try {
    response = await fetch(`${API_BASE_URL}/api/analytics/summary?${params}`, { signal })
  } catch (error) {
    if (error.name === 'AbortError') throw error
    throw new Error('Could not reach the PayPilot API. Check that the backend is running.')
  }
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = payload.detail
    const error = new Error(typeof detail === 'string' ? detail : detail?.message || 'Could not retrieve financial analytics.')
    error.code = detail?.code
    error.status = response.status
    throw error
  }
  return payload
}

export async function categorizeTransaction(transaction, { signal } = {}) {
  let response
  try {
    response = await fetch(`${API_BASE_URL}/api/ai/categorize`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(transaction),
      signal,
    })
  } catch (error) {
    if (error.name === 'AbortError') throw error
    throw new Error('Could not reach the PayPilot API. Check that the backend is running.')
  }

  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = payload.detail
    const error = new Error(typeof detail === 'string' ? detail : detail?.message || 'AI categorization failed.')
    error.code = detail?.code
    error.status = response.status
    throw error
  }
  return payload
}

async function fraudRequest(url, options = {}) {
  let response
  try {
    response = await fetch(`${API_BASE_URL}${url}`, options)
  } catch (error) {
    if (error.name === 'AbortError') throw error
    throw new Error('Could not reach the PayPilot API. Check that the backend is running.')
  }
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = payload.detail
    const error = new Error(typeof detail === 'string' ? detail : detail?.message || 'Fraud risk analysis failed.')
    error.code = detail?.code
    error.status = response.status
    throw error
  }
  return payload
}

export function getFraudTransactions({ limit = 500, riskLevel, reviewStatus, signal } = {}) {
  const params = new URLSearchParams({ limit: String(limit) })
  if (riskLevel) params.set('risk_level', riskLevel)
  if (reviewStatus) params.set('review_status', reviewStatus)
  return fraudRequest(`/api/fraud/transactions?${params}`, { signal })
}

export function analyzeFraudTransactions({ startDate, endDate, transactionIds } = {}, { signal } = {}) {
  const body = {}
  if (startDate && endDate) {
    body.start_date = startDate
    body.end_date = endDate
  }
  if (transactionIds?.length) body.transaction_ids = transactionIds
  return fraudRequest('/api/fraud/analyze', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
}

export function updateFraudReview(transactionId, reviewStatus, { signal } = {}) {
  return fraudRequest(`/api/fraud/transactions/${encodeURIComponent(transactionId)}/review`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ review_status: reviewStatus }),
    signal,
  })
}

export async function sendAssistantMessage(message, conversationId, { signal } = {}) {
  let response
  try {
    response = await fetch(`${API_BASE_URL}/api/assistant/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, conversation_id: conversationId || undefined }),
      signal,
    })
  } catch (error) {
    if (error.name === 'AbortError') throw error
    throw new Error('Could not reach the PayPilot API. Check that the backend is running.')
  }
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = payload.detail
    const error = new Error(typeof detail === 'string' ? detail : detail?.message || 'The AI Assistant could not answer this question.')
    error.code = detail?.code
    error.status = response.status
    throw error
  }
  return payload
}

export async function clearAssistantConversation(conversationId) {
  if (!conversationId) return
  try {
    await fetch(`${API_BASE_URL}/api/assistant/conversations/${encodeURIComponent(conversationId)}`, { method: 'DELETE' })
  } catch {
    // Local conversation state is cleared even if the server session has expired.
  }
}
