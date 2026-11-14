import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactElement, useEffect, useRef, useState } from "react";
import { call, get, type Json, type Run, type RunEvent } from "../api";
import { Concerns, ErrorBox, Section, Status } from "../components/common";
import { href } from "../router";

const OPEN = new Set(["pending", "running", "awaiting_gate"]);
const EVENT_KINDS = [
  "status",
  "phase",
  "gate",
  "gate_answered",
  "agent",
  "strategy",
  "sources",
  "items",
  "scored",
  "collected",
  "screened",
  "brief_review",
  "fields_proposed",
  "draft",
  "drafts",
  "skipped",
  "summary",
  "identified",
  "fields_researched",
];

function useRunEvents(runId: number, onEvent: () => void): RunEvent[] {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const notify = useRef(onEvent);
  notify.current = onEvent;
  useEffect(() => {
    setEvents([]);
    // EventSource resends Last-Event-ID on reconnect, so the server replays only what was missed.
    const source = new EventSource(`/api/runs/${runId}/events`);
    const receive = (message: MessageEvent<string>) => {
      const event = JSON.parse(message.data) as RunEvent;
      setEvents((seen) => (seen.some((e) => e.seq === event.seq) ? seen : [...seen, event]));
      if (event.kind !== "agent") notify.current();
    };
    for (const kind of EVENT_KINDS) source.addEventListener(kind, receive);
    return () => source.close();
  }, [runId]);
  return events;
}

export function RunView({ id }: { id: number }): ReactElement {
  const client = useQueryClient();
  const run = useQuery({ queryKey: ["run", id], queryFn: () => get<Run>(`/api/runs/${id}`) });
  const refresh = () => client.invalidateQueries({ queryKey: ["run", id] });
  const events = useRunEvents(id, () => void refresh());
  const cancel = useMutation({ mutationFn: () => call<Run>("POST", `/api/runs/${id}/cancel`), onSuccess: refresh });
  const resume = useMutation({ mutationFn: () => call<Run>("POST", `/api/runs/${id}/resume`), onSuccess: refresh });
  const r = run.data;
  if (!r) return <ErrorBox error={run.error} />;
  const itemId = typeof r.state.item_id === "number" ? r.state.item_id : null;

  return (
    <div className="stack">
      <h2>
        Run {r.id}: {r.pipeline} <Status status={r.status} />
      </h2>
      <p>
        <a href={href.project(r.project_id, "runs")}>Project runs</a> · <a href={href.project(r.project_id, "report")}>Report</a>
        {itemId !== null && (
          <>
            {" "}
            · <a href={href.item(itemId)}>Lead</a>
          </>
        )}
      </p>
      <Section
        title={`Phase: ${r.phase ?? "starting"} · $${r.cost_usd.toFixed(2)}`}
        actions={
          <span className="actions">
            {OPEN.has(r.status) && (
              <button className="secondary" onClick={() => cancel.mutate()} disabled={cancel.isPending}>
                Cancel
              </button>
            )}
            {(r.status === "failed" || r.status === "cancelled") && (
              <button onClick={() => resume.mutate()} disabled={resume.isPending}>
                Resume from {r.phase}
              </button>
            )}
          </span>
        }
      >
        {r.error && <div className="error">{r.error}</div>}
        <ErrorBox error={cancel.error ?? resume.error} />
        <Concerns items={r.state.concerns} />
      </Section>
      {r.status === "awaiting_gate" && r.pending_gate && <Gate run={r} gate={r.pending_gate} onDone={refresh} />}
      {r.pipeline.endsWith(".fields") && r.status === "complete" && <FieldsDone run={r} />}
      {isSummary(r.state.summary) && <SummaryView summary={r.state.summary} />}
      <Section title="Activity">
        <ol className="events">
          {events.map((e) => (
            <li key={e.seq} className={`event-${e.kind}`}>
              <span className="muted">{new Date(e.created_at).toLocaleTimeString()}</span> <b>{e.kind}</b> {describe(e)}
            </li>
          ))}
        </ol>
      </Section>
    </div>
  );
}

function describe(e: RunEvent): string {
  const p = e.payload;
  if (e.kind === "agent") return `${String(p.agent)} ${String(p.kind)}${p.detail ? `: ${String(p.detail).slice(0, 160)}` : ""}`;
  if (e.kind === "phase") return String(p.phase);
  if (e.kind === "gate") return String(p.kind);
  return JSON.stringify(p);
}

type RunSummary = {
  summary: string;
  patterns: { pattern: string; item_ids: number[] }[];
  recommendations: string[];
};

function isSummary(value: unknown): value is RunSummary {
  return typeof value === "object" && value !== null && typeof (value as RunSummary).summary === "string";
}

function SummaryView({ summary }: { summary: RunSummary }): ReactElement {
  return (
    <Section title="Run summary">
      {summary.summary.split(/\n\s*\n/).map((paragraph, n) => (
        <p key={n}>{paragraph}</p>
      ))}
      {summary.patterns.length > 0 && (
        <>
          <h3>Patterns</h3>
          <ul>
            {summary.patterns.map((p, n) => (
              <li key={n}>
                {p.pattern}{" "}
                {p.item_ids.map((id) => (
                  <a key={id} className="muted" href={href.item(id)}>
                    #{id}{" "}
                  </a>
                ))}
              </li>
            ))}
          </ul>
        </>
      )}
      {summary.recommendations.length > 0 && (
        <>
          <h3>Recommendations</h3>
          <ol>
            {summary.recommendations.map((r, n) => (
              <li key={n}>{r}</li>
            ))}
          </ol>
        </>
      )}
    </Section>
  );
}

type BriefReview = { complete?: boolean; gaps?: { field: string; question: string }[]; suggestions?: string[] };

function FieldsDone({ run }: { run: Run }): ReactElement {
  const review = run.state.review as BriefReview | null;
  return (
    <Section title="Brief review">
      {review?.gaps?.length ? (
        <ul>
          {review.gaps.map((g) => (
            <li key={g.field}>
              <b>{g.field}</b>: {g.question}
            </li>
          ))}
        </ul>
      ) : (
        <p>The brief has what the search needs.</p>
      )}
      {review?.suggestions?.map((s, n) => (
        <p key={n} className="hint">
          {s}
        </p>
      ))}
      <p>
        {String(run.state.rationale ?? "")} <a href={href.project(run.project_id, "fields")}>Review the proposed fields</a>
      </p>
    </Section>
  );
}

function Gate({ run, gate, onDone }: { run: Run; gate: NonNullable<Run["pending_gate"]>; onDone: () => void }): ReactElement {
  const draft = gate.payload.strategy as Json | null;
  const original = JSON.stringify(draft, null, 2);
  const [text, setText] = useState(original);
  const [feedback, setFeedback] = useState("");
  const [editing, setEditing] = useState(false);
  const answer = useMutation({
    mutationFn: (body: Json) => call<Run>("POST", `/api/runs/${run.id}/gate`, body),
    onSuccess: onDone,
  });
  const edited = editing && text !== original;
  const approve = () => answer.mutate(edited ? { approve: true, revised: JSON.parse(text) as Json } : { approve: true });
  return (
    <Section title={`Approve the ${gate.kind}`}>
      {draft && !editing && <StrategySummary strategy={draft} />}
      {editing && <textarea className="code" rows={24} value={text} onChange={(e) => setText(e.target.value)} />}
      <div className="actions">
        <button onClick={approve} disabled={answer.isPending}>
          {edited ? "Approve my edits" : "Approve"}
        </button>
        <button className="secondary" onClick={() => setEditing(!editing)}>
          {editing ? "Show summary" : "Edit JSON"}
        </button>
      </div>
      <label className="field">
        <span>Or send it back with notes</span>
        <textarea rows={3} value={feedback} onChange={(e) => setFeedback(e.target.value)} placeholder="What should change?" />
      </label>
      <button className="secondary" disabled={!feedback.trim() || answer.isPending} onClick={() => answer.mutate({ approve: false, feedback })}>
        Revise with notes
      </button>
      <ErrorBox error={answer.error} />
    </Section>
  );
}

function StrategySummary({ strategy }: { strategy: Json }): ReactElement {
  // Strategies differ per mode, so show top-level fields generically, short values inline.
  return (
    <dl className="summary">
      {Object.entries(strategy).map(([key, value]) => (
        <div key={key}>
          <dt>{key.replaceAll("_", " ")}</dt>
          <dd>
            {typeof value === "string" || typeof value === "number" ? (
              String(value)
            ) : Array.isArray(value) && value.every((v) => typeof v === "string") ? (
              value.join(", ")
            ) : (
              <pre>{JSON.stringify(value, null, 2)}</pre>
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}
