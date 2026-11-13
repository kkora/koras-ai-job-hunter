"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { AlertCircle, ArrowRight, Check, ChevronRight, CircleHelp, LoaderCircle, Search, ShieldCheck, X } from "lucide-react";
import { api, ApiError, type Eligibility } from "@/lib/api";

export function useResource<T>(path: string, refresh = 0) {
  const [resource, setResource] = useState<{ path: string; refresh: number; data: T | null; error: Error | null; loading: boolean } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const data = await api<T>(path, { signal: controller.signal });
        if (!controller.signal.aborted) setResource({ path, refresh, data, error: null, loading: false });
      } catch (failure) {
        if (!controller.signal.aborted) setResource({ path, refresh, data: null, error: failure instanceof Error ? failure : new Error("Unable to load this view."), loading: false });
      }
    }
    void load();
    return () => controller.abort();
  }, [path, refresh]);
  return resource?.path === path && resource.refresh === refresh ? resource : { data: null, error: null, loading: true };
}

export function StatusBadge({ status }: { status: Eligibility | string }) {
  const style = status === "Eligible" ? "eligible" : status === "Ineligible" ? "ineligible" : status === "Needs Review" ? "review" : status === "Unknown" ? "unknown" : "neutral";
  return <span className={`badge badge-${style}`}><span className="badge-dot" />{status}</span>;
}

export function ErrorState({ error, retry }: { error: Error; retry?: () => void }) {
  return <div className="error-state" role="alert"><AlertCircle size={20} /><div><strong>{error instanceof ApiError && error.status === 401 ? "Your session needs attention" : "We couldn’t load this view"}</strong><p>{error.message}</p>{error instanceof ApiError && error.requestId && <small>Reference: {error.requestId}</small>}</div>{retry && <button className="button button-secondary" onClick={retry}>Try again</button>}</div>;
}

export function EmptyState({ title, description, action }: { title: string; description: string; action?: ReactNode }) {
  return <div className="empty-state"><span className="empty-icon"><Search size={24} /></span><h3>{title}</h3><p>{description}</p>{action}</div>;
}

export function LoadingState({ label = "Loading workspace" }: { label?: string }) {
  return <div className="loading-state" role="status"><LoaderCircle className="spin" size={22} /><span>{label}</span><div className="skeleton-lines"><span /><span /><span /></div></div>;
}

export function PageHeading({ eyebrow, title, description, action }: { eyebrow?: string; title: string; description: string; action?: ReactNode }) {
  return <div className="page-heading"><div>{eyebrow && <p className="eyebrow">{eyebrow}</p>}<h1>{title}</h1><p>{description}</p></div>{action && <div className="heading-action">{action}</div>}</div>;
}

export function Panel({ title, subtitle, action, children, className = "" }: { title?: string; subtitle?: string; action?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={`panel ${className}`}>{title && <div className="panel-heading"><div><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div>{action}</div>}{children}</section>;
}

export function Notice({ children, kind = "info" }: { children: ReactNode; kind?: "info" | "warning" | "success" }) {
  return <div className={`notice notice-${kind}`} role={kind === "warning" ? "alert" : "status"}>{kind === "success" ? <Check size={17} /> : kind === "warning" ? <CircleHelp size={17} /> : <ShieldCheck size={17} />}<div>{children}</div></div>;
}

export function Modal({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  const titleId = useId();
  const modalRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  useEffect(() => { closeRef.current = onClose; }, [onClose]);
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const background = Array.from(document.body.children).filter((element): element is HTMLElement => element instanceof HTMLElement && element !== modalRef.current);
    const priorInert = background.map(element => element.inert);
    background.forEach(element => { element.inert = true; });
    const priorOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const first = modalRef.current?.querySelector<HTMLElement>("button");
    first?.focus();
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") closeRef.current();
      if (event.key === "Tab") {
        const dialog = modalRef.current;
        const controls = Array.from(dialog?.querySelectorAll<HTMLElement>("button:not(:disabled), input:not(:disabled):not([type='hidden']), select:not(:disabled), textarea:not(:disabled), a[href]") ?? []).filter(element => element.tabIndex >= 0 && element.getClientRects().length > 0);
        const firstControl = controls[0], lastControl = controls.at(-1);
        if (event.shiftKey && document.activeElement === firstControl) { event.preventDefault(); lastControl?.focus(); }
        if (!event.shiftKey && document.activeElement === lastControl) { event.preventDefault(); firstControl?.focus(); }
      }
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      background.forEach((element, index) => { element.inert = priorInert[index]; });
      document.body.style.overflow = priorOverflow;
      if (previous?.isConnected) previous.focus();
      else document.getElementById("main-content")?.focus();
    };
  }, []);
  return createPortal(<div className="modal-backdrop" ref={modalRef} onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}><section className="modal" role="dialog" aria-modal="true" aria-labelledby={titleId}><div className="modal-heading"><h2 id={titleId}>{title}</h2><button className="icon-button" onClick={onClose} aria-label="Close dialog"><X size={20} /></button></div>{children}</section></div>, document.body);
}

export function TextLink({ children }: { children: ReactNode }) { return <span className="text-link">{children}<ArrowRight size={15} /></span>; }
export function Breadcrumb({ children }: { children: ReactNode }) { return <div className="breadcrumb"><span>Workspace</span><ChevronRight size={13} /><strong>{children}</strong></div>; }
