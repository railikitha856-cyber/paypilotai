import { LayoutDashboard, ArrowLeftRight, Sparkles, ShieldAlert, Bot, Settings, CircleHelp, ChevronDown, Command } from 'lucide-react'
const groups = [{ title: 'WORKSPACE', items: [['Overview', LayoutDashboard], ['Transactions', ArrowLeftRight], ['AI Insights', Sparkles], ['Fraud Detection', ShieldAlert], ['AI Assistant', Bot]] }, { title: 'PREFERENCES', items: [['Settings', Settings]] }]
export default function Sidebar({ active, onNavigate, mobileOpen }) {
  return <aside className={`sidebar ${mobileOpen ? 'mobile-open' : ''}`}><div className="brand"><div className="brand-mark"><span/><span/><span/></div><div className="brand-name">paypilot<span>AI</span></div></div>
    <button className="workspace-switch"><div className="workspace-avatar">N</div><div className="workspace-meta"><strong>Northstar Studio</strong><small>Business account</small></div><ChevronDown size={15}/></button>
    <nav>{groups.map(group => <div className="nav-group" key={group.title}><div className="nav-label">{group.title}</div>{group.items.map(([label, Icon]) => <button key={label} className={`nav-item ${active === label ? 'active' : ''}`} onClick={() => onNavigate(label)}><Icon size={18} strokeWidth={1.8}/><span>{label}</span>{label === 'AI Insights' && <span className="nav-new">2</span>}</button>)}</div>)}</nav>
    <div className="sidebar-bottom"><div className="help-card"><div className="help-icon"><CircleHelp size={16}/></div><div><b>Need a hand?</b><small>Visit our help center</small></div><ArrowRightIcon/></div><div className="profile"><div className="profile-avatar">AR</div><div className="profile-copy"><b>Alex Rivera</b><small>Owner</small></div><button className="more-button">···</button></div><div className="build-tag"><Command size={12}/> Crafted for confident decisions</div></div>
  </aside>
}
function ArrowRightIcon(){return <span className="help-arrow">↗</span>}
