"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Activity, AlertTriangle, ArrowUpRight, BarChart3, Check, ChevronRight, CircleDot, Clock3, Globe2, LogOut, Package, RefreshCw, Search, Send, ShieldCheck, Sparkles, X, type LucideIcon } from "lucide-react";
import { useAuth } from "./auth-context";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type Product = { id: number; sku: string; title: string; description: string; category: string; price: number; status: string; inventory_quantity: number; content: { score: number; grade: string; issues: string[] } };
type Analytics = { product_count: number; active_product_count: number; category_count: number; total_inventory_units: number; low_stock_count: number; out_of_stock_count: number; average_content_score: number; weak_content_count: number; agent_runs_total: number; agent_runs_pending_approval: number; sales: { order_count: number; units_sold: number; total_revenue: number; average_order_value: number }; top_sellers: { title: string; units_sold: number }[] };
type Approval = { id: number; agent_run_id: number; tool_name: string; summary: string; status: string; preview?: { changes?: { field: string; current: unknown; proposed: unknown }[]; sku?: string; title?: string } };
type Run = { id: number; status: string; user_request: string; final_response?: string; error?: string; steps: { id: number; step_type: string; message: string; tool_name?: string; status: string }[]; approvals: Approval[] };
type Job = { id: number; objective: string; query: string; status: string; stage: string; stats: { domains_discovered?: number; pages_crawled?: number; products_discovered?: number; opportunities_found?: number }; error?: string };
type Opportunity = { id: number; type: string; title: string; summary: string; recommended_action: string; source_urls: string[]; score: number; confidence: number; competition_level: string; evidence: { stores_observed?: number; products_observed?: number; median_price?: number | null }; status: string };

const money = (value: number) => new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(value);

export default function Home() {
  const { user, loading: authLoading, logout, apiFetch } = useAuth();
  const router = useRouter();
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [products, setProducts] = useState<Product[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
  const [researchQuery, setResearchQuery] = useState("");
  const [researching, setResearching] = useState(false);
  const [query, setQuery] = useState("");
  const [request, setRequest] = useState("");
  const [run, setRun] = useState<Run | null>(null);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const [summary, catalog, pending, jobList, opportunityList] = await Promise.all([apiFetch<Analytics>("/analytics/summary"), apiFetch<{ items: Product[] }>(`/products?limit=12${query ? `&search=${encodeURIComponent(query)}` : ""}`), apiFetch<Approval[]>("/approvals"), apiFetch<Job[]>("/intelligence/jobs"), apiFetch<Opportunity[]>("/intelligence/opportunities")]);
      setAnalytics(summary); setProducts(catalog.items); setApprovals(pending); setJobs(jobList); setOpportunities(opportunityList);
    } catch (err) { setError(err instanceof Error ? err.message : "Could not reach the operations API"); }
    finally { setLoading(false); }
  }, [query, apiFetch]);

  // Server-side authority: without a session the dashboard never renders data.
  useEffect(() => {
    if (!authLoading && !user) router.replace("/login");
  }, [authLoading, user, router]);

  // Fetching external data on mount and when the search filter changes is the effect's purpose.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!jobs.some((job) => job.status === "QUEUED" || job.status === "RUNNING")) return;
    const timer = window.setInterval(() => { void load(); }, 2000);
    return () => window.clearInterval(timer);
  }, [jobs, load]);

  const submit = async (event: FormEvent) => {
    event.preventDefault(); if (!request.trim()) return;
    setSending(true); setError(""); setRun(null);
    try {
      const created = await apiFetch<Run>("/agent/run", { method: "POST", body: JSON.stringify({ message: request, session_id: "dashboard" }) });
      setRun(created); setRequest("");
      const source = new EventSource(`${API}/agent/runs/${created.id}/events`);
      source.addEventListener("step", (event) => { const step = JSON.parse((event as MessageEvent).data); setRun((current) => current ? { ...current, steps: [...current.steps, step] } : current); });
      source.addEventListener("paused", () => { source.close(); void loadRun(created.id); void load(); });
      source.addEventListener("done", (event) => { setRun((current) => current ? { ...current, ...JSON.parse((event as MessageEvent).data) } : current); source.close(); void load(); });
      source.addEventListener("error", () => { source.close(); void loadRun(created.id); });
    } catch (err) { setError(err instanceof Error ? err.message : "Could not start the agent"); }
    finally { setSending(false); }
  };

  const loadRun = async (id: number) => { try { setRun(await apiFetch<Run>(`/agent/runs/${id}`)); } catch (err) { setError(err instanceof Error ? err.message : "Could not load run"); } };
  const decide = async (approval: Approval, approved: boolean) => { try { await apiFetch(`/approvals/${approval.id}/${approved ? "approve" : "reject"}`, { method: "POST", body: JSON.stringify({}) }); await load(); if (run) await loadRun(run.id); } catch (err) { setError(err instanceof Error ? err.message : "Could not resolve approval"); } };
  const startResearch = async (event: FormEvent) => {
    event.preventDefault(); if (!researchQuery.trim()) return;
    setResearching(true); setError("");
    try { await apiFetch<Job>("/intelligence/jobs", { method: "POST", body: JSON.stringify({ objective: `Find opportunities in ${researchQuery}`, query: researchQuery }) }); setResearchQuery(""); await load(); }
    catch (err) { setError(err instanceof Error ? err.message : "Could not start research"); }
    finally { setResearching(false); }
  };

  const healthLabel = useMemo(() => error ? "API attention needed" : loading ? "Connecting" : "Store connected", [error, loading]);

  if (authLoading || !user) {
    return <main className="login-shell"><div className="login-card"><p className="muted">Checking your session…</p></div></main>;
  }

  return <main className="console-shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark"><Sparkles size={17} /></div><div><strong>Ops Agent</strong><span>commerce control room</span></div></div>
      <nav><a className="active"><Activity size={17} /> Overview</a><a href="#intelligence"><Globe2 size={17} /> Intelligence <b>{opportunities.length}</b></a><a href="#catalog"><Package size={17} /> Catalog</a><a href="#approvals"><ShieldCheck size={17} /> Approvals <b>{approvals.length}</b></a></nav>
      <div className="sidebar-foot"><span className={`status-dot ${error ? "bad" : ""}`} /> {healthLabel}<small>{user.email} · {user.role}</small><button className="logout-button" onClick={() => { logout(); router.replace("/login"); }} aria-label="Sign out"><LogOut size={13} /> Sign out</button></div>
    </aside>
    <section className="workspace">
      <header className="topbar"><div><p className="eyebrow">Tuesday, August 21, 2026</p><h1>Good morning, manager.</h1></div><button className="icon-button" onClick={() => { setLoading(true); void load(); }} aria-label="Refresh data" title="Refresh data"><RefreshCw size={17} /></button></header>
      {error && <div className="alert"><AlertTriangle size={18} /><span>{error}</span><button onClick={() => setError("")} aria-label="Dismiss error"><X size={16} /></button></div>}
      <section className="hero-panel"><div><span className="kicker"><CircleDot size={12} /> LIVE STORE SIGNAL</span><h2>Keep the catalog<br /><em>moving forward.</em></h2><p>Ask the operations agent to investigate performance, find weak copy, or prepare a catalog change for your approval.</p></div><div className="hero-orbit"><div className="orbit-line" /><Sparkles size={31} /><span>AI</span></div></section>
      <section className="metric-grid">{([["Catalog", analytics?.product_count ?? "-", `${analytics?.active_product_count ?? 0} active`, Package], ["Inventory", analytics?.total_inventory_units ?? "-", `${analytics?.low_stock_count ?? 0} low stock`, ArrowUpRight], ["Revenue · 30d", analytics ? money(analytics.sales.total_revenue) : "-", `${analytics?.sales.order_count ?? 0} orders`, Activity], ["Content health", analytics ? `${analytics.average_content_score}/100` : "-", `${analytics?.weak_content_count ?? 0} need attention`, Sparkles]] as [string, string | number, string, LucideIcon][]).map(([label, value, note, Icon]) => <article className="metric" key={String(label)}><div className="metric-label"><span>{label}</span><Icon size={16} /></div><strong>{value}</strong><small>{note}</small></article>)}</section>
      <div className="content-grid">
        <section className="panel agent-panel"><div className="panel-heading"><div><span className="section-number">01 / AGENT</span><h3>What should we investigate?</h3></div><span className="live-chip"><span /> ready</span></div><form onSubmit={submit}><textarea value={request} onChange={(event) => setRequest(event.target.value)} placeholder="e.g. Find our weakest product descriptions and suggest improvements" rows={3} /><div className="prompt-footer"><span>Reads store data first. Writes always pause for approval.</span><button className="primary-button" disabled={sending || !request.trim()}>{sending ? "Working..." : "Run agent"}<Send size={15} /></button></div></form>{run && <div className="run-card"><div className="run-header"><span className={`run-status ${run.status.toLowerCase()}`}>{run.status.replaceAll("_", " ")}</span><span>Run #{run.id}</span></div><div className="activity-feed">{run.steps.map((step) => <div className="activity-item" key={step.id}><span className="activity-icon">{step.step_type === "tool_call" ? <ArrowUpRight size={13} /> : <Clock3 size={13} />}</span><div><strong>{step.tool_name ?? step.step_type.replaceAll("_", " ")}</strong><p>{step.message}</p></div></div>)}</div>{run.final_response && <p className="final-response">{run.final_response}</p>}{run.error && <p className="error-text">{run.error}</p>}</div>}</section>
        <section className="panel sellers-panel"><div className="panel-heading"><div><span className="section-number">02 / SIGNAL</span><h3>Top sellers</h3></div><span className="muted">30 days</span></div>{analytics?.top_sellers.map((seller, index) => <div className="seller-row" key={seller.title}><span className="rank">0{index + 1}</span><div><strong>{seller.title}</strong><span>{seller.units_sold} units sold</span></div><ChevronRight size={15} /></div>) ?? <div className="empty">Waiting for store data</div>}</section>
      </div>
      <section className="panel catalog-panel" id="catalog"><div className="panel-heading"><div><span className="section-number">03 / CATALOG</span><h3>Product pulse</h3></div><label className="search-box"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search catalog" /></label></div><div className="table-wrap"><table><thead><tr><th>Product</th><th>Category</th><th>Price</th><th>Stock</th><th>Content</th></tr></thead><tbody>{products.map((product) => <tr key={product.id}><td><strong>{product.title}</strong><span>{product.sku}</span></td><td>{product.category}</td><td>{money(product.price)}</td><td><span className={product.inventory_quantity <= 10 ? "stock low" : "stock"}>{product.inventory_quantity}</span></td><td><span className={`grade ${product.content.grade}`}>{product.content.score}</span></td></tr>)}</tbody></table>{!products.length && !loading && <div className="empty">No products match that search.</div>}</div></section>
      <section className="panel intelligence-panel" id="intelligence"><div className="panel-heading"><div><span className="section-number">04 / INTELLIGENCE</span><h3>Market opportunity feed</h3></div><span className="muted">Evidence-backed · public web only</span></div><form className="research-form" onSubmit={startResearch}><div className="research-input"><Globe2 size={17} /><input value={researchQuery} onChange={(event) => setResearchQuery(event.target.value)} placeholder="Research a market, niche, or product category" /></div><button className="primary-button" disabled={researching || !researchQuery.trim()}>{researching ? "Queueing..." : "Start investigation"}<ArrowUpRight size={15} /></button></form>{jobs.slice(0, 3).map((job) => <div className="job-row" key={job.id}><div className={`job-icon ${job.status.toLowerCase()}`}><BarChart3 size={16} /></div><div className="job-copy"><strong>{job.objective}</strong><span>{job.status.toLowerCase()} · {job.stage}</span><small>{job.stats.pages_crawled ?? 0} pages crawled · {job.stats.products_discovered ?? 0} products · {job.stats.opportunities_found ?? 0} opportunities</small></div><span className="job-status">#{job.id}</span></div>)}{opportunities.slice(0, 6).map((opportunity) => <article className="opportunity-row" key={opportunity.id}><div className="score-ring"><strong>{Math.round(opportunity.score)}</strong><span>score</span></div><div className="opportunity-copy"><div><span className="opportunity-type">{opportunity.type.replaceAll("_", " ")}</span><span className="freshness">{Math.round(opportunity.confidence * 100)}% confidence</span></div><h4>{opportunity.title}</h4><p>{opportunity.summary}</p><small>{opportunity.evidence.stores_observed ?? 0} stores · {opportunity.source_urls.length} sources · {opportunity.competition_level} competition</small></div></article>)}{!opportunities.length && !jobs.length && <div className="intelligence-empty"><Globe2 size={19} /><div><strong>No market signals yet</strong><span>Start an investigation to discover public products, stores, and catalog gaps.</span></div></div>}</section>
      <section className="panel approvals-panel" id="approvals"><div className="panel-heading"><div><span className="section-number">05 / SAFETY</span><h3>Approval queue</h3></div><span className="muted">Human decision required</span></div>{approvals.map((approval) => <div className="approval-row" key={approval.id}><div className="approval-copy"><span className="approval-icon"><ShieldCheck size={16} /></span><div><strong>{approval.summary}</strong><span>{approval.tool_name} · run #{approval.agent_run_id}</span>{approval.preview?.changes?.map((change) => <small key={change.field}>{change.field}: <s>{String(change.current)}</s> <b>{String(change.proposed)}</b></small>)}</div></div><div className="approval-actions"><button className="reject-button" onClick={() => void decide(approval, false)} aria-label={`Reject approval ${approval.id}`}><X size={15} /></button><button className="approve-button" onClick={() => void decide(approval, true)}><Check size={15} /> Approve</button></div></div>)}{!approvals.length && <div className="empty"><Check size={17} /> Nothing waiting. Approved work will appear in the run activity.</div>}</section>
    </section>
  </main>;
}
