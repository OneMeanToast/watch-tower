"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { CatalogView } from "@/components/catalog-view";
import { ExtractDesk } from "@/components/extract-desk";
import { SpendView } from "@/components/spend-view";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

type View = "highway" | "catalog" | "indicators" | "trace" | "digest" | "export" | "desks" | "spend" | "extract";

type Gate = { gate: string; state: string; detail: string };

type Match = {
  id: string;
  indicator_id: string;
  title: string;
  url: string;
  source: string;
  feed_id: string;
  text: string;
  cosine: number;
  p_hit: number;
  severity: number;
  confidence: number;
  action: string;
  reason: string;
  backend: string;
  explanation: string;
  snippets: string[];
  trace: Gate[];
  event_id: string;
  created_at: string;
};

type Indicator = {
  id: string;
  name: string;
  description: string;
  sources: string[];
  keywords_any: string[];
  min_cosine: number;
  alert_if: string;
  digest_if: string | null;
  cooldown: string;
  push_min_confidence: number;
  uncertain_min: number;
};

type Desk = {
  id: string;
  name: string;
  brief: string;
  indicators: string[];
  exports: string[];
  digest_seen_at: string;
};

type Status = {
  version: string;
  counts: { items: number; matches: number; alerts: number; feedback: number };
  vector_backend: string;
  last_report: {
    mode: string;
    pushes: number;
    digests: number;
    items_new: number;
    duplicates: number;
    spam_dropped?: number;
    backend: string;
    embedder: string;
    errors: string[];
  } | null;
};

const VIEWS: { id: View; label: string }[] = [
  { id: "highway", label: "Highway" },
  { id: "catalog", label: "Catalog" },
  { id: "indicators", label: "Indicators" },
  { id: "trace", label: "Trace" },
  { id: "digest", label: "Digest" },
  { id: "export", label: "Export" },
  { id: "desks", label: "Desks" },
  { id: "spend", label: "Spend" },
  { id: "extract", label: "Extract" },
];

const EXPORTS = ["markdown", "csv", "json", "kev", "opml", "tether"] as const;

type Range = { preset: "all" | "24h" | "7d" | "30d" | "custom"; from: string; to: string };

const ALL_RANGE: Range = { preset: "all", from: "", to: "" };

function rangeQuery(range: Range): string {
  if (range.preset === "all") return "";
  const now = new Date();
  let from = "";
  let to = now.toISOString();
  if (range.preset === "24h") from = new Date(now.getTime() - 86_400_000).toISOString();
  else if (range.preset === "7d") from = new Date(now.getTime() - 7 * 86_400_000).toISOString();
  else if (range.preset === "30d") from = new Date(now.getTime() - 30 * 86_400_000).toISOString();
  else {
    from = range.from ? new Date(`${range.from}T00:00:00`).toISOString() : "";
    to = range.to ? new Date(`${range.to}T23:59:59`).toISOString() : "";
  }
  const parts: string[] = [];
  if (from) parts.push(`from=${encodeURIComponent(from)}`);
  if (to) parts.push(`to=${encodeURIComponent(to)}`);
  return parts.length ? `&${parts.join("&")}` : "";
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
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
      /* plain text */
    }
    throw new Error(message);
  }
  const type = response.headers.get("content-type") || "";
  if (type.includes("application/json") && !path.startsWith("/api/export")) {
    return response.json() as Promise<T>;
  }
  return (await response.text()) as T;
}

function slug(name: string) {
  const base = name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 32);
  return base || "desk";
}

function stateTone(state: string) {
  if (state === "pass" || state === "push") return "text-primary";
  if (state === "drop") return "text-muted-foreground";
  return "text-foreground";
}

function countPhrase(count: number, singular: string, plural = `${singular}s`) {
  return `${count} ${count === 1 ? singular : plural}`;
}

function actionLabel(action: string) {
  if (action === "push") return "Alert";
  if (action === "digest") return "Digest";
  if (action === "suppressed") return "Held";
  if (action === "drop") return "Set aside";
  return action;
}

export function DeskApp() {
  const [view, setView] = useState<View>("highway");
  const [deskId, setDeskId] = useState("weston");
  const [desks, setDesks] = useState<Desk[]>([]);
  const [matches, setMatches] = useState<Match[]>([]);
  const [indicators, setIndicators] = useState<Indicator[]>([]);
  const [digest, setDigest] = useState<Match[]>([]);
  const [status, setStatus] = useState<Status | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState("Opening your desk");
  const [error, setError] = useState("");
  const [range, setRange] = useState<Range>(ALL_RANGE);

  const desk = desks.find((item) => item.id === deskId) || desks[0];

  const refresh = useCallback(async (id: string, nextRange: Range = ALL_RANGE) => {
    const query = rangeQuery(nextRange);
    const [nextStatus, nextMatches, nextIndicators, nextDesks, nextDigest] = await Promise.all([
      api<Status>("/api/status"),
      api<Match[]>(`/api/matches?desk=${encodeURIComponent(id)}${query}`),
      api<Indicator[]>("/api/indicators"),
      api<Desk[]>("/api/desks"),
      api<Match[]>(`/api/digest?desk=${encodeURIComponent(id)}${query}`),
    ]);
    setStatus(nextStatus);
    setMatches(nextMatches);
    setIndicators(nextIndicators);
    setDesks(nextDesks);
    setDigest(nextDigest);
    setSelected((current) => {
      if (current && nextMatches.some((row) => row.id === current)) return current;
      return nextMatches.find((row) => row.action === "push")?.id || nextMatches[0]?.id || null;
    });
  }, []);

  useEffect(() => {
    const stored = window.localStorage.getItem("wt-desk");
    const initial = stored || "weston";
    setDeskId(initial);
    (async () => {
      try {
        const first = await api<Status>("/api/status");
        if ((first.counts?.matches || 0) === 0) {
          setBusy("Loading the sample");
          await api("/api/wire", { method: "POST", body: JSON.stringify({ mode: "demo" }) });
        }
        await refresh(initial);
        setBusy("");
      } catch (exc) {
        setError(exc instanceof Error ? exc.message : "The desk API is not up");
        setBusy("");
      }
    })();
  }, [refresh]);

  async function runWire(mode: "demo" | "live") {
    setBusy(mode === "demo" ? "Replaying the sample" : "Checking live sources");
    setError("");
    try {
      await api("/api/wire", { method: "POST", body: JSON.stringify({ mode }) });
      await refresh(deskId, range);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Wire failed");
    } finally {
      setBusy("");
    }
  }

  async function chooseDesk(id: string) {
    setDeskId(id);
    window.localStorage.setItem("wt-desk", id);
    setError("");
    try {
      await refresh(id, range);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Could not switch desks");
    }
  }

  function applyRange(next: Range) {
    setRange(next);
    refresh(desk?.id || deskId, next).catch((exc) => setError(exc instanceof Error ? exc.message : "Could not filter"));
  }

  const lanes = useMemo(() => {
    const grouped = new Map<string, Match[]>();
    for (const row of matches) {
      const list = grouped.get(row.indicator_id) || [];
      list.push(row);
      grouped.set(row.indicator_id, list);
    }
    return grouped;
  }, [matches]);

  const focus = matches.find((row) => row.id === selected) || null;
  const laneIds = desk?.indicators?.length ? desk.indicators : indicators.map((item) => item.id);

  return (
    <div className="min-h-screen bg-background font-sans text-foreground">
      <header className="border-b border-border px-5 py-5 md:px-8">
        <div className="flex flex-wrap items-end justify-between gap-6">
          <div>
            <h1 className="font-serif text-4xl leading-none tracking-tight text-foreground">Watch Tower</h1>
            <p className="mt-2 text-sm text-muted-foreground">What arrived, and why it is on this desk.</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="secondary" size="sm" onClick={() => runWire("demo")} disabled={!!busy}>
              Replay the sample
            </Button>
            <Button size="sm" onClick={() => runWire("live")} disabled={!!busy}>
              Check live sources
            </Button>
          </div>
        </div>
        <div className="mt-5 flex gap-2 overflow-x-auto">
          {desks.map((item) => (
            <button
              key={item.id}
              onClick={() => chooseDesk(item.id)}
              className={`shrink-0 rounded-lg border px-3 py-2 text-left text-sm ${
                item.id === (desk?.id || deskId)
                  ? "border-primary/70 bg-card text-foreground"
                  : "border-border text-muted-foreground"
              }`}
            >
              {item.name}
            </button>
          ))}
        </div>
      </header>

      <nav className="flex gap-1 overflow-x-auto border-b border-border px-3 md:px-5">
        {VIEWS.map((item) => (
          <button
            key={item.id}
            className={`px-3 py-3 text-sm ${
              view === item.id
                ? "border-b-2 border-primary text-foreground"
                : "border-b-2 border-transparent text-muted-foreground"
            }`}
            onClick={() => setView(item.id)}
          >
            {item.label}
            {item.id === "digest" && digest.length > 0 ? ` · ${digest.length}` : ""}
          </button>
        ))}
      </nav>

      <div className="flex flex-wrap items-center gap-2 border-b border-border px-5 py-3 md:px-8">
        <span className="text-xs text-muted-foreground">Published</span>
        {(["all", "24h", "7d", "30d"] as const).map((preset) => (
          <button
            key={preset}
            className={`rounded-md px-2.5 py-1 text-xs ${
              range.preset === preset ? "bg-primary text-primary-foreground" : "text-muted-foreground"
            }`}
            onClick={() => applyRange({ preset, from: "", to: "" })}
          >
            {preset === "all" ? "All" : preset}
          </button>
        ))}
        <label className="flex items-center gap-2 text-xs text-muted-foreground">
          From
          <input
            type="date"
            className="rounded-md border border-border bg-card px-2 py-1 text-xs text-foreground"
            value={range.from}
            onChange={(event) => applyRange({ preset: "custom", from: event.target.value, to: range.to })}
          />
        </label>
        <label className="flex items-center gap-2 text-xs text-muted-foreground">
          To
          <input
            type="date"
            className="rounded-md border border-border bg-card px-2 py-1 text-xs text-foreground"
            value={range.to}
            onChange={(event) => applyRange({ preset: "custom", from: range.from, to: event.target.value })}
          />
        </label>
        <span className="ml-auto text-xs text-muted-foreground">{busy || error || reportLine(status, desk)}</span>
      </div>

      <main className="px-0 py-0">
        {view === "highway" && (
          <Highway
            laneIds={laneIds}
            lanes={lanes}
            indicators={indicators}
            selected={selected}
            onSelect={(id) => {
              setSelected(id);
              setView("trace");
            }}
          />
        )}
        {view === "catalog" && (
          <CatalogView
            query={rangeQuery(range)}
            matches={matches}
            onOpen={(id) => {
              setSelected(id);
              setView("trace");
            }}
          />
        )}
        {view === "trace" && (
          <Trace match={focus} matches={matches} onSelect={setSelected} onLabeled={() => refresh(desk?.id || deskId, range)} />
        )}
        {view === "indicators" && <Indicators indicators={indicators} onSaved={() => refresh(desk?.id || deskId, range)} />}
        {view === "digest" && (
          <Digest
            rows={digest}
            desk={desk}
            onOpen={(id) => {
              setSelected(id);
              setView("trace");
            }}
            onRead={async () => {
              if (!desk) return;
              await api(`/api/desks/${desk.id}/digest-read`, { method: "POST" });
              await refresh(desk.id, range);
            }}
          />
        )}
        {view === "export" && <Exports desk={desk} query={rangeQuery(range)} />}
        {view === "desks" && (
          <Desks desks={desks} indicators={indicators} activeId={desk?.id} onSaved={chooseDesk} />
        )}
        {view === "spend" && <SpendView />}
        {view === "extract" && <ExtractDesk />}
      </main>
    </div>
  );
}

function reportLine(status: Status | null, desk: Desk | undefined) {
  const report = status?.last_report;
  const who = desk ? desk.name : "All lanes";
  if (!report) return who;
  const errors = report.errors?.length
    ? ` · ${countPhrase(report.errors.length, "source failed", "sources failed")}`
    : "";
  return `${who} · ${report.mode} · ${report.items_new} new · ${countPhrase(report.pushes, "alert")} · ${countPhrase(report.digests, "digest")}${errors}`;
}

function Highway({
  laneIds,
  lanes,
  indicators,
  selected,
  onSelect,
}: {
  laneIds: string[];
  lanes: Map<string, Match[]>;
  indicators: Indicator[];
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const names = new Map(indicators.map((item) => [item.id, item.name]));
  const ids = [...laneIds];
  for (const id of lanes.keys()) if (!ids.includes(id)) ids.push(id);
  if (ids.length === 0) {
    return <p className="px-8 py-10 text-sm text-muted-foreground">This desk has no lanes yet.</p>;
  }
  return (
    <div>
      {ids.map((id) => {
        const rows = (lanes.get(id) || []).filter((row) => row.action === "push" || row.action === "digest" || row.action === "suppressed");
        return (
          <section key={id} className="grid border-b border-border md:grid-cols-[220px_1fr]">
            <div className="border-b border-border px-5 py-5 md:border-b-0 md:border-r">
              <h2 className="font-serif text-lg leading-tight text-foreground">{names.get(id) || id}</h2>
              <p className="mt-2 text-xs text-muted-foreground">
                {countPhrase(rows.filter((row) => row.action === "push").length, "alert")} · {countPhrase(rows.filter((row) => row.action === "digest").length, "item for the digest", "items for the digest")}
              </p>
            </div>
            <div className="flex gap-3 overflow-x-auto px-5 py-5">
              {rows.length === 0 ? (
                <p className="self-center text-sm text-muted-foreground">Nothing in this lane.</p>
              ) : (
                rows.map((row) => (
                  <button
                    key={row.id}
                    onClick={() => onSelect(row.id)}
                    className={`w-72 shrink-0 rounded-lg border bg-card px-4 py-3 text-left ${
                      selected === row.id ? "border-primary" : "border-border"
                    } ${row.action === "suppressed" ? "opacity-60" : ""}`}
                  >
                    <div className="mb-2 flex items-center justify-between text-xs">
                      <span className={row.action === "push" ? "text-primary" : "text-muted-foreground"}>{actionLabel(row.action)}</span>
                      <span className="font-mono text-[11px] text-muted-foreground">{row.p_hit.toFixed(2)}</span>
                    </div>
                    <p className="line-clamp-3 text-sm leading-snug text-foreground">{row.title}</p>
                    <div className="mt-3 h-px bg-border">
                      <div className="h-px bg-primary" style={{ width: `${Math.round(Math.min(1, row.p_hit) * 100)}%` }} />
                    </div>
                  </button>
                ))
              )}
            </div>
          </section>
        );
      })}
    </div>
  );
}

function Trace({
  match,
  matches,
  onSelect,
  onLabeled,
}: {
  match: Match | null;
  matches: Match[];
  onSelect: (id: string) => void;
  onLabeled: () => Promise<void>;
}) {
  const [note, setNote] = useState("");
  const [labelError, setLabelError] = useState("");
  if (!match) return <p className="px-8 py-10 text-sm text-muted-foreground">Choose an item on the highway.</p>;
  return (
    <div className="grid lg:grid-cols-[240px_minmax(0,1fr)_minmax(0,1fr)]">
      <ul className="max-h-[78vh] overflow-auto border-b border-border lg:border-b-0 lg:border-r">
        {matches.map((row) => (
          <li key={row.id}>
            <button
              className={`block w-full border-b border-border px-4 py-3 text-left ${
                row.id === match.id ? "border-l-2 border-l-primary bg-card text-foreground" : "text-muted-foreground"
              }`}
              onClick={() => {
                setNote("");
                setLabelError("");
                onSelect(row.id);
              }}
            >
              <span className="text-xs">{actionLabel(row.action)}</span>
              <span className={`mt-1 block line-clamp-2 text-sm ${row.id === match.id ? "text-foreground" : "text-foreground/80"}`}>
                {row.title}
              </span>
            </button>
          </li>
        ))}
      </ul>
      <article className="border-b border-border px-6 py-6 lg:border-b-0 lg:border-r">
        <p className="text-xs text-primary">{actionLabel(match.action)}</p>
        <h2 className="mt-2 font-serif text-3xl leading-tight text-foreground">{match.title}</h2>
        {match.url ? (
          <a className="mt-3 block break-all text-xs text-primary underline" href={match.url}>
            {match.url}
          </a>
        ) : null}
        <p className="mt-4 text-sm leading-relaxed text-foreground/80">{match.text.slice(0, 900)}</p>
        {match.snippets.length > 0 && (
          <ul className="mt-5 space-y-3">
            {match.snippets.map((snippet) => (
              <li key={snippet} className="border-l border-primary pl-3 text-sm text-foreground">
                {snippet}
              </li>
            ))}
          </ul>
        )}
        <p className="mt-5 text-sm leading-relaxed text-muted-foreground">{match.explanation}</p>
        <div className="mt-5 flex gap-2">
          <Button
            size="sm"
            onClick={async () => {
              setLabelError("");
              try {
                await api(`/api/matches/${match.id}/label`, { method: "POST", body: JSON.stringify({ label: "hit" }) });
                setNote("Marked hit. It leaves with the Kev export.");
                await onLabeled();
              } catch (exc) {
                setLabelError(exc instanceof Error ? exc.message : "Label failed");
              }
            }}
          >
            Hit
          </Button>
          <Button
            size="sm"
            variant="secondary"
            onClick={async () => {
              setLabelError("");
              try {
                await api(`/api/matches/${match.id}/label`, { method: "POST", body: JSON.stringify({ label: "miss" }) });
                setNote("Marked miss.");
                await onLabeled();
              } catch (exc) {
                setLabelError(exc instanceof Error ? exc.message : "Label failed");
              }
            }}
          >
            Miss
          </Button>
        </div>
        {note && <p className="mt-3 text-sm text-muted-foreground">{note}</p>}
        {labelError && <p className="mt-3 text-sm text-primary">{labelError}</p>}
      </article>
      <ol>
        {match.trace.map((step) => (
          <li key={step.gate} className="border-b border-border px-5 py-4">
            <p className="text-sm">
              <span className={stateTone(step.state)}>{step.gate}</span>
              <span className="ml-3 text-muted-foreground">{step.state}</span>
            </p>
            <p className="mt-2 text-sm leading-relaxed text-foreground/85">{step.detail}</p>
          </li>
        ))}
      </ol>
    </div>
  );
}

function Indicators({ indicators, onSaved }: { indicators: Indicator[]; onSaved: () => Promise<void> }) {
  const [current, setCurrent] = useState(indicators[0]?.id || "");
  const indicator = indicators.find((item) => item.id === current) || indicators[0];
  const [form, setForm] = useState<Indicator | null>(indicator || null);
  const [keywords, setKeywords] = useState((indicator?.keywords_any || []).join(", "));
  const [message, setMessage] = useState("");
  useEffect(() => {
    setForm(indicator || null);
    setKeywords((indicator?.keywords_any || []).join(", "));
  }, [indicator]);
  if (!form) return <p className="px-8 py-10 text-sm text-muted-foreground">No indicators loaded.</p>;
  return (
    <div className="grid lg:grid-cols-[240px_1fr]">
      <ul className="border-b border-border lg:border-b-0 lg:border-r">
        {indicators.map((item) => (
          <li key={item.id}>
            <button
              className={`block w-full border-b border-border px-4 py-3 text-left text-sm ${
                item.id === form.id ? "border-l-2 border-l-primary bg-card text-foreground" : "text-muted-foreground"
              }`}
              onClick={() => setCurrent(item.id)}
            >
              {item.name}
            </button>
          </li>
        ))}
      </ul>
      <form
        className="space-y-4 px-5 py-5"
        onSubmit={async (event) => {
          event.preventDefault();
          setMessage("");
          try {
            await api(`/api/indicators/${form.id}`, {
              method: "PUT",
              body: JSON.stringify({
                name: form.name,
                description: form.description,
                keywords_any: keywords
                  .split(",")
                  .map((term) => term.trim())
                  .filter(Boolean),
                min_cosine: Number(form.min_cosine),
                alert_if: form.alert_if,
                digest_if: form.digest_if,
                cooldown: form.cooldown,
                push_min_confidence: Number(form.push_min_confidence),
                uncertain_min: Number(form.uncertain_min),
              }),
            });
            setMessage("Saved. The next pull uses this indicator.");
            await onSaved();
          } catch (exc) {
            setMessage(exc instanceof Error ? exc.message : "Save failed");
          }
        }}
      >
        <p className="font-mono text-xs text-muted-foreground">{form.id}</p>
        <Input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} />
        <Textarea value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} />
        <Field label="Keywords, comma separated" value={keywords} onChange={setKeywords} />
        <div className="grid gap-3 md:grid-cols-2">
          <Field label="Minimum similarity" value={String(form.min_cosine)} onChange={(value) => setForm({ ...form, min_cosine: Number(value) })} />
          <Field label="Cooldown" value={form.cooldown} onChange={(value) => setForm({ ...form, cooldown: value })} />
          <Field label="Confidence to alert" value={String(form.push_min_confidence)} onChange={(value) => setForm({ ...form, push_min_confidence: Number(value) })} />
          <Field label="Uncertain floor" value={String(form.uncertain_min)} onChange={(value) => setForm({ ...form, uncertain_min: Number(value) })} />
        </div>
        <Field label="Alert when" value={form.alert_if} onChange={(value) => setForm({ ...form, alert_if: value })} />
        <Field label="Digest when" value={form.digest_if || ""} onChange={(value) => setForm({ ...form, digest_if: value })} />
        <p className="text-xs text-muted-foreground">Sources {form.sources.join(", ") || "any"}</p>
        <Button type="submit" size="sm">
          Save indicator
        </Button>
        {message && <p className="text-sm text-muted-foreground">{message}</p>}
      </form>
    </div>
  );
}

function Field({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <label className="block text-sm text-muted-foreground">
      {label}
      <Input className="mt-2" value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function Digest({
  rows,
  desk,
  onOpen,
  onRead,
}: {
  rows: Match[];
  desk: Desk | undefined;
  onOpen: (id: string) => void;
  onRead: () => Promise<void>;
}) {
  return (
    <section className="px-5 py-5">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="font-serif text-3xl text-foreground">Unread</h2>
          <p className="mt-2 max-w-xl text-sm text-muted-foreground">{desk ? desk.brief : "Every lane."}</p>
        </div>
        <Button size="sm" variant="secondary" onClick={() => onRead()} disabled={rows.length === 0}>
          Mark this desk read
        </Button>
      </div>
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">Nothing waiting. Alerts stay on the highway.</p>
      ) : (
        <ul className="border-t border-border">
          {rows.map((row) => (
            <li key={row.id} className="border-b border-border">
              <button className="block w-full px-1 py-4 text-left" onClick={() => onOpen(row.id)}>
                <span className="text-xs text-muted-foreground">{row.indicator_id}</span>
                <span className="mt-1 block text-foreground">{row.title}</span>
                <span className="mt-1 block text-sm text-muted-foreground">{row.reason}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Exports({ desk, query }: { desk: Desk | undefined; query: string }) {
  const [preview, setPreview] = useState("");
  const [format, setFormat] = useState(desk?.exports?.[0] || "markdown");
  const [error, setError] = useState("");
  const formats = [...(desk?.exports?.length ? desk.exports : ["markdown", "csv", "json", "kev"])];
  for (const extra of ["opml", "tether"]) if (!formats.includes(extra)) formats.push(extra);
  async function load(kind: string) {
    setFormat(kind);
    setError("");
    try {
      const text = await api<string>(`/api/export?desk=${encodeURIComponent(desk?.id || "")}&format=${kind}${query}`);
      setPreview(text.slice(0, 5000));
    } catch (exc) {
      setPreview("");
      setError(exc instanceof Error ? exc.message : "Export failed");
    }
  }
  return (
    <section className="px-5 py-5">
      <h2 className="font-serif text-3xl text-foreground">Export</h2>
      <p className="mt-3 max-w-2xl text-sm leading-relaxed text-muted-foreground">
        {desk?.name || "This desk"} only receives its own lanes, and the published-date window above is applied.
        Markdown is the briefing. CSV and JSON are the match packet. OPML is the source list. Tether is the
        source-to-decision packet: each configured source and the calls it produced. Kev JSONL still needs hit and miss labels.
      </p>
      <div className="mt-5 flex flex-wrap gap-2">
        {formats.map((kind) => (
          <Button key={kind} size="sm" variant={format === kind ? "default" : "secondary"} onClick={() => load(kind)}>
            {kind}
          </Button>
        ))}
        <a
          className="inline-flex h-8 items-center rounded-lg border border-border px-3 text-sm text-foreground"
          href={`/api/export?desk=${encodeURIComponent(desk?.id || "")}&format=${format}${query}`}
        >
          Download {format}
        </a>
      </div>
      {error && <p className="mt-4 text-sm text-primary">{error}</p>}
      {preview && (
        <pre className="mt-5 max-h-[60vh] overflow-auto rounded-lg border border-border bg-card p-4 font-mono text-xs leading-relaxed text-foreground/85">
          {preview}
        </pre>
      )}
    </section>
  );
}

function Desks({
  desks,
  indicators,
  activeId,
  onSaved,
}: {
  desks: Desk[];
  indicators: Indicator[];
  activeId?: string;
  onSaved: (id: string) => Promise<void>;
}) {
  const [current, setCurrent] = useState(activeId || desks[0]?.id || "");
  const desk = desks.find((item) => item.id === current) || desks[0];
  const [form, setForm] = useState<Desk | null>(desk || null);
  const [message, setMessage] = useState("");
  const [draftName, setDraftName] = useState("");
  useEffect(() => setForm(desk || null), [desk]);
  async function createDesk() {
    const name = draftName.trim();
    if (!name) return;
    let id = slug(name);
    let n = 2;
    while (desks.some((item) => item.id === id)) {
      id = `${slug(name)}-${n++}`;
    }
    const created: Desk = {
      id,
      name,
      brief: "",
      indicators: [],
      exports: [...EXPORTS],
      digest_seen_at: "",
    };
    await api(`/api/desks/${id}`, { method: "PUT", body: JSON.stringify(created) });
    setDraftName("");
    setMessage(`Opened ${name}. It follows every lane until you narrow it.`);
    await onSaved(id);
    setCurrent(id);
  }
  if (!form) return null;
  return (
    <div className="grid lg:grid-cols-[240px_1fr]">
      <div className="border-b border-border lg:border-b-0 lg:border-r">
        <ul>
          {desks.map((item) => (
            <li key={item.id}>
              <button
                className={`block w-full border-b border-border px-4 py-3 text-left text-sm ${
                  item.id === form.id ? "border-l-2 border-l-primary bg-card text-foreground" : "text-muted-foreground"
                }`}
                onClick={() => setCurrent(item.id)}
              >
                {item.name}
              </button>
            </li>
          ))}
        </ul>
        <form
          className="space-y-2 px-4 py-4"
          onSubmit={(event) => {
            event.preventDefault();
            createDesk().catch((exc) => setMessage(exc instanceof Error ? exc.message : "Could not open a desk"));
          }}
        >
          <p className="text-sm text-muted-foreground">New desk</p>
          <Input value={draftName} placeholder="Name" onChange={(event) => setDraftName(event.target.value)} />
          <Button type="submit" size="sm" variant="secondary">
            Open desk
          </Button>
        </form>
      </div>
      <form
        className="space-y-4 px-5 py-5"
        onSubmit={async (event) => {
          event.preventDefault();
          try {
            await api(`/api/desks/${form.id}`, { method: "PUT", body: JSON.stringify(form) });
            setMessage("Saved on this machine. The seed file is unchanged.");
            await onSaved(form.id);
          } catch (exc) {
            setMessage(exc instanceof Error ? exc.message : "Save failed");
          }
        }}
      >
        <p className="font-mono text-xs text-muted-foreground">{form.id}</p>
        <Input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} />
        <Textarea
          value={form.brief}
          placeholder="What this person watches"
          onChange={(event) => setForm({ ...form, brief: event.target.value })}
        />
        <fieldset>
          <legend className="text-sm text-foreground">Lanes</legend>
          <p className="mb-3 mt-2 text-sm text-muted-foreground">Leave every box clear to follow the whole desk.</p>
          <div className="grid gap-2 md:grid-cols-2">
            {indicators.map((indicator) => {
              const on = form.indicators.includes(indicator.id);
              return (
                <label key={indicator.id} className="flex items-center gap-2 text-sm text-foreground">
                  <input
                    type="checkbox"
                    checked={on}
                    onChange={() =>
                      setForm({
                        ...form,
                        indicators: on ? form.indicators.filter((id) => id !== indicator.id) : [...form.indicators, indicator.id],
                      })
                    }
                  />
                  {indicator.name}
                </label>
              );
            })}
          </div>
        </fieldset>
        <fieldset>
          <legend className="text-sm text-foreground">Exports</legend>
          <div className="mt-3 flex flex-wrap gap-4">
            {EXPORTS.map((kind) => (
              <label key={kind} className="flex items-center gap-2 text-sm text-foreground">
                <input
                  type="checkbox"
                  checked={form.exports.includes(kind)}
                  onChange={() =>
                    setForm({
                      ...form,
                      exports: form.exports.includes(kind)
                        ? form.exports.filter((item) => item !== kind)
                        : [...form.exports, kind],
                    })
                  }
                />
                {kind}
              </label>
            ))}
          </div>
        </fieldset>
        <Button type="submit" size="sm">
          Save desk
        </Button>
        {message && <p className="text-sm text-muted-foreground">{message}</p>}
      </form>
    </div>
  );
}
