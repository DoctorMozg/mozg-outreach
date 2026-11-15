import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactElement, useState } from "react";
import { call, get, type Draft, type ItemDetail, type Run } from "../api";
import { ErrorBox, Score, Section, Value } from "../components/common";
import { Diff } from "../components/Diff";
import { href } from "../router";
import { Axes } from "./Report";

const INTERACTIONS = ["sent", "replied", "called", "met", "declined", "note"] as const;

export function ItemView({ id }: { id: number }): ReactElement {
  const client = useQueryClient();
  const key = ["item", id];
  const item = useQuery({ queryKey: key, queryFn: () => get<ItemDetail>(`/api/items/${id}`) });
  const refresh = (): void => void client.invalidateQueries({ queryKey: key });
  const select = useMutation({
    mutationFn: (selected: boolean) => call("PATCH", `/api/items/${id}`, { selected }),
    onSuccess: refresh,
  });
  const start = useMutation({
    mutationFn: (action: "enrich" | "letters") => call<Run>("POST", `/api/items/${id}/${action}`),
    onSuccess: (run) => (window.location.hash = href.run(run.id)),
  });
  const i = item.data;
  if (!i) return <ErrorBox error={item.error} />;

  return (
    <div className="stack">
      <h2>
        <Score value={i.score_total} /> {i.title}
      </h2>
      <p>
        <a href={href.project(i.project_id, "report")}>Back to the report</a>
      </p>
      <Section
        title="Actions"
        actions={
          <span className="actions">
            <label>
              <input type="checkbox" checked={i.selected} onChange={(e) => select.mutate(e.target.checked)} /> selected
            </label>
            {i.kind !== "gig" && (
              <button className="secondary" disabled={!i.selected || start.isPending} onClick={() => start.mutate("enrich")}>
                Enrich
              </button>
            )}
            <button disabled={!i.selected || start.isPending} onClick={() => start.mutate("letters")}>
              {{ company: "Write letters", job: "Write cover letter", gig: "Write proposal" }[i.kind]}
            </button>
          </span>
        }
      >
        {!i.selected && <p className="hint">Select the lead to enrich it or draft letters.</p>}
        <ErrorBox error={select.error ?? start.error} />
      </Section>
      <Section title="Fields">
        <table className="compact">
          <tbody>
            {Object.entries(i.fields).map(([name, field]) => (
              <tr key={name}>
                <th>{name}</th>
                <td>
                  <Value field={field} />
                  {field.quote && <blockquote>{field.quote}</blockquote>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>
      {i.score && (
        <Section title="Why this score">
          {i.score.blockers.length > 0 && <p className="error">Blockers: {i.score.blockers.join(", ")}</p>}
          <Axes item={i} />
        </Section>
      )}
      {i.entities.length > 0 && (
        <Section title="People and companies">
          <ul className="list">
            {i.entities.map((e) => (
              <li key={`${e.entity_id}-${e.role}`}>
                <a href={href.entity(e.entity_id)}>{e.name}</a> <span className="tag">{e.kind}</span> <span className="muted">{e.role}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}
      <Drafts drafts={i.drafts} onSaved={refresh} />
      <Interactions item={i} onSaved={refresh} />
      <Section title="Research details">
        {Object.entries(i.details).map(([name, detail]) => (
          <details key={name}>
            <summary>{name}</summary>
            <pre>{JSON.stringify(detail, null, 2)}</pre>
          </details>
        ))}
      </Section>
    </div>
  );
}

function Drafts({ drafts, onSaved }: { drafts: Draft[]; onSaved: () => void }): ReactElement | null {
  const threads = new Map<string, Draft[]>();
  for (const d of drafts) {
    const key = `${d.recipient} · ${d.channel}`;
    threads.set(key, [...(threads.get(key) ?? []), d]);
  }
  if (threads.size === 0) return null;
  return (
    <>
      {[...threads.entries()].map(([title, versions]) => (
        <DraftThread key={title} title={title} versions={versions} onSaved={onSaved} />
      ))}
    </>
  );
}

function DraftThread({ title, versions, onSaved }: { title: string; versions: Draft[]; onSaved: () => void }): ReactElement {
  const latest = versions.at(-1)!;
  const [shown, setShown] = useState(latest.version);
  const [view, setView] = useState<"text" | "naturalized" | "previous" | "edit">("text");
  const draft = versions.find((v) => v.version === shown) ?? latest;
  const previous = versions.find((v) => v.version === draft.version - 1);
  const [subject, setSubject] = useState(draft.subject ?? "");
  const [body, setBody] = useState(draft.body);
  const save = useMutation({
    mutationFn: () => call<Draft>("PUT", `/api/drafts/${draft.id}`, { subject: subject || null, body }),
    onSuccess: (saved) => {
      setShown(saved.version);
      setView("text");
      onSaved();
    },
  });
  const r = draft.review;
  const startEdit = (): void => {
    setSubject(draft.subject ?? "");
    setBody(draft.body);
    setView("edit");
  };
  const copy = (): void => void navigator.clipboard.writeText(draft.subject ? `${draft.subject}\n\n${draft.body}` : draft.body);

  return (
    <Section
      title={`Draft: ${title}`}
      actions={
        <span className="actions">
          <select value={shown} onChange={(e) => setShown(Number(e.target.value))}>
            {versions.map((v) => (
              <option key={v.version} value={v.version}>
                v{v.version}
                {v.review.edited_by_user ? " (yours)" : ""}
              </option>
            ))}
          </select>
          <button className="secondary" onClick={() => setView("text")}>
            Text
          </button>
          {r.before_naturalize && (
            <button className="secondary" onClick={() => setView("naturalized")}>
              Naturalizer diff
            </button>
          )}
          {previous && (
            <button className="secondary" onClick={() => setView("previous")}>
              Diff to v{previous.version}
            </button>
          )}
          <button className="secondary" onClick={startEdit}>
            Edit
          </button>
          <button onClick={copy}>Copy</button>
        </span>
      }
    >
      {r.address && (
        <p>
          To <b>{r.address}</b> <span className="tag">{r.address_confidence}</span>
        </p>
      )}
      {draft.subject && view !== "edit" && (
        <p>
          Subject: <b>{draft.subject}</b>
        </p>
      )}
      {view === "text" && <pre className="letter">{draft.body}</pre>}
      {view === "naturalized" && r.before_naturalize && <Diff before={r.before_naturalize} after={draft.body} />}
      {view === "previous" && previous && <Diff before={previous.body} after={draft.body} />}
      {view === "edit" && (
        <div className="form-grid">
          <label className="field">
            <span>Subject</span>
            <input value={subject} onChange={(e) => setSubject(e.target.value)} />
          </label>
          <textarea className="letter" rows={16} value={body} onChange={(e) => setBody(e.target.value)} />
          <div className="actions">
            <button onClick={() => save.mutate()} disabled={save.isPending}>
              Save as v{latest.version + 1}
            </button>
            <button className="secondary" onClick={() => setView("text")}>
              Discard
            </button>
          </div>
          <ErrorBox error={save.error} />
        </div>
      )}
      {r.problems && r.problems.length > 0 && <div className="error">Still breaks: {r.problems.join("; ")}</div>}
      {r.review_notes && r.review_notes.length > 0 && (
        <ul className="concerns">
          {r.review_notes.map((n, k) => (
            <li key={k}>{n}</li>
          ))}
        </ul>
      )}
      {r.hook_used && (
        <p className="hint">
          Hook: {r.hook_used} · Angle: {r.angle_used} · Reader: {r.reader_calibration}
          {r.changes?.length ? ` · Naturalizer: ${r.changes.join("; ")}` : ""}
        </p>
      )}
    </Section>
  );
}

function Interactions({ item, onSaved }: { item: ItemDetail; onSaved: () => void }): ReactElement {
  const [kind, setKind] = useState<(typeof INTERACTIONS)[number]>("sent");
  const [note, setNote] = useState("");
  const log = useMutation({
    mutationFn: () => call("POST", `/api/items/${item.id}/interactions`, { kind, note }),
    onSuccess: () => {
      setNote("");
      onSaved();
    },
  });
  return (
    <Section title="History">
      <ul className="list">
        {item.interactions.map((h) => (
          <li key={h.id}>
            <span className="muted">{new Date(h.happened_at).toLocaleDateString()}</span> <b>{h.kind}</b> {h.note}
          </li>
        ))}
      </ul>
      <div className="inline">
        <select value={kind} onChange={(e) => setKind(e.target.value as (typeof INTERACTIONS)[number])}>
          {INTERACTIONS.map((k) => (
            <option key={k}>{k}</option>
          ))}
        </select>
        <input placeholder="Note" value={note} onChange={(e) => setNote(e.target.value)} />
        <button onClick={() => log.mutate()} disabled={log.isPending}>
          Log
        </button>
      </div>
      <ErrorBox error={log.error} />
    </Section>
  );
}
