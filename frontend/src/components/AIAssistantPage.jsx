import { useEffect, useRef, useState } from 'react'
import { Bot, CircleHelp, LoaderCircle, MessageCircle, RotateCcw, Send, ShieldCheck, Sparkles, Trash2, UserRound } from 'lucide-react'
import { clearAssistantConversation, sendAssistantMessage } from '../services/api.js'

const suggestions = [
  'How much revenue did I generate this month?',
  'Show my refund summary.',
  'Which transactions need attention?',
  'What are my biggest expenses?',
  'Summarize my business performance.',
]

export default function AIAssistantPage({ messages, setMessages, conversationId, setConversationId }) {
  const [draft, setDraft] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const [retryMessage, setRetryMessage] = useState('')
  const endOfMessages = useRef(null)
  const inputRef = useRef(null)

  useEffect(() => {
    endOfMessages.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages, pending, error])

  async function sendMessage(rawMessage, { retry = false } = {}) {
    const message = rawMessage.trim()
    if (!message || pending) return
    setError('')
    setRetryMessage('')
    setDraft('')
    if (!retry) setMessages((current) => [...current, { role: 'user', content: message }])
    setPending(true)
    try {
      const result = await sendAssistantMessage(message, conversationId)
      setConversationId(result.conversation_id)
      setMessages((current) => [...current, {
        role: 'assistant', content: result.answer, explanation: result.explanation,
        warning: result.warning,
        dateRange: result.date_range_label,
        environment: 'PayPal Sandbox',
      }])
    } catch (reason) {
      setError(reason.message || 'The assistant could not answer. Try again.')
      setRetryMessage(message)
    } finally {
      setPending(false)
      inputRef.current?.focus()
    }
  }

  async function clearConversation() {
    await clearAssistantConversation(conversationId)
    setMessages([])
    setConversationId(null)
    setError('')
    setRetryMessage('')
    setDraft('')
    inputRef.current?.focus()
  }

  return <section className="assistant-page">
    <header className="assistant-heading">
      <div><div className="eyebrow">PAYPILOT AI · FINANCIAL OPERATIONS</div><h1>AI Assistant</h1><p className="subhead">Ask questions about your PayPal Sandbox activity and business performance.</p></div>
      <span className="sandbox-badge"><ShieldCheck size={14}/> PayPal Sandbox</span>
    </header>
    <div className="assistant-disclaimer"><CircleHelp size={15}/><span>Answers use available PayPal Sandbox records and saved categorizations or risk assessments. Results are informational and are not professional financial advice.</span></div>

    <section className="assistant-chat panel" aria-label="Financial assistant chat">
      <div className="assistant-chat-top"><div className="assistant-presence"><span className="assistant-presence-icon"><Bot size={17}/></span><span><b>PayPilot Assistant</b><small>Sandbox data only · currencies kept separate</small></span></div>
        <button type="button" className="assistant-clear" onClick={() => { void clearConversation() }} disabled={pending || (!messages.length && !conversationId)}><Trash2 size={14}/> Clear conversation</button>
      </div>
      <div className="assistant-thread" aria-live="polite">
        {messages.length === 0 ? <div className="assistant-empty">
          <span className="assistant-empty-icon"><Sparkles size={20}/></span><h2>What would you like to know?</h2><p>I’ll look up actual Sandbox records for the relevant date range and keep each currency separate.</p>
          <div className="assistant-suggestions">{suggestions.map((suggestion) => <button type="button" key={suggestion} onClick={() => { void sendMessage(suggestion) }} disabled={pending}><MessageCircle size={13}/>{suggestion}</button>)}</div>
        </div> : <div className="assistant-messages">{messages.map((item, index) => <article className={`assistant-message ${item.role}`} key={`${item.role}-${index}`}>
          <span className="assistant-message-avatar">{item.role === 'assistant' ? <Bot size={15}/> : <UserRound size={15}/>}</span>
          <div className="assistant-message-body"><div className="assistant-message-label">{item.role === 'assistant' ? 'PAYPILOT AI' : 'YOU'}{item.environment && <span>{item.environment}</span>}</div>
            <p className="assistant-answer-text">{item.content}</p>{item.warning && <p className="assistant-error-note" role="status">{item.warning}</p>}{item.explanation && <p className="assistant-explanation">{item.explanation}</p>}{item.dateRange && <small className="assistant-date-range">Date range · {item.dateRange}</small>}
          </div>
        </article>)}</div>}
        {pending && <div className="assistant-message assistant"><span className="assistant-message-avatar"><Bot size={15}/></span><div className="assistant-message-body"><div className="assistant-message-label">PAYPILOT AI</div><div className="assistant-thinking"><LoaderCircle size={14} className="spin"/> Checking Sandbox data and preparing a response…</div></div></div>}
        {error && <div className="assistant-error" role="alert"><span>{error}</span>{retryMessage && <button type="button" onClick={() => { void sendMessage(retryMessage, { retry: true }) }} disabled={pending}><RotateCcw size={13}/> Retry</button>}</div>}
        <div ref={endOfMessages}/>
      </div>
      <form className="assistant-composer" onSubmit={(event) => { event.preventDefault(); void sendMessage(draft) }}>
        <input ref={inputRef} value={draft} onChange={(event) => setDraft(event.target.value)} maxLength={2000} placeholder="Ask about revenue, refunds, fees, trends…" aria-label="Message the financial assistant" disabled={pending}/>
        <button type="submit" disabled={pending || !draft.trim()} aria-label="Send message"><Send size={16}/><span>Send</span></button>
      </form>
      <p className="assistant-input-note"><ShieldCheck size={12}/> No payer names or transaction IDs are sent to the AI for explanations.</p>
    </section>
  </section>
}
