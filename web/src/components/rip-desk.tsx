"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

type Preview = {
  url: string;
  canonical: string;
  kind: string;
  title: string;
  csrf: string;
  selection: string;
  robots: { allowed: boolean; warning: string };
  warnings: string[];
};

type Job = {
  id: string;
  url?: string;
  mode?: string;
  status: string;
  error?: string;
  result: {
    stored?: boolean;
    saved?: boolean;
    title?: string;
    method?: string;
    words?: number;
    pushes?: number;
    id?: string;
    warnings?: string[];
    paste_required?: boolean;
    guard?: { flagged: boolean; reason: string };
    discovery?: {
      candidates: { url: string; title: string; n_items: number }[];
      selector: { selector: string; items: { title: string; url: string }[] } | null;
      warnings: string[];
    };
    source?: { id: string; url: string; kind: string };
  };
};

async function readJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers || {}) },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof body.detail === "string" ? body.detail : response.statusText;
    throw new Error(detail || "request failed");
  }
  return body as T;
}

export function RipDesk() {
  const params = useSearchParams();
  const initialUrl = params.get("url") || "";
  const initialTitle = params.get("title") || "";
  const initialSel = params.get("sel") || "";
  const [url, setUrl] = useState(initialUrl);
  const [note, setNote] = useState("");
  const [selection, setSelection] = useState(initialSel);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [recent, setRecent] = useState<Job[]>([]);

  useEffect(() => {
    let cancelled = false;
    readJson<Job[]>("/api/rip/jobs")
      .then((rows) => {
        if (!cancelled) setRecent(rows);
      })
      .catch(() => {
        if (!cancelled) setRecent([]);
      });
    return () => {
      cancelled = true;
    };
  }, [job]);

  useEffect(() => {
    if (!initialUrl) {
      setPreview(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError("");
    setJob(null);
    const query = new URLSearchParams({ url: initialUrl, title: initialTitle, sel: initialSel });
    readJson<Preview>(`/api/rip/preview?${query.toString()}`)
      .then((row) => {
        if (!cancelled) setPreview(row);
      })
      .catch((exc: unknown) => {
        if (!cancelled) {
          setPreview(null);
          setError(exc instanceof Error ? exc.message : "Preview failed");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [initialUrl, initialTitle, initialSel]);

  async function act(mode: "rip" | "subscribe", extra?: { pick?: number; selector?: string; feed_url?: string }) {
    if (!preview?.csrf) {
      setError("This page has no CSRF token yet. Open it from the bookmarklet or paste a URL.");
      return;
    }
    setBusy(mode);
    setError("");
    try {
      if (extra?.feed_url || extra?.selector) {
        const saved = await readJson<Job>("/api/rip/subscribe", {
          method: "POST",
          body: JSON.stringify({
            url: preview.url,
            csrf: preview.csrf,
            feed_url: extra.feed_url || "",
            selector: extra.selector || "",
            note,
          }),
        });
        setJob(saved);
        return;
      }
      const saved = await readJson<Job>("/api/rip", {
        method: "POST",
        body: JSON.stringify({
          url: preview.url,
          mode,
          sel: selection,
          note,
          title: preview.title,
          csrf: preview.csrf,
          pick: extra?.pick,
        }),
      });
      setJob(saved);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Rip failed");
    } finally {
      setBusy("");
    }
  }

  const result = job?.result;
  const discovery = result?.discovery;

  return (
    <main className="min-h-screen bg-black font-mono text-foreground">
      <header className="border-b border-white/15 px-4 py-4 md:px-6">
        <p className="text-[11px] tracking-[0.42em] text-primary">WATCH TOWER</p>
        <div className="mt-2 flex flex-wrap items-end justify-between gap-3">
          <h1 className="text-3xl tracking-tight text-white">Rip</h1>
          <Link href="/" className="text-[11px] uppercase tracking-[0.18em] text-white/50">
            Back to the catalog
          </Link>
        </div>
        <p className="mt-3 max-w-xl text-sm text-white/55">
          A GET only shows this page. Rip now and Subscribe send a token the page just minted, so another site cannot fire them.
        </p>
      </header>

      <section className="grid gap-6 px-4 py-5 md:grid-cols-[minmax(0,1fr)_280px] md:px-6">
        <div>
          {!initialUrl && (
            <form
              className="space-y-2"
              onSubmit={(event) => {
                event.preventDefault();
                const next = url.trim();
                if (!next) {
                  setError("Paste a URL first.");
                  return;
                }
                window.location.assign(`/rip?url=${encodeURIComponent(next)}`);
              }}
            >
              <Input value={url} placeholder="https://" onChange={(event) => setUrl(event.target.value)} />
              <Button type="submit" size="sm">
                Open confirm
              </Button>
            </form>
          )}

          {loading && <p className="text-sm text-white/50">Checking the URL…</p>}
          {error && <p className="mt-3 text-sm text-primary">{error}</p>}

          {preview && (
            <div className="mt-4 border border-white/15 p-4">
              <p className="text-[10px] uppercase tracking-[0.22em] text-white/40">01 · {preview.kind}</p>
              <h2 className="mt-2 text-lg text-white">{preview.title || preview.canonical}</h2>
              <p className="mt-2 break-all text-xs text-white/45">{preview.canonical}</p>
              <p className="mt-3 text-xs text-white/70">
                {preview.robots.allowed
                  ? "robots.txt allows this fetch."
                  : `robots.txt warning: ${preview.robots.warning}. A rip can continue. A subscription will not.`}
              </p>
              {preview.selection && (
                <p className="mt-3 text-xs text-white/55">Selection saved with the page, {preview.selection.length} characters.</p>
              )}
              <label className="mt-4 block text-[10px] uppercase tracking-widest text-white/35">
                Note
                <Input className="mt-1" value={note} onChange={(event) => setNote(event.target.value)} />
              </label>
              <label className="mt-3 block text-[10px] uppercase tracking-widest text-white/35">
                Paste, if the page is thin
                <Textarea className="mt-1" value={selection} onChange={(event) => setSelection(event.target.value)} />
              </label>
              <div className="mt-4 flex flex-wrap gap-2">
                <Button size="sm" disabled={!!busy} onClick={() => act("rip")}>
                  {busy === "rip" ? "Ripping…" : "Rip now"}
                </Button>
                <Button size="sm" variant="secondary" disabled={!!busy} onClick={() => act("subscribe")}>
                  {busy === "subscribe" ? "Looking…" : "Subscribe"}
                </Button>
              </div>
            </div>
          )}

          {result && (
            <div className="mt-4 border border-white/15 p-4">
              <p className="text-[10px] uppercase tracking-[0.22em] text-primary">02 · {job?.status}</p>
              {result.title && <h2 className="mt-2 text-lg text-white">{result.title}</h2>}
              {result.method && (
                <p className="mt-2 text-xs text-white/60">
                  {result.method}
                  {typeof result.words === "number" ? ` · ${result.words} words` : ""}
                  {typeof result.pushes === "number" ? ` · ${result.pushes} push` : ""}
                </p>
              )}
              {result.guard?.flagged && <p className="mt-2 text-xs text-primary">{result.guard.reason}</p>}
              {(result.warnings || []).map((warning) => (
                <p key={warning} className="mt-1 text-xs text-white/50">
                  {warning}
                </p>
              ))}
              {result.paste_required && (
                <p className="mt-3 text-sm text-white/70">Nothing sturdy enough to store. Paste the article in the box and rip again.</p>
              )}
              {result.saved && result.source && (
                <p className="mt-3 text-sm text-white">
                  Saved {result.source.id} into the local source list. It shows up in the catalog on the next load.
                </p>
              )}
              {discovery && !result.saved && (
                <div className="mt-4 space-y-3">
                  {discovery.candidates.map((row, index) => (
                    <button
                      key={row.url}
                      className="block w-full border border-white/15 px-3 py-2 text-left"
                      onClick={() => act("subscribe", { pick: index })}
                    >
                      <span className="text-[10px] uppercase tracking-widest text-primary">{row.n_items} items</span>
                      <span className="mt-1 block text-sm text-white">{row.title}</span>
                    </button>
                  ))}
                  {discovery.selector && discovery.selector.items.length > 0 && (
                    <div className="border border-white/15 p-3">
                      <p className="text-[10px] uppercase tracking-widest text-white/40">
                        No feed. CSS {discovery.selector.selector} sees {discovery.selector.items.length} items.
                      </p>
                      <ul className="mt-2 space-y-1">
                        {discovery.selector.items.slice(0, 6).map((item) => (
                          <li key={item.url || item.title} className="text-xs text-white/70">
                            {item.title}
                          </li>
                        ))}
                      </ul>
                      <Button
                        className="mt-3"
                        size="sm"
                        variant="secondary"
                        onClick={() => act("subscribe", { selector: discovery.selector?.selector || "article" })}
                      >
                        Save selector feed
                      </Button>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        <aside>
          <h2 className="text-[11px] uppercase tracking-[0.22em] text-white/40">Recent rips</h2>
          <ul className="mt-3 border-t border-white/10">
            {recent.length === 0 ? (
              <li className="py-3 text-sm text-white/40">None yet.</li>
            ) : (
              recent.map((row) => (
                <li key={row.id} className="border-b border-white/10 py-2">
                  <p className="text-[10px] uppercase tracking-widest text-primary">
                    {row.mode || "rip"} · {row.status}
                  </p>
                  <p className="mt-1 break-all text-xs text-white/70">{row.result?.title || row.url}</p>
                </li>
              ))
            )}
          </ul>
        </aside>
      </section>
    </main>
  );
}
