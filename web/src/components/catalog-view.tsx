"use client";

import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

type Source = {
  id: string;
  kind: string;
  name: string;
  group: string;
  url: string;
  source: string;
  feed_id: string;
  status: "live" | "armed" | "seen" | "quiet" | "off" | string;
  dated: boolean;
  items: number;
  oldest: string;
  newest: string;
  detail: string;
  last_error: string;
  probe_status: number;
  probe_at: string;
};

type Intake = {
  item_id: string;
  kind: string;
  method: string;
  note: string;
  title: string;
  url: string;
  published_at: string;
  created_at: string;
};

type WireItem = {
  id: string;
  title: string;
  url: string;
  source: string;
  feed_id: string;
  published_at: string;
  words: number;
};

type MatchLite = {
  id: string;
  title: string;
  action: string;
  source: string;
  feed_id: string;
  indicator_id: string;
  published_at?: string;
  p_hit: number;
};

type Catalog = {
  sources: Source[];
  intake: Intake[];
  items: WireItem[];
};

const ACTIONS = ["push", "digest", "suppressed", "drop"] as const;

async function readJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers || {}) },
  });
  if (!response.ok) {
    const detail = await response.text();
    let message = detail || response.statusText;
    try {
      const parsed = JSON.parse(detail) as { detail?: unknown };
      if (typeof parsed.detail === "string") message = parsed.detail;
    } catch {
      /* plain */
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

function statusLabel(status: string) {
  if (status === "live") return "Live";
  if (status === "armed") return "Armed";
  if (status === "seen") return "Seen";
  if (status === "quiet") return "Quiet";
  return "Off";
}

export function CatalogView({
  query,
  matches,
  onOpen,
}: {
  query: string;
  matches: MatchLite[];
  onOpen: (id: string) => void;
}) {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [focusItems, setFocusItems] = useState<WireItem[]>([]);
  const [error, setError] = useState("");
  const [pinging, setPinging] = useState(false);
  const [title, setTitle] = useState("");
  const [url, setUrl] = useState("");
  const [text, setText] = useState("");
  const [published, setPublished] = useState("");
  const [uploadNote, setUploadNote] = useState("");

  async function load() {
    const next = await readJson<Catalog>(`/api/catalog?${query.replace(/^&/, "")}`);
    setCatalog(next);
  }

  useEffect(() => {
    load().catch((exc) => setError(exc instanceof Error ? exc.message : "Catalog failed"));
    // query is the published-date window.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);

  useEffect(() => {
    const row = (catalog?.sources || []).find((item) => item.id === selected);
    if (!row) {
      setFocusItems([]);
      return;
    }
    const params = new URLSearchParams(query.replace(/^&/, ""));
    params.set("source", row.source);
    if (row.feed_id) params.set("feed_id", row.feed_id);
    readJson<WireItem[]>(`/api/catalog/items?${params.toString()}`)
      .then(setFocusItems)
      .catch(() => setFocusItems([]));
  }, [selected, catalog, query]);

  const sources = catalog?.sources || [];
  const active = sources.filter((row) => row.status !== "off");
  const dark = sources.filter((row) => row.status === "off");
  const focus = sources.find((row) => row.id === selected) || null;

  const decisions = useMemo(() => {
    const counts = { push: 0, digest: 0, suppressed: 0, drop: 0 };
    for (const row of matches) {
      if (row.action in counts) counts[row.action as keyof typeof counts] += 1;
    }
    return counts;
  }, [matches]);

  const edges = useMemo(() => {
    const bySource = new Map<string, Record<string, number>>();
    for (const row of matches) {
      const key = row.feed_id ? `${row.source}:${row.feed_id}` : row.source;
      const bag = bySource.get(key) || { push: 0, digest: 0, suppressed: 0, drop: 0 };
      if (row.action in bag) bag[row.action] += 1;
      bySource.set(key, bag);
    }
    const lines: { source: string; action: (typeof ACTIONS)[number]; n: number }[] = [];
    for (const source of active) {
      const bag = bySource.get(source.id);
      if (!bag) continue;
      for (const action of ACTIONS) {
        if (bag[action] > 0) lines.push({ source: source.id, action, n: bag[action] });
      }
    }
    return lines;
  }, [active, matches]);

  const height = Math.max(520, active.length * 36 + 40);

  return (
    <div>
      <div className="flex flex-wrap items-end justify-between gap-3 border-b border-white/15 px-4 py-4 md:px-6">
        <div>
          <p className="text-[11px] tracking-[0.32em] text-primary">DATA CATALOG</p>
          <h2 className="mt-2 text-2xl text-white">Live tether</h2>
          <p className="mt-2 max-w-xl text-sm text-white/50">
            Configured sources on the left, decisions on the right. A line only moves when something from that source was scored.
          </p>
        </div>
        <Button
          size="sm"
          variant="secondary"
          disabled={pinging}
          onClick={async () => {
            setPinging(true);
            setError("");
            try {
              await readJson("/api/catalog/probe", { method: "POST" });
              await load();
            } catch (exc) {
              setError(exc instanceof Error ? exc.message : "Ping failed");
            } finally {
              setPinging(false);
            }
          }}
        >
          {pinging ? "Pinging feeds" : "Ping sources"}
        </Button>
      </div>
      {error && <p className="px-6 py-2 text-xs text-primary">{error}</p>}

      <div className="relative border-b border-white/15" style={{ minHeight: height }}>
        <div className="tether-scan pointer-events-none absolute inset-x-0 top-0 h-16" />
        <div className="grid md:grid-cols-[minmax(220px,280px)_1fr_minmax(120px,160px)]" style={{ minHeight: height }}>
          <ul className="relative z-10 border-b border-white/10 md:border-b-0 md:border-r">
            {active.map((row, index) => (
              <li key={row.id}>
                <button
                  className={`flex w-full items-start gap-3 px-4 py-2 text-left ${
                    selected === row.id ? "bg-white text-black" : "text-white"
                  }`}
                  onClick={() => setSelected(selected === row.id ? null : row.id)}
                >
                  <span className={`mt-1 h-2 w-2 shrink-0 ${row.status === "live" ? "catalog-live bg-primary" : "bg-white/30"}`} />
                  <span className="min-w-0">
                    <span className="block text-[10px] tracking-[0.18em] uppercase opacity-60">
                      {String(index + 1).padStart(2, "0")} {statusLabel(row.status)}
                      {row.dated ? "" : " · undated"}
                    </span>
                    <span className="block truncate text-sm">{row.name}</span>
                    <span className={`block text-[10px] ${selected === row.id ? "text-black/60" : "text-white/35"}`}>
                      {row.items} on the wire{row.group ? ` · ${row.group}` : ""}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
          <svg className="pointer-events-none absolute inset-0 hidden h-full w-full md:block" viewBox={`0 0 1000 ${height}`} preserveAspectRatio="none">
            {edges.map((edge) => {
              const sourceIndex = active.findIndex((row) => row.id === edge.source);
              const actionIndex = ACTIONS.indexOf(edge.action);
              if (sourceIndex < 0 || actionIndex < 0) return null;
              const y1 = 18 + sourceIndex * 36 + 14;
              const y2 = (height / (ACTIONS.length + 1)) * (actionIndex + 1);
              const dim = focus && focus.id !== edge.source;
              const pathId = `tether-${edge.source}-${edge.action}`.replace(/[^a-zA-Z0-9_-]/g, "-");
              return (
                <g key={`${edge.source}-${edge.action}`} opacity={dim ? 0.08 : 0.9}>
                  <path
                    id={pathId}
                    d={`M 250 ${y1} C 520 ${y1}, 620 ${y2}, 860 ${y2}`}
                    className="tether-flow"
                    strokeWidth={Math.min(2.4, 0.6 + edge.n * 0.35)}
                  />
                  <circle r="3.2" className="tether-dot">
                    <animateMotion dur={`${2.4 + (sourceIndex % 5) * 0.35}s`} repeatCount="indefinite">
                      <mpath href={`#${pathId}`} />
                    </animateMotion>
                  </circle>
                </g>
              );
            })}
          </svg>
          <div className="relative z-10 max-h-[70vh] overflow-auto bg-black/75 px-4 py-3">
            {!focus ? (
              <p className="text-xs uppercase tracking-[0.18em] text-white/35">Select a source to read what it published.</p>
            ) : (
              <>
                <p className="text-[11px] tracking-[0.2em] uppercase text-primary">{focus.name}</p>
                <p className="mt-1 text-xs text-white/45">{focus.detail}</p>
                <ul className="mt-3">
                  {focusItems.length === 0 ? (
                    <li className="py-2 text-sm text-white/40">No items in this window.</li>
                  ) : (
                    focusItems.map((item) => {
                      const match = matches.find((rowMatch) => rowMatch.title === item.title && rowMatch.source === item.source);
                      return (
                        <li key={item.id} className="border-b border-white/10 py-2">
                          <button className="block w-full text-left" onClick={() => match && onOpen(match.id)} disabled={!match}>
                            <span className="text-[10px] tracking-widest text-white/35">
                              {(item.published_at || "").slice(0, 16).replace("T", " ")}
                            </span>
                            <span className="mt-1 block text-sm text-white">{item.title}</span>
                            <span className="text-[10px] uppercase tracking-widest text-white/35">
                              {item.words} words{match ? ` · ${match.action}` : " · no lane"}
                            </span>
                          </button>
                        </li>
                      );
                    })
                  )}
                </ul>
              </>
            )}
          </div>
          <ul className="relative z-10 flex flex-row gap-2 border-white/10 px-3 py-4 md:flex-col md:justify-around md:border-l">
            {ACTIONS.map((action) => (
              <li key={action} className="border border-white/15 px-3 py-3">
                <p className="text-[10px] tracking-[0.22em] uppercase text-white/40">{action}</p>
                <p className={`mt-1 text-2xl ${action === "push" ? "text-primary" : "text-white"}`}>{decisions[action]}</p>
              </li>
            ))}
          </ul>
        </div>
      </div>

      <div className="grid gap-0 lg:grid-cols-[1fr_1fr]">
        <section className="border-b border-white/15 px-4 py-4 lg:border-b-0 lg:border-r md:px-6">
          <h3 className="text-[11px] tracking-[0.22em] uppercase text-white/45">
            {focus ? focus.name : "Newest in this window"}
          </h3>
          {focus && (
            <p className="mt-2 text-sm text-white/55">
              {focus.detail}
              {focus.url ? ` · ${focus.url}` : ""}
              {focus.last_error ? ` · ${focus.last_error}` : ""}
              {focus.probe_at ? ` · ping ${focus.probe_status || "fail"}` : ""}
            </p>
          )}
          <ul className="mt-3 border-t border-white/10">
            {(focus ? focusItems : catalog?.items || []).slice(0, 12).length === 0 ? (
              <li className="py-3 text-sm text-white/40">Nothing in this window.</li>
            ) : (
              (focus ? focusItems : catalog?.items || []).slice(0, 12).map((item) => (
                <li key={item.id} className="border-b border-white/10 py-2">
                  <p className="text-[10px] tracking-widest text-white/35">{item.published_at.slice(0, 16).replace("T", " ")}</p>
                  <p className="mt-1 text-sm text-white">{item.title}</p>
                </li>
              ))
            )}
          </ul>
          {dark.length > 0 && (
            <p className="mt-4 text-[11px] uppercase tracking-[0.16em] text-white/30">
              Off · {dark.map((row) => row.name).join(" · ")}
            </p>
          )}
        </section>

        <section className="px-4 py-4 md:px-6">
          <h3 className="text-[11px] tracking-[0.22em] uppercase text-white/45">Manual upload and extraction</h3>
          <form
            className="mt-3 space-y-2"
            onSubmit={async (event) => {
              event.preventDefault();
              setUploadNote("");
              setError("");
              try {
                const saved = await readJson<{ title: string; method: string; pushes: number; duplicates: number }>(
                  "/api/catalog/upload",
                  {
                    method: "POST",
                    body: JSON.stringify({ title, text, url, published_at: published }),
                  },
                );
                setUploadNote(
                  saved.duplicates
                    ? "Already on the wire."
                    : `Processed with ${saved.method}. ${saved.pushes} push.`,
                );
                setTitle("");
                setText("");
                setUrl("");
                await load();
              } catch (exc) {
                setError(exc instanceof Error ? exc.message : "Upload failed");
              }
            }}
          >
            <Input value={title} placeholder="Title" onChange={(event) => setTitle(event.target.value)} />
            <Input value={url} placeholder="URL, optional" onChange={(event) => setUrl(event.target.value)} />
            <Textarea value={text} placeholder="Paste the article, or leave this empty and use the URL" onChange={(event) => setText(event.target.value)} />
            <label className="block text-[10px] uppercase tracking-widest text-white/35">
              Publish date
              <Input className="mt-1" type="datetime-local" value={published} onChange={(event) => setPublished(event.target.value)} />
            </label>
            <Button type="submit" size="sm">
              Process onto the wire
            </Button>
            {uploadNote && <p className="text-xs text-white/55">{uploadNote}</p>}
          </form>
          <ul className="mt-4 border-t border-white/10">
            {(catalog?.intake || []).length === 0 ? (
              <li className="py-3 text-sm text-white/40">No manual uploads or full-text extracts yet.</li>
            ) : (
              catalog?.intake.slice(0, 8).map((row) => (
                <li key={row.item_id} className="border-b border-white/10 py-2">
                  <p className="text-[10px] tracking-[0.18em] uppercase text-primary">
                    {row.kind} · {row.method}
                  </p>
                  <p className="mt-1 text-sm text-white">{row.title || row.note || row.item_id}</p>
                  <p className="text-[10px] text-white/35">{row.published_at.slice(0, 16).replace("T", " ") || row.created_at.slice(0, 16)}</p>
                </li>
              ))
            )}
          </ul>
        </section>
      </div>
    </div>
  );
}
