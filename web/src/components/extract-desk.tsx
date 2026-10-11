"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";

type Mention = {
  id: string;
  start: number;
  end: number;
  surface: string;
  type: string;
  entity_id: string | null;
  status: string;
  p: number | null;
};

type ReviewRow = {
  kind: string;
  id: string;
  label: string;
  type: string;
  status: string;
  p: number | null;
  modality?: string;
  left?: string;
  right?: string;
  entity_id?: string;
};

type Entity = { id: string; schema: string; caption: string; wikidata_qid?: string | null };

type Graph = {
  nodes: { id: string; schema: string; caption: string }[];
  edges: { id: string; source: string; target: string; type: string; modality: string }[];
};

type Board = {
  document: { id: string; title: string; text: string; kind: string; starred: boolean } | null;
  documents: { id: string; title: string; kind: string }[];
  mentions: Mention[];
  review: ReviewRow[];
  entities: Entity[];
  graph: Graph;
  types: string[];
  jobs_today: number;
  daily_cap: number;
};

type Profile = {
  id: string;
  schema: string;
  caption: string;
  aliases: string[];
  mentions: { id: string; title: string; surface: string; context: string; type: string; p: number | null }[];
  relations: { id: string; type: string; modality: string; head: string; tail: string }[];
  timeline: { id: string; type: string; cameo: string; trigger: string; when: string; roles: { role: string; caption: string }[] }[];
};

const COLORS: Record<string, string> = {
  Vulnerability: "#ff5a36",
  SoftwareProduct: "#7eb6ff",
  Organization: "#e7ff4a",
  Company: "#d6ff6a",
  PublicBody: "#ffd27a",
  AIModel: "#c9a6ff",
  Date: "#9ad7c2",
  Quantity: "#f2c14e",
  Regulation: "#ff9ec8",
  Person: "#f0b429",
};

function colorFor(type: string) {
  return COLORS[type] || "#d8d2c8";
}

async function readJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers || {}) },
  });
  if (!response.ok) {
    throw new Error((await response.text()) || response.statusText);
  }
  return response.json() as Promise<T>;
}

function Markup({ text, mentions, onPick }: { text: string; mentions: Mention[]; onPick: (id: string) => void }) {
  const pieces = useMemo(() => {
    const visible = mentions
      .filter((row) => row.status !== "rejected" && text.slice(row.start, row.end) === row.surface)
      .sort((a, b) => a.start - b.start || b.end - a.end);
    const nodes: { key: string; text: string; mention?: Mention }[] = [];
    let cursor = 0;
    for (const mention of visible) {
      if (mention.start < cursor) continue;
      if (mention.start > cursor) nodes.push({ key: `t-${cursor}`, text: text.slice(cursor, mention.start) });
      nodes.push({ key: mention.id, text: text.slice(mention.start, mention.end), mention });
      cursor = mention.end;
    }
    if (cursor < text.length) nodes.push({ key: `t-${cursor}`, text: text.slice(cursor) });
    return nodes;
  }, [text, mentions]);

  return (
    <p className="whitespace-pre-wrap text-sm leading-7 text-white/90">
      {pieces.map((piece) =>
        piece.mention ? (
          <mark
            key={piece.key}
            title={`${piece.mention.type} · ${piece.mention.status}`}
            onClick={() => piece.mention?.entity_id && onPick(piece.mention.entity_id)}
            className="cursor-pointer rounded px-0.5 text-black"
            style={{ background: colorFor(piece.mention.type) }}
          >
            {piece.text}
          </mark>
        ) : (
          <span key={piece.key}>{piece.text}</span>
        ),
      )}
    </p>
  );
}

function canvasInkCount(canvas: HTMLCanvasElement) {
  const gl = canvas.getContext("webgl2") || canvas.getContext("webgl");
  if (!gl) return 0;
  const pixel = new Uint8Array(4);
  let ink = 0;
  for (let y = 8; y < canvas.height; y += 24) {
    for (let x = 8; x < canvas.width; x += 24) {
      gl.readPixels(x, y, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel);
      if (pixel[3] > 16 && pixel[0] + pixel[1] + pixel[2] > 24) ink += 1;
    }
  }
  return ink;
}

function LayoutChart({ graph, onPick }: { graph: Graph; onPick: (id: string) => void }) {
  const placed = graph.nodes.map((node, index) => {
    const angle = (Math.PI * 2 * index) / Math.max(graph.nodes.length, 1);
    return { ...node, x: Math.cos(angle), y: Math.sin(angle) };
  });
  const at = Object.fromEntries(placed.map((node) => [node.id, node]));
  return (
    <svg viewBox="-1.6 -1.55 3.2 3.1" className="h-80 w-full bg-black" role="img" aria-label="Entity link chart">
      {graph.edges.map((edge) => {
        const source = at[edge.source];
        const target = at[edge.target];
        if (!source || !target) return null;
        return (
          <line
            key={edge.id}
            x1={source.x}
            y1={source.y}
            x2={target.x}
            y2={target.y}
            stroke={edge.modality === "speculative" ? "#8a8478" : "#e7ff4a"}
            strokeWidth={edge.modality === "speculative" ? 0.008 : 0.014}
          />
        );
      })}
      {placed.map((node) => (
        <g key={node.id} onClick={() => onPick(node.id)} className="cursor-pointer">
          <circle cx={node.x} cy={node.y} r={0.07} fill={colorFor(node.schema)} />
          <text x={node.x + 0.09} y={node.y + 0.03} fill="#f4f1ea" fontSize="0.09">
            {node.caption}
          </text>
        </g>
      ))}
    </svg>
  );
}

function LinkChart({ graph, onPick }: { graph: Graph; onPick: (id: string) => void }) {
  const [note, setNote] = useState("Drawing the chart");
  const [glChart, setGlChart] = useState(true);

  useEffect(() => {
    const container = document.getElementById("extract-sigma");
    if (!container) return;
    let sigma: { kill: () => void } | null = null;
    let cancelled = false;
    container.replaceChildren();
    (async () => {
      try {
        const [{ default: Graph }, { default: Sigma }] = await Promise.all([
          import("graphology"),
          import("sigma"),
        ]);
        if (cancelled) return;
        const model = new Graph();
        graph.nodes.forEach((node, index) => {
          const angle = (Math.PI * 2 * index) / Math.max(graph.nodes.length, 1);
          model.addNode(node.id, {
            label: node.caption,
            x: Math.cos(angle),
            y: Math.sin(angle),
            size: 12,
            color: colorFor(node.schema),
            labelColor: "#f4f1ea",
            forceLabel: true,
          });
        });
        graph.edges.forEach((edge) => {
          if (!model.hasNode(edge.source) || !model.hasNode(edge.target)) return;
          if (model.hasEdge(edge.source, edge.target)) return;
          model.addEdge(edge.source, edge.target, {
            label: edge.type,
            size: edge.modality === "speculative" ? 0.6 : 1.4,
            color: edge.modality === "speculative" ? "#8a8478" : "#e7ff4a",
          });
        });
        sigma = new Sigma(model, container, {
          allowInvalidContainer: true,
          itemSizesReference: "screen",
          stagePadding: 24,
        });
        sigma.on("clickNode", ({ node }) => onPick(node));
        window.setTimeout(() => {
          const canvas = container.querySelector("canvas");
          const drawn = canvas instanceof HTMLCanvasElement && canvasInkCount(canvas) >= 8;
          setGlChart(drawn);
          setNote(
            `${graph.nodes.length} entities · ${graph.edges.length} links` +
              (drawn ? "" : " · layout"),
          );
        }, 350);
      } catch (exc) {
        setNote(exc instanceof Error ? exc.message : "The chart did not draw");
      }
    })();
    return () => {
      cancelled = true;
      sigma?.kill();
    };
  }, [graph, onPick]);

  return (
    <div>
      <div className="relative h-80 bg-black">
        <div className={glChart ? "hidden" : "absolute inset-0"}>
          <LayoutChart graph={graph} onPick={onPick} />
        </div>
        <div id="extract-sigma" className={glChart ? "absolute inset-0" : "pointer-events-none absolute inset-0 opacity-0"} />
      </div>
      <p className="mt-2 text-[11px] tracking-widest text-white/45 uppercase">{note}</p>
    </div>
  );
}

function Neighbourhood({ graph, focus }: { graph: Graph; focus: string }) {
  const ego = useMemo(() => {
    const keep = new Set<string>([focus]);
    for (const edge of graph.edges) {
      if (edge.source === focus) keep.add(edge.target);
      if (edge.target === focus) keep.add(edge.source);
    }
    return {
      nodes: graph.nodes.filter((node) => keep.has(node.id)),
      edges: graph.edges.filter((edge) => keep.has(edge.source) && keep.has(edge.target)),
    };
  }, [graph, focus]);

  useEffect(() => {
    if (ego.nodes.length === 0 || ego.nodes.length > 12) return;
    const container = document.getElementById("extract-cy");
    if (!container) return;
    let cy: { destroy: () => void } | null = null;
    let cancelled = false;
    (async () => {
      const loaded = await import("cytoscape");
      const cytoscape = loaded.default ?? loaded;
      if (cancelled || !container) return;
      cy = cytoscape({
        container,
        elements: [
          ...ego.nodes.map((node) => ({ data: { id: node.id, label: node.caption } })),
          ...ego.edges.map((edge) => ({ data: { id: edge.id, source: edge.source, target: edge.target, label: edge.type } })),
        ],
        style: [
          {
            selector: "node",
            style: {
              label: "data(label)",
              color: "#f4f1ea",
              "background-color": "#e7ff4a",
              "font-size": 10,
              "text-valign": "bottom",
              "text-margin-y": 6,
            },
          },
          {
            selector: "edge",
            style: { label: "data(label)", width: 1, "line-color": "#e7ff4a", "font-size": 8, color: "#f4f1ea", "curve-style": "bezier" },
          },
        ],
        layout: { name: "cose", animate: false },
      });
    })();
    return () => {
      cancelled = true;
      cy?.destroy();
    };
  }, [ego]);

  if (ego.nodes.length > 12) return null;
  return (
    <div>
      <p className="mb-2 text-[11px] tracking-[0.22em] text-white/45 uppercase">Neighbourhood</p>
      <div id="extract-cy" className="h-56 w-full border border-white/10" />
    </div>
  );
}

export function ExtractDesk() {
  const [board, setBoard] = useState<Board | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("Loading the fixture");
  const [editType, setEditType] = useState<Record<string, string>>({});

  const load = useCallback(async (doc?: string) => {
    setBusy("Loading the fixture");
    setError("");
    try {
      const next = await readJson<Board>(doc ? `/api/extract/board?doc=${encodeURIComponent(doc)}` : "/api/extract/board");
      setBoard(next);
      setBusy("");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "The extraction board did not answer");
      setBusy("");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const openEntity = useCallback(async (id: string) => {
    setError("");
    try {
      setProfile(await readJson<Profile>(`/api/extract/entities/${encodeURIComponent(id)}`));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "That entity is not in the store");
    }
  }, []);

  async function act(body: Record<string, string>) {
    setBusy("Saving the label");
    setError("");
    try {
      await readJson("/api/extract/act", { method: "POST", body: JSON.stringify(body) });
      await load(board?.document?.id);
      if (profile) await openEntity(profile.id);
      setBusy("");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "The label was not stored");
      setBusy("");
    }
  }

  async function star() {
    if (!board?.document) return;
    setBusy("Starring");
    try {
      await readJson(`/api/extract/documents/${board.document.id}/star`, { method: "POST" });
      await load(board.document.id);
      setBusy("");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Star failed");
      setBusy("");
    }
  }

  const legend = useMemo(() => {
    const types = new Set((board?.mentions || []).map((row) => row.type));
    return [...types];
  }, [board]);

  return (
    <section className="px-4 py-6 md:px-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-[11px] tracking-[0.32em] text-primary uppercase">Forensic extract</p>
          <h2 className="mt-2 text-2xl text-white">Spans, judgments, link chart</h2>
        </div>
        <p className="text-[11px] tracking-widest text-white/45 uppercase">
          {board ? `${board.jobs_today} queued today · cap ${board.daily_cap}` : busy || "Idle"}
        </p>
      </div>
      {error && <p className="mt-4 border border-red-400/40 px-3 py-2 text-sm text-red-200">{error}</p>}
      {!board && !error && <p className="mt-6 text-sm text-white/60">{busy || "Loading the fixture"}</p>}
      {board && !board.document && <p className="mt-6 text-sm text-white/60">No document has been extracted yet.</p>}
      {board?.document && (
        <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1.4fr)_minmax(18rem,0.8fr)]">
          <div className="space-y-6">
            <article className="border border-white/15 p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p className="text-[10px] tracking-[0.28em] text-white/40 uppercase">{board.document.kind || "document"}</p>
                  <h3 className="mt-1 text-lg text-white">{board.document.title}</h3>
                </div>
                <Button size="sm" variant="secondary" className="uppercase tracking-widest" onClick={() => void star()} disabled={!!busy}>
                  {board.document.starred ? "Starred" : "Star"}
                </Button>
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                {legend.map((type) => (
                  <span key={type} className="px-2 py-1 text-[10px] tracking-widest text-black uppercase" style={{ background: colorFor(type) }}>
                    {type}
                  </span>
                ))}
              </div>
              <div className="mt-4">
                <Markup text={board.document.text} mentions={board.mentions} onPick={(id) => void openEntity(id)} />
              </div>
            </article>
            <article className="border border-white/15 p-4">
              <h3 className="text-[11px] tracking-[0.28em] text-white/50 uppercase">Link chart</h3>
              {board.graph.nodes.length === 0 ? (
                <p className="mt-3 text-sm text-white/60">No entities to draw.</p>
              ) : (
                <div className="mt-3">
                  <LinkChart graph={board.graph} onPick={(id) => void openEntity(id)} />
                </div>
              )}
            </article>
            {profile && <Neighbourhood graph={board.graph} focus={profile.id} />}
          </div>
          <div className="space-y-6">
            <article className="border border-white/15 p-4">
              <h3 className="text-[11px] tracking-[0.28em] text-white/50 uppercase">Review, least certain first</h3>
              {board.review.length === 0 && <p className="mt-3 text-sm text-white/60">Nothing is waiting.</p>}
              <ul className="mt-3 space-y-3">
                {board.review.map((row) => (
                  <li key={`${row.kind}-${row.id}`} className="border border-white/10 p-3">
                    <p className="text-sm text-white">{row.label}</p>
                    <p className="mt-1 text-[10px] tracking-widest text-white/40 uppercase">
                      {row.kind} · {row.type}
                      {row.modality ? ` · ${row.modality}` : ""} · p {row.p == null ? "—" : row.p.toFixed(2)}
                    </p>
                    <div className="mt-2 flex flex-wrap gap-2">
                      {row.kind === "judgment" ? (
                        <>
                          <Button size="sm" onClick={() => void act({ action: "accept", kind: "judgment", id: row.id })}>
                            Merge
                          </Button>
                          <Button size="sm" variant="secondary" onClick={() => void act({ action: "reject", kind: "judgment", id: row.id })}>
                            Not the same
                          </Button>
                        </>
                      ) : (
                        <>
                          <Button size="sm" onClick={() => void act({ action: "accept", kind: row.kind, id: row.id })}>
                            Accept
                          </Button>
                          <Button size="sm" variant="secondary" onClick={() => void act({ action: "reject", kind: row.kind, id: row.id })}>
                            Reject
                          </Button>
                          {row.kind === "mention" && (
                            <>
                              <select
                                className="border border-white/20 bg-black px-2 text-xs text-white"
                                value={editType[row.id] || row.type}
                                onChange={(event) => setEditType((prev) => ({ ...prev, [row.id]: event.target.value }))}
                              >
                                {board.types.map((type) => (
                                  <option key={type} value={type}>
                                    {type}
                                  </option>
                                ))}
                              </select>
                              <Button
                                size="sm"
                                variant="secondary"
                                onClick={() => void act({ action: "edit", kind: "mention", id: row.id, type: editType[row.id] || row.type })}
                              >
                                Edit type
                              </Button>
                            </>
                          )}
                        </>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            </article>
            {profile ? (
              <article className="border border-white/15 p-4">
                <p className="text-[10px] tracking-[0.28em] text-primary uppercase">{profile.schema}</p>
                <h3 className="mt-1 text-xl text-white">{profile.caption}</h3>
                <p className="mt-3 text-[11px] tracking-widest text-white/40 uppercase">Aliases</p>
                <p className="mt-1 text-sm text-white/80">{profile.aliases.length ? profile.aliases.join(" · ") : "No aliases yet."}</p>
                <p className="mt-4 text-[11px] tracking-widest text-white/40 uppercase">Mentions</p>
                <ul className="mt-2 space-y-2">
                  {profile.mentions.map((mention) => (
                    <li key={mention.id} className="text-sm text-white/80">
                      <span className="text-white">{mention.surface}</span>
                      <span className="block text-white/50">{mention.context}</span>
                    </li>
                  ))}
                  {profile.mentions.length === 0 && <li className="text-sm text-white/50">No mentions in context.</li>}
                </ul>
                <p className="mt-4 text-[11px] tracking-widest text-white/40 uppercase">Relations</p>
                <ul className="mt-2 space-y-1 text-sm text-white/80">
                  {profile.relations.map((relation) => (
                    <li key={relation.id}>
                      {relation.head} — {relation.type} — {relation.tail}
                      <span className="text-white/40"> ({relation.modality})</span>
                    </li>
                  ))}
                  {profile.relations.length === 0 && <li className="text-white/50">No relations.</li>}
                </ul>
                <p className="mt-4 text-[11px] tracking-widest text-white/40 uppercase">Timeline</p>
                <ul className="mt-2 space-y-2 text-sm text-white/80">
                  {profile.timeline.map((event) => (
                    <li key={event.id}>
                      <span className="text-white/40">{event.when || "undated"}</span> {event.type}
                      {event.cameo ? ` · CAMEO ${event.cameo}` : ""} · {event.trigger}
                      <span className="block text-white/50">
                        {event.roles.map((role) => `${role.role}: ${role.caption}`).join(" · ")}
                      </span>
                    </li>
                  ))}
                  {profile.timeline.length === 0 && <li className="text-white/50">No events.</li>}
                </ul>
              </article>
            ) : (
              <article className="border border-white/15 p-4">
                <h3 className="text-[11px] tracking-[0.28em] text-white/50 uppercase">Entities</h3>
                <ul className="mt-3 space-y-2">
                  {board.entities.map((entity) => (
                    <li key={entity.id}>
                      <button className="text-left text-sm text-white hover:text-primary" onClick={() => void openEntity(entity.id)}>
                        {entity.caption}
                        <span className="ml-2 text-[10px] tracking-widest text-white/40 uppercase">{entity.schema}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </article>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
