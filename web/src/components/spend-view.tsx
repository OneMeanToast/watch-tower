"use client";

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

type Cost = {
  usd_per_mtok: number;
  monthly_usd: number;
  daily_warn_usd: number;
  today_usd: number;
  month_usd: number;
  projected_usd: number;
  today_tokens: number;
  month_tokens: number;
  last_run_tokens: number;
  last_run_usd: number;
  cached_hits: number;
  over_budget: boolean;
  daily_warning: boolean;
  output_billed: boolean;
  fallback: string;
  note: string;
};

async function readCost(): Promise<Cost> {
  const response = await fetch("/api/cost");
  if (!response.ok) {
    throw new Error((await response.text()) || response.statusText);
  }
  return response.json() as Promise<Cost>;
}

function money(value: number) {
  return value.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 4,
    maximumFractionDigits: 4,
  });
}

export function SpendView() {
  const [cost, setCost] = useState<Cost | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("Reading the meter");
  const [rate, setRate] = useState("");
  const [cap, setCap] = useState("");
  const [warn, setWarn] = useState("");

  const load = useCallback(async () => {
    setBusy("Reading the meter");
    setError("");
    try {
      const next = await readCost();
      setCost(next);
      setRate(String(next.usd_per_mtok));
      setCap(String(next.monthly_usd));
      setWarn(String(next.daily_warn_usd));
      setBusy("");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "The meter did not answer");
      setBusy("");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function save() {
    const usd = Number(rate);
    const monthly = Number(cap);
    const daily = Number(warn);
    if (!Number.isFinite(usd) || usd < 0 || !Number.isFinite(monthly) || monthly < 0 || !Number.isFinite(daily) || daily < 0) {
      setError("Price, cap, and warning must be zero or a positive number.");
      return;
    }
    setBusy("Saving the cap");
    setError("");
    try {
      const response = await fetch("/api/cost", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ usd_per_mtok: usd, monthly_usd: monthly, daily_warn_usd: daily }),
      });
      if (!response.ok) {
        throw new Error((await response.text()) || response.statusText);
      }
      const next = (await response.json()) as Cost;
      setCost(next);
      setBusy("");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "The cap was not saved");
      setBusy("");
    }
  }

  if (!cost && !error) {
    return <p className="px-6 py-8 text-sm text-white/50">{busy || "Reading the meter"}</p>;
  }
  if (!cost && error) {
    return (
      <div className="px-6 py-8">
        <p className="text-sm text-white">{error}</p>
        <Button className="mt-4 uppercase tracking-widest" size="sm" onClick={() => void load()}>
          Try again
        </Button>
      </div>
    );
  }
  if (!cost) return null;
  const empty = cost.month_tokens === 0 && cost.today_tokens === 0 && cost.cached_hits === 0;
  return (
    <section className="grid gap-6 px-4 py-6 md:grid-cols-[minmax(0,1fr)_20rem] md:px-6">
      <div>
        <p className="text-[10px] tracking-[0.28em] uppercase text-white/40">Jev input tokens</p>
        <h2 className="mt-2 text-sm tracking-[0.18em] uppercase">Spend</h2>
        <div className="mt-6 grid gap-px bg-white/15 sm:grid-cols-3">
          <Meter label="Today" value={money(cost.today_usd)} detail={`${cost.today_tokens.toLocaleString()} input tokens`} />
          <Meter label="This month" value={money(cost.month_usd)} detail={`${cost.month_tokens.toLocaleString()} input tokens`} />
          <Meter label="Projected" value={money(cost.projected_usd)} detail="Straight line through the UTC month" />
        </div>
        <dl className="mt-6 space-y-2 text-sm">
          <Row label="Last run" value={`${cost.last_run_tokens.toLocaleString()} input tokens · ${money(cost.last_run_usd)}`} />
          <Row label="Cached hits this month" value={String(cost.cached_hits)} />
          <Row label="Output tokens" value={cost.output_billed ? "billed" : "not billed"} />
          <Row label="Rate" value={`${money(cost.usd_per_mtok)} per million input tokens`} />
          <Row label="Monthly cap" value={cost.monthly_usd > 0 ? money(cost.monthly_usd) : "no cap"} />
          <Row label="Daily warning" value={money(cost.daily_warn_usd)} />
        </dl>
        {empty ? (
          <p className="mt-6 max-w-xl text-sm text-white/55">
            No Jev calls in this desk database yet. A live pull with JEV_API_KEY set writes spend here.
            Replay fixtures stays on the lexical decider and costs nothing. The command line writes its
            own database, so a <span className="text-white">wt run</span> does not show up here unless
            WT_DB points at the desk file.
          </p>
        ) : null}
        {cost.daily_warning ? (
          <p className="mt-4 text-sm text-primary">
            Today is at or over the {money(cost.daily_warn_usd)} warning. Calls continue until the monthly cap.
          </p>
        ) : null}
        {cost.over_budget ? (
          <p className="mt-4 text-sm text-primary">The monthly cap is spent. New items stay on local Kev or the lexical decider.</p>
        ) : null}
        <p className="mt-4 max-w-xl text-sm text-white/55">{cost.fallback}</p>
        <p className="mt-2 max-w-xl text-xs text-white/35">{cost.note}</p>
        {error ? <p className="mt-4 text-sm text-white">{error}</p> : null}
        {busy ? <p className="mt-4 text-xs tracking-widest uppercase text-white/40">{busy}</p> : null}
      </div>
      <form
        className="border border-white/15 p-4"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <p className="text-[10px] tracking-[0.22em] uppercase text-white/40">Editable</p>
        <label className="mt-4 block text-[10px] tracking-[0.18em] uppercase text-white/45">
          Dollars per million input tokens
          <Input className="mt-1" value={rate} onChange={(event) => setRate(event.target.value)} inputMode="decimal" />
        </label>
        <label className="mt-4 block text-[10px] tracking-[0.18em] uppercase text-white/45">
          Monthly cap (0 means no cap)
          <Input className="mt-1" value={cap} onChange={(event) => setCap(event.target.value)} inputMode="decimal" />
        </label>
        <label className="mt-4 block text-[10px] tracking-[0.18em] uppercase text-white/45">
          Daily warning
          <Input className="mt-1" value={warn} onChange={(event) => setWarn(event.target.value)} inputMode="decimal" />
        </label>
        <Button className="mt-4 uppercase tracking-widest" size="sm" type="submit" disabled={!!busy}>
          Save
        </Button>
      </form>
    </section>
  );
}

function Meter({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="bg-black px-4 py-4">
      <p className="text-[10px] tracking-[0.22em] uppercase text-white/40">{label}</p>
      <p className="mt-2 font-mono text-lg text-primary">{value}</p>
      <p className="mt-1 text-xs text-white/45">{detail}</p>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-wrap justify-between gap-2 border-b border-white/10 py-2">
      <dt className="text-[10px] tracking-[0.18em] uppercase text-white/40">{label}</dt>
      <dd className="font-mono text-sm">{value}</dd>
    </div>
  );
}
