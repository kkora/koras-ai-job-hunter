"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
import {
  Activity, ArrowRight, ArrowUpRight, Bookmark, BriefcaseBusiness, Check,
  ChevronLeft, ChevronRight, CircleHelp, Database, Fingerprint, Globe2, LayoutDashboard,
  ListFilter, LoaderCircle, LockKeyhole, LogOut, Menu, MoreHorizontal, Play, Search, Settings2,
  ShieldCheck, SlidersHorizontal, Sparkles, X
} from "lucide-react";
import { api, ApiError, SESSION_EXPIRED_EVENT, array, countryLabel, date, object, prettyLabel, rateLabel, setCsrf, str, type Dashboard, type JsonObject, type Opportunity, type OpportunityDetail, type OpportunityPage, type Session } from "@/lib/api";
import { Breadcrumb, EmptyState, ErrorState, LoadingState, Modal, Notice, PageHeading, Panel, StatusBadge, useResource } from "@/components/ui";
import { ProfilesPage, SourcesPage, VendorPage, WorkflowsPage } from "@/components/settings-pages";

const navigation = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/opportunities", label: "Opportunities", icon: BriefcaseBusiness },
  { href: "/review", label: "Review queue", icon: ShieldCheck },
  { href: "/saved", label: "Saved opportunities", icon: Bookmark },
  { href: "/profiles", label: "Search profiles", icon: SlidersHorizontal },
  { href: "/sources", label: "Source registry", icon: Database },
  { href: "/workflows", label: "Workflow monitor", icon: Activity },
  { href: "/settings", label: "Vendor & settings", icon: Settings2 }
];

export type AppContext = { session: Session; notify: (message: string) => void; refresh: number; bump: () => void };

export function WorkspaceApp() {
  const router = useRouter(), pathname = usePathname(), searchParams = useSearchParams();
  const [session, setSession] = useState<Session | null>(null);
  const [booting, setBooting] = useState(true);
  const [connectionError, setConnectionError] = useState<Error | null>(null);
  const [mobile, setMobile] = useState(false);
  const [toast, setToast] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [globalQuery, setGlobalQuery] = useState("");
  const notify = useCallback((message: string) => setToast(message), []);
  const bump = useCallback(() => setRefresh(value => value + 1), []);

  useEffect(() => {
    function expired() {
      if (!session) return;
      setSession(null); setCsrf(""); setToast(""); setMobile(false);
      setConnectionError(new Error("Your session ended. Sign in again to continue."));
      router.replace("/login");
    }
    window.addEventListener(SESSION_EXPIRED_EVENT, expired);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, expired);
  }, [router, session]);

  useEffect(() => {
    let active = true;
    async function boot() {
      try {
        const current = await api<Session>("/auth/me");
        if (active) { setSession(current); setCsrf(current.csrf_token); }
      } catch (error) {
        if (active && !(error instanceof ApiError && error.status === 401)) setConnectionError(error instanceof Error ? error : new Error("Local API unavailable."));
      } finally { if (active) setBooting(false); }
    }
    void boot();
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(""), 4500);
    return () => clearTimeout(timer);
  }, [toast]);

  async function logout() {
    try { await api("/auth/logout", { method: "POST", body: {} }); setSession(null); setCsrf(""); router.push("/login"); }
    catch (error) { notify(error instanceof Error ? error.message : "Sign out failed."); }
  }

  if (booting) return <div className="boot-screen"><Brand /><LoaderCircle className="spin" size={22} /><p>Opening your private workspace</p></div>;
  if (!session) return <LoginPage connectionError={connectionError} onLogin={value => { setSession(value); setConnectionError(null); setCsrf(value.csrf_token); router.push("/"); }} />;

  const active = navigation.find(item => item.href === pathname) ?? (pathname.startsWith("/opportunities/") ? navigation[1] : navigation[0]);
  const context: AppContext = { session, notify, refresh, bump };
  let page: ReactNode;
  if (pathname.startsWith("/opportunities/")) page = <OpportunityPageView id={pathname.split("/")[2]} context={context} />;
  else if (pathname === "/opportunities" || pathname === "/saved" || pathname === "/review") page = <SearchPage key={`${pathname}?${searchParams.toString()}`} context={context} mode={pathname.slice(1)} />;
  else if (pathname === "/sources") page = <SourcesPage context={context} />;
  else if (pathname === "/profiles") page = <ProfilesPage context={context} />;
  else if (pathname === "/workflows") page = <WorkflowsPage context={context} />;
  else if (pathname === "/settings") page = <VendorPage context={context} />;
  else page = <OverviewPage context={context} />;

  return <div className="workspace-shell">
    <a className="skip-link" href="#main-content">Skip to main content</a>
    {mobile && <button aria-label="Close navigation" className="sidebar-overlay" onClick={() => setMobile(false)} />}
    <aside className={`sidebar ${mobile ? "sidebar-open" : ""}`} aria-label="Main navigation">
      <div className="sidebar-brand"><Brand /><button className="icon-button mobile-close" aria-label="Close navigation" onClick={() => setMobile(false)}><X size={20} /></button></div>
      <div className="workspace-picker"><div className="workspace-avatar">K</div><div><strong>Koras Technologies</strong><span>Private workspace</span></div><span className="workspace-picker-mark">⌘</span></div>
      <p className="nav-section-label">INTELLIGENCE</p>
      <nav aria-label="Main navigation">{navigation.map(item => <Link key={item.href} href={item.href} className={`nav-link ${active.href === item.href ? "nav-link-active" : ""}`} aria-current={active.href === item.href ? "page" : undefined} onClick={() => setMobile(false)}><item.icon size={18} strokeWidth={1.75} /><span>{item.label}</span>{active.href === item.href && <span className="nav-active-dot" />}</Link>)}</nav>
      <div className="sidebar-bottom"><div className="environment-card"><span className="environment-icon"><Fingerprint size={21} /></span><div><strong>Local & private</strong><p>Synthetic data only</p></div><span className="live-dot" /></div><div className="sidebar-user"><div className="user-avatar">{session.user.role.slice(0, 1).toUpperCase()}</div><div><strong>{prettyLabel(session.user.role)}</strong><span>{session.user.email}</span></div><button className="icon-button" aria-label="Sign out" title="Sign out" onClick={logout}><LogOut size={17} /></button></div></div>
    </aside>

    <div className="workspace-main"><header className="topbar"><div className="topbar-left"><button className="icon-button menu-button" aria-label="Open navigation" aria-expanded={mobile} onClick={() => setMobile(true)}><Menu size={21} /></button><Breadcrumb>{active.label}</Breadcrumb></div><div className="topbar-right"><form className="global-search" role="search" onSubmit={event => { event.preventDefault(); router.push(`/opportunities?q=${encodeURIComponent(globalQuery)}`); }}><Search size={16} /><input aria-label="Search workspace opportunities" value={globalQuery} onChange={event => setGlobalQuery(event.target.value)} placeholder="Search opportunities…" /><kbd>↵</kbd></form><span className="local-pill"><span className="live-dot" />Local P1</span><div className="topbar-avatar" title={session.user.email}>{session.user.role.slice(0, 1).toUpperCase()}</div></div></header>
      <main id="main-content" className="main-content" tabIndex={-1}><div className="synthetic-banner"><span><span className="synthetic-dot" /><strong>Synthetic workspace</strong><span className="banner-description">Development fixtures · No real opportunities or business claims</span></span><span className="outbound-lock"><LockKeyhole size={13} />Outbound actions disabled</span></div>{page}<footer className="page-footer"><span>Koras AI Job Hunter <span className="footer-divider">/</span> Private P1 development</span><span><ShieldCheck size={13} />Evidence before action</span></footer></main>
    </div>
    {toast && <div className="toast" role="status"><Check size={17} /><span>{toast}</span><button className="icon-button" aria-label="Dismiss notification" onClick={() => setToast("")}><X size={15} /></button></div>}
  </div>;
}

function Brand() { return <div className="brand"><span className="brand-symbol"><span /><span /><span /><span /></span><div><strong>KORAS<span>AI</span></strong><small>JOB HUNTER</small></div></div>; }

function LoginPage({ onLogin, connectionError }: { onLogin: (value: Session) => void; connectionError: Error | null }) {
  const [email, setEmail] = useState("owner@kajh.invalid"), [password, setPassword] = useState("");
  const [error, setError] = useState(""), [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try { onLogin(await api<Session>("/auth/login", { method: "POST", body: { email, password } })); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "Unable to sign in."); }
    finally { setBusy(false); }
  }
  return <div className="login-shell"><section className="login-story"><Brand /><div className="login-story-body"><span className="login-eyebrow"><span className="live-dot" />PRIVATE OPPORTUNITY INTELLIGENCE</span><h1>The right work.<br /><span>With the evidence<br />to move forward.</span></h1><p>A focused workspace for discovering, qualifying, and reviewing global B2B consulting engagements.</p><div className="login-proof"><div><Globe2 size={20} /><strong>Global perspective</strong><span>Country-specific eligibility</span></div><div><ShieldCheck size={20} /><strong>Evidence-led decisions</strong><span>Every material fact traceable</span></div><div><LockKeyhole size={20} /><strong>You stay in control</strong><span>No automatic outreach</span></div></div></div><div className="login-story-footer"><span>Koras Technologies LLC</span><span>Local P1 · Synthetic data</span></div></section><section className="login-panel"><div className="login-form-wrap"><span className="login-lock"><LockKeyhole size={26} /></span><p className="eyebrow">YOUR PRIVATE WORKSPACE</p><h2>Welcome back.</h2><p className="login-intro">Sign in to your local opportunity workspace.</p>{connectionError && <Notice kind="warning">{connectionError.message}</Notice>}<form onSubmit={submit}><label htmlFor="email">Email address</label><input id="email" type="email" autoComplete="username" required value={email} onChange={event => setEmail(event.target.value)} /><label htmlFor="password">Password</label><input id="password" type="password" autoComplete="current-password" required value={password} onChange={event => setPassword(event.target.value)} placeholder="Enter your local development password" />{error && <p className="form-error" role="alert">{error}</p>}<button type="submit" className="button button-primary login-button" disabled={busy}>{busy ? <LoaderCircle size={17} className="spin" /> : null}{busy ? "Signing in…" : "Open workspace"}<ArrowRight size={17} /></button></form><div className="login-note"><ShieldCheck size={17} /><p>Use the generated local credentials from the project’s development setup. Owner, Reviewer, and Auditor accounts contain synthetic data only.</p></div></div><span className="login-panel-footer">Local authentication · Secure session · No external business actions</span></section></div>;
}

function OverviewPage({ context }: { context: AppContext }) {
  const { data, error, loading } = useResource<Dashboard>("/dashboard", context.refresh);
  const [busy, setBusy] = useState(false);
  async function ingest() {
    const source = data?.source_health.find(item => item.adapter_type === "synthetic" && item.enabled && !item.killed);
    if (!source) { context.notify("Enable an approved synthetic source in Source registry first."); return; }
    setBusy(true);
    try { await api(`/sources/${source.id}/ingest`, { method: "POST", body: {} }); context.notify("Synthetic discovery queued. Follow its progress in Workflow monitor."); context.bump(); }
    catch (failure) { context.notify(failure instanceof Error ? failure.message : "Unable to queue discovery."); }
    finally { setBusy(false); }
  }
  return <><PageHeading eyebrow="WORKSPACE OVERVIEW" title="Opportunity intelligence" description="A focused view of your consulting opportunities. Every decision starts with evidence." action={context.session.user.role.toLowerCase() === "owner" && <button className="button button-primary" onClick={ingest} disabled={busy || loading}>{busy ? <LoaderCircle className="spin" size={16} /> : <Play size={15} fill="currentColor" />}Run synthetic discovery</button>} />{loading ? <LoadingState /> : error ? <ErrorState error={error} retry={context.bump} /> : data && <>
    <div className="metric-grid"><Metric label="Eligible opportunities" value={data.counts.eligible ?? 0} icon={<BriefcaseBusiness size={19} />} detail="Confirmed deterministic gates" accent="green" /><Metric label="Needs your review" value={(data.counts.unknown ?? 0) + (data.counts.needs_review ?? 0)} icon={<CircleHelp size={19} />} detail="Missing or conflicting evidence" accent="amber" /><Metric label="Saved opportunities" value={data.counts.saved ?? 0} icon={<Bookmark size={19} />} detail="Your considered shortlist" accent="purple" /><Metric label="Active sources" value={data.counts.active_sources ?? 0} icon={<Database size={19} />} detail="Synthetic acquisition only" accent="blue" /></div>
    <div className="overview-grid"><div className="overview-primary"><Panel title="Latest opportunities" subtitle="Newly observed consulting opportunities and their eligibility evidence." action={<Link className="text-link" href="/opportunities">View all<ArrowRight size={15} /></Link>}>{data.recent.length ? <div className="opportunity-list">{data.recent.slice(0, 6).map(item => <OpportunityRow key={item.id} opportunity={item} />)}</div> : <EmptyState title="A clear starting point" description="Run the synthetic source to populate your local development workspace." />}</Panel><div className="principle-card"><span className="principle-icon"><ShieldCheck size={24} /></span><div><h3>Unknown is a useful answer.</h3><p>Missing B2B permission, residency, or delivery requirements stay visible for review. A contract label never becomes an eligibility guarantee.</p></div><Link href="/review" aria-label="Open review queue"><ArrowUpRight size={22} /></Link></div></div>
      <aside className="overview-secondary"><Panel title="Qualification snapshot" subtitle="Current deterministic classification"><div className="qualification-chart" role="img" aria-label={`Eligible ${data.counts.eligible ?? 0}, needs review ${data.counts.needs_review ?? 0}, unknown ${data.counts.unknown ?? 0}, ineligible ${data.counts.ineligible ?? 0}`}><div className="chart-total"><strong>{data.counts.total ?? 0}</strong><span>opportunities</span></div><div className="chart-bars">{[{ key: "eligible", label: "Eligible", color: "green" }, { key: "needs_review", label: "Needs Review", color: "amber" }, { key: "unknown", label: "Unknown", color: "slate" }, { key: "ineligible", label: "Ineligible", color: "red" }].map(item => <div className="chart-row" key={item.key}><span className={`legend-dot dot-${item.color}`} /><span>{item.label}</span><strong>{data.counts[item.key] ?? 0}</strong><div className="bar-track"><span className={`bar-${item.color}`} style={{ width: `${Math.max(0, ((data.counts[item.key] ?? 0) / Math.max(1, data.counts.total ?? 1)) * 100)}%` }} /></div></div>)}</div></div><Link className="panel-bottom-link" href="/review">Review unresolved evidence<ArrowRight size={15} /></Link></Panel>
        <Panel title="Source health" action={<Link className="icon-button" href="/sources" aria-label="View source registry"><MoreHorizontal size={19} /></Link>}><div className="source-health-list">{data.source_health.slice(0, 5).map(source => <div className="source-health-row" key={source.id}><span className={`source-health-icon ${source.adapter_type === "synthetic" ? "source-synthetic" : ""}`}><Database size={15} /></span><div><strong>{source.name}</strong><span>{source.adapter_type === "synthetic" ? "Development fixtures" : "Entitlement unresolved"}</span></div><span className={`tiny-state ${source.enabled && !source.killed ? "tiny-active" : ""}`}>{source.enabled && !source.killed ? "Active" : "Disabled"}</span></div>)}</div></Panel>
        <div className="future-card"><Sparkles size={19} /><strong>Intelligence, deliberately gated.</strong><p>AI ranking, live verification, and digests are reserved for later phases.</p><span>P2 · Not enabled</span></div>
      </aside></div>
  </>}</>;
}

function Metric({ label, value, icon, detail, accent }: { label: string; value: number; icon: ReactNode; detail: string; accent: string }) {
  return <section className="metric-card"><div className="metric-top"><span>{label}</span><span className={`metric-icon metric-${accent}`}>{icon}</span></div><strong className="metric-value">{new Intl.NumberFormat("en-US").format(value)}</strong><div className="metric-detail"><span className={`metric-detail-dot dot-${accent}`} />{detail}</div></section>;
}

export function OpportunityRow({ opportunity, onSave, canSave = false }: { opportunity: Opportunity; onSave?: (item: Opportunity) => void; canSave?: boolean }) {
  return <article className="opportunity-row"><div className={`company-avatar company-${(opportunity.organization?.charCodeAt(0) ?? 0) % 5}`}>{opportunity.organization?.slice(0, 2).toUpperCase() || "SY"}</div><div className="opportunity-row-main"><div className="opportunity-title-line"><Link href={`/opportunities/${opportunity.id}`}>{opportunity.title}</Link></div><div className="opportunity-company"><strong>{opportunity.organization}</strong><span>·</span><span>{prettyLabel(opportunity.category)}</span></div><div className="opportunity-meta"><span><Globe2 size={12} />{countryLabel(opportunity.country) || "Location unknown"}</span><span>{opportunity.workplace || "Workplace unknown"}</span><span>{rateLabel(opportunity)}</span></div></div><div className="opportunity-row-side"><StatusBadge status={opportunity.eligibility} /><span className="observed-date">{date(opportunity.last_seen)}</span></div>{canSave && onSave ? <button className={`icon-button save-button ${opportunity.saved ? "is-saved" : ""}`} aria-label={opportunity.saved ? `Unsave ${opportunity.title}` : `Save ${opportunity.title}`} aria-pressed={opportunity.saved} onClick={() => onSave(opportunity)}><Bookmark size={17} fill={opportunity.saved ? "currentColor" : "none"} /></button> : <Link className="row-arrow" href={`/opportunities/${opportunity.id}`} aria-label={`View ${opportunity.title}`}><ChevronRight size={18} /></Link>}</article>;
}

function SearchPage({ context, mode }: { context: AppContext; mode: string }) {
  const searchParams = useSearchParams();
  const [query, setQuery] = useState(searchParams.get("q") ?? "");
  const [appliedQuery, setAppliedQuery] = useState(searchParams.get("q") ?? "");
  const [status, setStatus] = useState(mode === "review" ? "Needs Review" : "");
  const [country, setCountry] = useState("");
  const [workplace, setWorkplace] = useState("");
  const [offset, setOffset] = useState(0);
  const params = new URLSearchParams({ limit: "20", offset: String(offset) });
  if (appliedQuery) params.set("q", appliedQuery);
  if (status) params.set("status", status);
  if (country) params.set("country", country);
  if (workplace) params.set("workplace", workplace);
  if (mode === "saved") params.set("saved", "true");
  const { data, error, loading } = useResource<OpportunityPage>(`/opportunities?${params}`, context.refresh);
  const canSave = context.session.user.role.toLowerCase() !== "auditor";
  const resetPage = () => setOffset(0);

  async function save(item: Opportunity) {
    try { await api(`/opportunities/${item.id}/saved`, { method: "PUT", body: { saved: !item.saved } }); context.notify(item.saved ? "Opportunity removed from your shortlist." : "Opportunity saved to your shortlist."); context.bump(); }
    catch (failure) { context.notify(failure instanceof Error ? failure.message : "Unable to save opportunity."); }
  }

  const heading = mode === "saved" ? { title: "Your considered shortlist", eyebrow: "SAVED OPPORTUNITIES", description: "Keep the engagements you want to come back to in one private place." } : mode === "review" ? { title: "Resolve the uncertainty", eyebrow: "REVIEW QUEUE", description: "Inspect the facts behind unresolved commercial and geographic eligibility." } : { title: "Find the right engagement", eyebrow: "GLOBAL OPPORTUNITY SEARCH", description: "Explore consulting opportunities with transparent eligibility and source evidence." };
  return <><PageHeading {...heading} />{mode === "review" && <div className="segmented" role="group" aria-label="Review status"><button className={status === "Needs Review" ? "selected" : ""} onClick={() => { setStatus("Needs Review"); resetPage(); }}>Needs Review</button><button className={status === "Unknown" ? "selected" : ""} onClick={() => { setStatus("Unknown"); resetPage(); }}>Unknown</button></div>}
    <Panel className="search-panel"><form className="search-filter-form" onSubmit={event => { event.preventDefault(); setAppliedQuery(query); resetPage(); }}><div className="search-field"><Search size={18} /><input aria-label="Search opportunity title or description" placeholder="Search by title, skill, or company…" value={query} onChange={event => setQuery(event.target.value)} /><button className="button button-primary button-small" type="submit">Search</button></div><div className="filter-row"><span className="filter-label"><ListFilter size={15} />Filters</span>{mode !== "review" && <select aria-label="Eligibility status" value={status} onChange={event => { setStatus(event.target.value); resetPage(); }}><option value="">All eligibility states</option>{["Eligible", "Unknown", "Needs Review", "Ineligible"].map(item => <option key={item}>{item}</option>)}</select>}<select aria-label="Country" value={country} onChange={event => { setCountry(event.target.value); resetPage(); }}><option value="">All countries</option>{["US", "CA", "GB", "DE", "AU", "FR", "IN", "NL"].map(item => <option value={item} key={item}>{countryLabel(item)}</option>)}</select><select aria-label="Workplace" value={workplace} onChange={event => { setWorkplace(event.target.value); resetPage(); }}><option value="">All workplaces</option><option value="remote">Remote</option><option value="hybrid">Hybrid</option><option value="onsite">Onsite</option></select>{(appliedQuery || country || workplace || (status && mode !== "review")) && <button className="clear-filters" type="button" onClick={() => { setQuery(""); setAppliedQuery(""); setCountry(""); setWorkplace(""); if (mode !== "review") setStatus(""); resetPage(); }}><X size={13} />Clear</button>}</div></form><div className="results-toolbar"><span><strong>{data?.total ?? "—"}</strong> {mode === "review" ? "opportunities awaiting evidence" : "opportunities"}</span><span>Most recently observed</span></div>{loading ? <LoadingState label="Loading opportunities" /> : error ? <ErrorState error={error} retry={context.bump} /> : data?.items.length ? <div className="opportunity-list">{data.items.map(item => <OpportunityRow opportunity={item} key={item.id} onSave={save} canSave={canSave} />)}</div> : <EmptyState title={mode === "saved" ? "Your shortlist is ready when you are" : "No opportunities in this view"} description={mode === "saved" ? "Save an opportunity from search to keep it here." : "Try a broader search or review the active filters. Only synthetic records are available in P1."} action={<Link href="/opportunities" className="button button-secondary">Explore opportunities<ArrowRight size={15} /></Link>} />}{data && data.total > 20 && <div className="pagination"><span>{offset + 1}–{Math.min(offset + 20, data.total)} of {data.total}</span><div><button className="button button-secondary button-small" disabled={offset === 0 || loading} onClick={() => setOffset(value => Math.max(0, value - 20))}><ChevronLeft size={15} />Previous</button><button className="button button-secondary button-small" disabled={offset + 20 >= data.total || loading} onClick={() => setOffset(value => value + 20)}>Next<ChevronRight size={15} /></button></div></div>}</Panel>
    <div className="subtle-information"><ShieldCheck size={15} />Remote availability and contract labels do not establish B2B or worldwide eligibility.</div></>;
}

function OpportunityPageView({ id, context }: { id: string; context: AppContext }) {
  const router = useRouter();
  const [separating, setSeparating] = useState<OpportunityDetail["sources"][number] | null>(null);
  const closeSeparation = useCallback(() => setSeparating(null), []);
  const { data, error, loading } = useResource<OpportunityDetail>(`/opportunities/${encodeURIComponent(id)}`, context.refresh);
  const [reviewing, setReviewing] = useState(false);
  const closeReview = useCallback(() => setReviewing(false), []);
  const canReview = context.session.user.role.toLowerCase() !== "auditor";
  async function save() {
    if (!data) return;
    try { await api(`/opportunities/${id}/saved`, { method: "PUT", body: { saved: !data.opportunity.saved } }); context.bump(); context.notify(data.opportunity.saved ? "Opportunity removed from shortlist." : "Opportunity saved."); }
    catch (failure) { context.notify(failure instanceof Error ? failure.message : "Save failed."); }
  }
  if (loading) return <LoadingState label="Loading opportunity and evidence" />;
  if (error) return <ErrorState error={error} retry={context.bump} />;
  if (!data) return null;
  const item = data.opportunity;
  return <><Link className="back-link" href="/opportunities"><ChevronLeft size={16} />All opportunities</Link><div className="detail-heading"><div className={`company-avatar company-avatar-large company-${(item.organization?.charCodeAt(0) ?? 0) % 5}`}>{item.organization?.slice(0, 2).toUpperCase()}</div><div><p className="eyebrow">{prettyLabel(item.category)} · SYNTHETIC OPPORTUNITY</p><h1>{item.title}</h1><p>{item.organization} <span>·</span> {countryLabel(item.country)} <span>·</span> {item.workplace || "Workplace unknown"}</p></div><div className="detail-heading-actions">{canReview && <button className={`button button-secondary ${item.saved ? "saved-active" : ""}`} onClick={save}><Bookmark size={16} fill={item.saved ? "currentColor" : "none"} />{item.saved ? "Saved" : "Save"}</button>}{canReview && <button className="button button-primary" onClick={() => setReviewing(true)}><ShieldCheck size={16} />Review evidence</button>}</div></div><div className="detail-summary-strip"><span><StatusBadge status={item.eligibility} /></span><span><strong>{rateLabel(item)}</strong><small>Disclosed compensation</small></span><span><strong>{date(item.first_seen)}</strong><small>First observed</small></span><span><strong>Version {item.version}</strong><small>Canonical content</small></span><span><strong>{prettyLabel(item.lifecycle)}</strong><small>Listing lifecycle</small></span></div>
    <div className="detail-grid"><div><Panel title="Engagement overview"><div className="description-text">{item.description || "The source has not supplied a description."}</div><div className="skills-section"><p className="field-label">TECHNICAL SPECIALIZATIONS</p><div className="tag-list">{item.skills.length ? item.skills.map(skill => <span className="tag" key={skill}>{skill}</span>) : <span className="muted">No skills recorded</span>}</div></div></Panel><Panel title="Eligibility evidence" subtitle="Deterministic rules, current outcomes, and supporting facts."><div className="evidence-list">{data.evidence.length ? data.evidence.map((entry, index) => <div className="evidence-row" key={`${entry.rule}-${index}`}><span className={`evidence-icon ${entry.outcome === "Eligible" ? "evidence-pass" : ""}`}>{entry.outcome === "Eligible" ? <Check size={16} /> : <CircleHelp size={16} />}</span><div><div className="evidence-title"><h3>{prettyLabel(entry.rule)}</h3><StatusBadge status={entry.outcome} /></div><EvidenceDetails details={entry.details} /></div></div>) : <EmptyState title="No rule evidence recorded" description="This record needs a deterministic assessment before it can be qualified." />}</div></Panel><Panel title="Observation & review timeline"><div className="timeline">{data.history.length ? data.history.map((entry, index) => <div className="timeline-item" key={index}><span className="timeline-dot" /><div><h3>{prettyLabel(str(entry.action ?? entry.event_type ?? entry.type, "Record observed"))}</h3><p>{str(entry.reason ?? object(entry.details).reason ?? entry.message, "Recorded in the workspace audit history.")}</p><small>{date(entry.created_at ?? entry.timestamp ?? entry.occurred_at)} · {str(entry.actor_type ?? entry.actor, "System")}</small></div></div>) : <div className="timeline-item"><span className="timeline-dot" /><div><h3>Source observation retained</h3><p>Current content version {item.version}</p><small>{date(item.last_seen)}</small></div></div>}</div></Panel></div><aside><Panel title="Source provenance" subtitle="Original references are preserved."><div className="provenance-list">{data.sources.length ? data.sources.map(source => <div className="provenance-item" key={`${source.id}-${source.external_id}`}><div><Database size={16} /><strong>{source.name}</strong></div><span className="field-label">SOURCE RECORD</span><code>{source.external_id}</code><span className="field-label">ORIGINAL REFERENCE</span><p className="source-url">{source.original_url}</p><small>Synthetic reference · No live source fetched</small>{context.session.user.role.toLowerCase() === "owner" && data.sources.length > 1 && <button className="text-link" onClick={() => setSeparating(source)}>Separate source reference<ChevronRight size={14} /></button>}</div>) : <Notice kind="warning">No currently permitted source reference is available.</Notice>}</div></Panel><Panel title="Recorded terms"><dl className="terms-list">{Object.entries(item.facts).filter(([key]) => !["description", "skills", "evidence"].includes(key)).slice(0, 14).map(([key, value]) => <div key={key}><dt>{prettyLabel(key)}</dt><dd>{value === null || value === undefined ? <span className="muted">Unknown</span> : typeof value === "boolean" ? value ? "Yes" : "No" : Array.isArray(value) ? array(value).join(", ") || "Not specified" : typeof value === "object" ? <span className="muted">Structured source evidence</span> : String(value)}</dd></div>)}</dl></Panel><div className="future-card"><LockKeyhole size={18} /><strong>Actions stay with you.</strong><p>Applications, proposals, and outreach are disabled in this local P1 workspace.</p><span>No external dispatch</span></div></aside></div>{separating && <SeparateSourceForm detail={data} source={separating} onClose={closeSeparation} onSaved={newId => { closeSeparation(); context.bump(); context.notify("Source separated into its own canonical opportunity. The audit history is preserved."); router.push(`/opportunities/${newId}`); }} />}{reviewing && <ReviewForm detail={data} onClose={closeReview} onSaved={() => { closeReview(); context.bump(); context.notify("Evidence review recorded. Eligibility was recomputed from the facts."); }} />}</>;
}

function EvidenceDetails({ details }: { details: unknown }) {
  if (typeof details === "string") return <p>{details}</p>;
  const record = object(details);
  const narrative = record.reason ?? record.excerpt ?? record.message ?? record.explanation;
  if (narrative) return <p>{str(narrative)}</p>;
  return <div className="evidence-facts">{Object.entries(record).slice(0, 5).map(([key, value]) => <span key={key}><strong>{prettyLabel(key)}:</strong> {typeof value === "object" ? JSON.stringify(value) : String(value ?? "Unknown")}</span>)}</div>;
}

function ReviewForm({ detail, onClose, onSaved }: { detail: OpportunityDetail; onClose: () => void; onSaved: () => void }) {
  const [reason, setReason] = useState("");
  const [factsText, setFactsText] = useState(JSON.stringify(detail.opportunity.facts, null, 2));
  const [error, setError] = useState(""), [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault(); setError("");
    let facts: JsonObject;
    try { const parsed = JSON.parse(factsText); if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error(); facts = parsed; }
    catch { setError("Evidence facts must be a valid object. Correct the highlighted source facts and retry."); return; }
    setBusy(true);
    try { await api(`/opportunities/${detail.opportunity.id}/review`, { method: "POST", body: { version: detail.opportunity.version, facts, reason } }); onSaved(); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "Review could not be saved."); }
    finally { setBusy(false); }
  }
  return <Modal title="Review eligibility evidence" onClose={onClose}><form onSubmit={submit} className="editor-form"><Notice>Update supported source facts. Eligibility is recomputed by the rules engine; this form cannot directly assign an Eligible status.</Notice><label htmlFor="review-facts">Recorded evidence facts</label><textarea id="review-facts" className="code-editor" rows={14} value={factsText} onChange={event => setFactsText(event.target.value)} spellCheck={false} /><label htmlFor="review-reason">Review rationale</label><textarea id="review-reason" rows={3} required minLength={5} maxLength={2000} value={reason} onChange={event => setReason(event.target.value)} placeholder="Describe the supporting evidence and why this correction is justified." />{error && <p className="form-error" role="alert">{error}</p>}<div className="modal-actions"><button type="button" className="button button-secondary" onClick={onClose}>Cancel</button><button type="submit" className="button button-primary" disabled={busy || reason.trim().length < 5}>{busy && <LoaderCircle className="spin" size={15} />}Record review</button></div></form></Modal>;
}

function SeparateSourceForm({ detail, source, onClose, onSaved }: { detail: OpportunityDetail; source: OpportunityDetail["sources"][number]; onClose: () => void; onSaved: (id: string) => void }) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const result = await api<{ id: string }>(`/opportunities/${detail.opportunity.id}/split-source`, { method: "POST", body: { version: detail.opportunity.version, source_link_id: source.id, reason } });
      onSaved(result.id);
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Source could not be separated."); }
    finally { setBusy(false); }
  }
  return <Modal title="Separate source reference" onClose={onClose}><form className="editor-form" onSubmit={submit}><Notice>Use this correction when distinct requisitions were grouped together. The selected observation becomes a separate canonical opportunity, and the original keeps its remaining references.</Notice><p className="small-text"><strong>{source.name}</strong><br /><code>{source.external_id}</code></p><label htmlFor="separation-reason">Separation rationale</label><textarea id="separation-reason" required minLength={5} maxLength={2000} rows={3} value={reason} onChange={event => setReason(event.target.value)} placeholder="Explain why this source represents a distinct engagement." />{error && <p className="form-error" role="alert">{error}</p>}<div className="modal-actions"><button type="button" className="button button-secondary" onClick={onClose}>Cancel</button><button className="button button-primary" type="submit" disabled={busy || reason.trim().length < 5}>{busy && <LoaderCircle className="spin" size={15} />}Separate reference</button></div></form></Modal>;
}
