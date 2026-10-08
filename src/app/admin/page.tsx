"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ShieldAlert, ShieldCheck, UserCog } from "lucide-react";
import { useAuth } from "../auth-context";

type AdminUser = { id: number; email: string; role: string; is_active: boolean; organization_id: number };

const ROLES = ["admin", "manager", "analyst", "viewer"] as const;

export default function AdminPage() {
  const { user, loading: authLoading, apiFetch } = useAuth();
  const router = useRouter();
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [search, setSearch] = useState("");
  const [error, setError] = useState("");
  const [forbidden, setForbidden] = useState(false);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setError(""); setForbidden(false); setLoading(true);
    try {
      const query = search ? `?search=${encodeURIComponent(search)}` : "";
      setUsers(await apiFetch<AdminUser[]>(`/admin/users${query}`));
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to load users";
      if (message.includes("admin role") || message.includes("403")) setForbidden(true);
      else setError(message);
    } finally { setLoading(false); }
  }, [apiFetch, search]);

  useEffect(() => {
    if (!authLoading && !user) router.replace("/login");
  }, [authLoading, user, router]);

  // Client-side convenience only: the server enforces the admin role.
  // setState here is conditional UI state derived from identity, not a
  // cascading render — the rule cannot see the guard.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => { if (user && user.role !== "admin") setForbidden(true); }, [user]);
  useEffect(() => { if (user?.role === "admin") void load(); }, [load, user]);
  /* eslint-enable react-hooks/set-state-in-effect */

  const update = async (target: AdminUser, changes: Partial<Pick<AdminUser, "role" | "is_active">>) => {
    setError("");
    try {
      await apiFetch(`/admin/users/${target.id}`, { method: "PATCH", body: JSON.stringify(changes) });
      await load();
    } catch (err) { setError(err instanceof Error ? err.message : "Update failed"); }
  };

  if (authLoading || !user) return <main className="login-shell"><div className="login-card"><p className="muted">Checking your session…</p></div></main>;

  if (forbidden) {
    return <main className="login-shell"><div className="login-card"><ShieldAlert size={22} /><h1>Not authorized</h1><p className="muted">Administrator access is required for this area.</p><button className="primary-button" onClick={() => router.replace("/")}>Back to console</button></div></main>;
  }

  return <main className="console-shell">
    <aside className="sidebar"><div className="brand"><div className="brand-mark"><UserCog size={17} /></div><div><strong>Administration</strong><span>user management</span></div></div>
      <nav><Link href="/">← Console</Link></nav>
      <div className="sidebar-foot"><span className="status-dot" /> Signed in<small>{user.email} · {user.role}</small></div>
    </aside>
    <section className="workspace">
      <header className="topbar"><div><p className="eyebrow">ADMIN / IDENTITY</p><h1>User management</h1></div></header>
      {error && <div className="alert" role="alert">{error}</div>}
      <section className="panel">
        <div className="panel-heading"><div><span className="section-number">USERS</span><h2>Organization members</h2></div>
          <label className="search-box"><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search by email" /></label>
        </div>
        <div className="table-wrap" tabIndex={0} role="region" aria-label="Organization members"><table><thead><tr><th>Email</th><th>Role</th><th>Status</th><th>Actions</th></tr></thead><tbody>
          {users.map((item) => <tr key={item.id}>
            <td><strong>{item.email}</strong><span>org #{item.organization_id}</span></td>
            <td><select value={item.role} aria-label={`Role for ${item.email}`} onChange={(event) => void update(item, { role: event.target.value })}>
              {ROLES.map((role) => <option key={role} value={role}>{role}</option>)}
            </select></td>
            <td>{item.is_active ? <span className="grade excellent">active</span> : <span className="grade poor">inactive</span>}</td>
            <td><button className="reject-button" onClick={() => void update(item, { is_active: !item.is_active })} aria-label={`${item.is_active ? "Deactivate" : "Activate"} ${item.email}`}>
              {item.is_active ? "Deactivate" : "Activate"}
            </button></td>
          </tr>)}
        </tbody></table>{!loading && !users.length && <div className="empty">No users match that search.</div>}</div>
      </section>
      <section className="panel approvals-panel"><div className="panel-heading"><div><span className="section-number">SAFETY</span><h2>Protections</h2></div></div>
        <div className="empty"><ShieldCheck size={17} /> The last active administrator cannot be demoted or deactivated. All changes are audit-logged.</div>
      </section>
    </section>
  </main>;
}
