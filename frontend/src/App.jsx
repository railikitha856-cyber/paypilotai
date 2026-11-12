import { useState } from 'react'
import Sidebar from './components/Sidebar.jsx'
import Header from './components/Header.jsx'
import FinancialAnalytics from './components/FinancialAnalytics.jsx'
import TransactionsPage from './components/TransactionsPage.jsx'
import FraudDetectionPage from './components/FraudDetectionPage.jsx'
import AIAssistantPage from './components/AIAssistantPage.jsx'
import StatCards from './components/StatCards.jsx'
import RevenueChart from './components/RevenueChart.jsx'
import ActivityChart from './components/ActivityChart.jsx'
import TransactionsTable from './components/TransactionsTable.jsx'
import { Sparkles, ArrowUpRight, ShieldCheck } from 'lucide-react'

export default function App() {
  const [active, setActive] = useState('Overview')
  const [mobileOpen, setMobileOpen] = useState(false)
  const [assistantMessages, setAssistantMessages] = useState([])
  const [assistantConversationId, setAssistantConversationId] = useState(null)
  return <div className="app-shell">
    <Sidebar active={active} onNavigate={(item) => { setActive(item); setMobileOpen(false) }} mobileOpen={mobileOpen} />
    <main className="main-area">
      <Header onMenu={() => setMobileOpen(!mobileOpen)} pageTitle={active} />
      {active === 'Transactions' ? <div className="page-content"><TransactionsPage /></div> : active === 'Fraud Detection' ? <div className="page-content"><FraudDetectionPage /></div> : active === 'AI Assistant' ? <div className="page-content"><AIAssistantPage messages={assistantMessages} setMessages={setAssistantMessages} conversationId={assistantConversationId} setConversationId={setAssistantConversationId}/></div> : active === 'Overview' ? <div className="page-content"><FinancialAnalytics /></div> : <div className="page-content">
        <div className="welcome-row"><div><div className="eyebrow">PAYPILOT AI WORKSPACE</div><h1>{active}</h1><p className="subhead">Here’s what’s happening with your business today.</p></div></div>
        <div className="sample-notice"><span className="notice-dot"/><span><b>Preview mode</b> · All figures and transactions shown are illustrative sample data.</span></div>
        <StatCards />
        <section className="insight-banner"><div className="insight-icon"><Sparkles size={19}/></div><div className="insight-copy"><span className="banner-label">PILOT INSIGHT <span className="new-pill">NEW</span></span><p>Revenue is trending up <b>12.8%</b> compared with the previous period. Your busiest day was Thursday.</p></div><button className="text-link" onClick={() => setActive('AI Insights')}>Explore insights <ArrowUpRight size={15}/></button></section>
        <div className="charts-grid"><RevenueChart/><ActivityChart/></div><TransactionsTable />
        <footer className="footer-note"><ShieldCheck size={15}/> Secure workspace <span>·</span> PayPilot AI preview</footer>
      </div>}
    </main>
  </div>
}
