import { useState } from 'react';
import type { FormEvent } from 'react';
import { ArrowRight, BookOpen, ShieldCheck } from 'lucide-react';
import { api, errorMessage } from '../api';
import type { User } from '../types';
import { ErrorBanner, Spinner } from './Shared';

export default function Login({ onLogin, initialError }: { onLogin: (user: User) => void; initialError: string | null }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(initialError);
  async function submit(event: FormEvent) {
    event.preventDefault(); setError(null); setBusy(true);
    try { onLogin((await api.login(email.trim(), password)).user); }
    catch (caught) { setError(errorMessage(caught)); }
    finally { setBusy(false); }
  }
  return <main className="login-page">
    <section className="login-intro"><div className="brand"><span className="brand-mark"><BookOpen size={22} /></span>helpdesk<span className="brand-period">.</span></div>
      <div className="login-story"><span className="eyebrow light">YOUR KNOWLEDGE, CONNECTED</span><h1>Good answers<br />start here.</h1><p>A focused workspace for your documents and the evidence your team needs.</p><div className="login-feature"><ShieldCheck size={20} /><span>Private workspace Â· Role-based access</span></div></div>
      <span className="login-footer">AI HELPDESK ASSISTANT</span>
    </section>
    <section className="login-form-area"><form className="login-form" onSubmit={submit}><span className="eyebrow">KNOWLEDGE WORKSPACE</span><h2>Welcome back.</h2><p className="subtle">Sign in with your workspace account to continue.</p><ErrorBanner message={error} />
      <label>Email address<input autoComplete="username" type="email" value={email} onChange={event => setEmail(event.target.value)} placeholder="admin@helpdesk or employee@helpdesk" required disabled={busy} /></label>
      <label htmlFor="login-password">Password</label>
      <div className="password-field"><input id="login-password" autoComplete="current-password" type={showPassword ? 'text' : 'password'} value={password} onChange={event => setPassword(event.target.value)} placeholder="Enter your password" required disabled={busy} /><button type="button" className="password-toggle" aria-label={showPassword ? 'Hide password' : 'Show password'} aria-controls="login-password" aria-pressed={showPassword} onClick={() => setShowPassword(value => !value)} disabled={busy}>{showPassword ? 'Hide' : 'Show'}</button></div>
      <button className="button primary full" disabled={busy} type="submit">{busy ? <Spinner label="Signing inâ€¦" /> : <>Sign in<ArrowRight size={17} /></>}</button><p className="login-note">Need access? Ask your workspace administrator.</p>
    </form></section>
  </main>;
}
