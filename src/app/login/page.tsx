"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { ShieldCheck, Sparkles } from "lucide-react";
import { useAuth } from "../auth-context";

export default function LoginPage() {
  const { login, user, loading, error } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);

  if (loading) {
    return <main className="login-shell"><div className="login-card"><p className="muted">Restoring session…</p></div></main>;
  }

  if (user) {
    router.replace("/");
    return <main className="login-shell"><div className="login-card"><p className="muted">Signed in as {user.email}. Redirecting…</p></div></main>;
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    const ok = await login(email, password);
    setSubmitting(false);
    if (ok) router.replace("/");
  };

  return (
    <main className="login-shell">
      <form className="login-card" onSubmit={submit}>
        <div className="brand"><div className="brand-mark"><Sparkles size={17} /></div><div><strong>Ares</strong><span>commerce control room</span></div></div>
        <h1>Sign in</h1>
        <p className="muted">Use your operations account to access the console.</p>
        {error && <div className="alert" role="alert">{error}</div>}
        <label htmlFor="email">Email</label>
        <input id="email" type="email" autoComplete="username" required
               value={email} onChange={(event) => setEmail(event.target.value)} />
        <label htmlFor="password">Password</label>
        <input id="password" type="password" autoComplete="current-password" required
               value={password} onChange={(event) => setPassword(event.target.value)} />
        <button className="primary-button" type="submit" disabled={submitting || !email || !password}>
          <ShieldCheck size={15} /> {submitting ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </main>
  );
}
