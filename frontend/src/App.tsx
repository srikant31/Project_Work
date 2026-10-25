import { useEffect, useState } from 'react';
import { Activity as ActivityIcon, ArrowUpRight, BarChart3, BookOpen, Database, FlaskConical, FolderOpen, LogOut, MessageSquare, Search, ShieldCheck, UserRound, Users } from 'lucide-react';
import { api, ApiError, errorMessage } from './api';
import type { User } from './types';
import Activity from './components/Activity';
import Assistant from './components/Assistant';
import Experiments from './components/Experiments';
import KnowledgeBase from './components/KnowledgeBase';
import Login from './components/Login';
import RetrievalLab from './components/RetrievalLab';
import UserFeedback from './components/UserFeedback';
import Analytics from './components/Analytics';
import Categories from './components/Categories';
import Profile from './components/Profile';
import { ErrorBanner, Spinner } from './components/Shared';

type Page = 'knowledge' | 'assistant' | 'retrieval' | 'experiments' | 'activity' | 'feedback' | 'analytics' | 'categories' | 'profile';
export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState<Page>('knowledge');
  const [error, setError] = useState<string | null>(null);
  const [loggingOut, setLoggingOut] = useState(false);
  useEffect(() => {
    let active = true;
    api.me().then(data => { if (active) { setUser(data.user); setPage(data.user.role === 'admin' ? 'knowledge' : 'assistant'); } }).catch(caught => { if (active && !(caught instanceof ApiError && caught.status === 401)) setError(errorMessage(caught)); }).finally(() => { if (active) setLoading(false); });
    const expired = () => { setUser(null); setError(null); };
    window.addEventListener('helpdesk:unauthorized', expired);
    return () => { active = false; window.removeEventListener('helpdesk:unauthorized', expired); };
  }, []);
  async function logout() { setLoggingOut(true); setError(null); try { await api.logout(); setUser(null); } catch (caught) { setError(errorMessage(caught)); } finally { setLoggingOut(false); } }
  if (loading) return <div className="app-loading"><span className="brand-mark"><BookOpen size={24} /></span><Spinner label="Opening your workspace…" /></div>;
  if (!user) return <Login initialError={error} onLogin={next => { setUser(next); setPage(next.role === 'admin' ? 'knowledge' : 'assistant'); setError(null); }} />;
  const admin = user.role === 'admin';
  const destinations = [
    { page: 'assistant' as Page, label: 'Assistant', icon: MessageSquare, allowed: true },
    { page: 'knowledge' as Page, label: 'Knowledge base', icon: Database, allowed: admin },
    { page: 'retrieval' as Page, label: 'Retrieval lab', icon: Search, allowed: true },
    { page: 'experiments' as Page, label: 'Experiments', icon: FlaskConical, allowed: admin },
    { page: 'feedback' as Page, label: 'User feedback', icon: Users, allowed: admin },
    { page: 'analytics' as Page, label: 'Analytics', icon: BarChart3, allowed: admin },
    { page: 'categories' as Page, label: 'Categories', icon: FolderOpen, allowed: admin },
    { page: 'activity' as Page, label: 'Activity', icon: ActivityIcon, allowed: admin },
    { page: 'profile' as Page, label: 'Your profile', icon: UserRound, allowed: true },
  ];
  return <div className="app-shell"><aside className="sidebar"><div className="brand"><span className="brand-mark"><BookOpen size={21} /></span>helpdesk<span className="brand-period">.</span></div><div className="workspace-label"><span className="workspace-avatar">K</span><div><strong>Knowledge workspace</strong><small>AI Helpdesk Assistant</small></div></div><span className="sidebar-label">WORKSPACE</span><nav aria-label="Workspace navigation">{destinations.filter(item => item.allowed).map(item => <button key={item.page} aria-label={item.label} title={item.label} aria-current={page === item.page ? 'page' : undefined} className={page === item.page ? 'nav-item active' : 'nav-item'} onClick={() => setPage(item.page)}><item.icon size={18} />{item.label}<span className="nav-active-dot" /></button>)}</nav><div className="sidebar-bottom"><div className="workspace-hint"><ShieldCheck size={19} /><strong>Grounded in your sources.</strong><p>Published knowledge.<br />Traceable evidence.</p><span>YOUR PRIVATE WORKSPACE<ArrowUpRight size={13} /></span></div><div className="account"><span className="account-avatar">{user.email[0].toUpperCase()}</span><div><strong title={user.email}>{user.email}</strong><small>{admin ? 'Administrator' : 'Employee'}</small></div><button className="icon-button" title="Sign out" aria-label="Sign out" onClick={logout} disabled={loggingOut}><LogOut size={17} /></button></div></div></aside><div className="main-shell"><header className="topbar"><span>Knowledge workspace</span><div><span className="local-indicator"><span />Local workspace</span><span className="role-label">{admin ? 'Admin' : 'Employee'}</span></div></header><main className="main-content"><ErrorBanner message={error} />{page === 'knowledge' && admin ? <KnowledgeBase /> : page === 'experiments' && admin ? <Experiments /> : page === 'activity' && admin ? <Activity /> : page === 'feedback' && admin ? <UserFeedback /> : page === 'analytics' && admin ? <Analytics /> : page === 'categories' && admin ? <Categories /> : page === 'profile' ? <Profile /> : page === 'assistant' ? <Assistant onOpenRetrieval={() => setPage('retrieval')} /> : <RetrievalLab />}</main></div></div>;
}
