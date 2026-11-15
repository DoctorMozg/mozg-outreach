import { type UseQueryResult, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactElement, useEffect, useState } from "react";
import { type BriefDraft, call, get, type Json, type Project as ProjectRow, type Run } from "../api";
import { Concerns, ErrorBox, Section, Status } from "../components/common";
import { SchemaForm } from "../components/SchemaForm";
import { href } from "../router";
import { Fields } from "./Fields";
import { Report } from "./Report";
import { Sources } from "./Sources";

type Schemas = { brief: Json; options: Json };
const TABS = ["brief", "fields", "sources", "runs", "report"] as const;

export function Project({ id, tab }: { id: number; tab: string }): ReactElement {
  const project = useQuery({ queryKey: ["project", id], queryFn: () => get<ProjectRow>(`/api/projects/${id}`) });
  if (!project.data) return <ErrorBox error={project.error} />;
  const p = project.data;
  return (
    <div className="stack">
      <header className="inline">
        <h2>
          {p.name} <span className="tag">{p.mode}</span>
        </h2>
        <DeleteProject project={p} />
      </header>
      <nav className="tabs">
        {TABS.map((t) => (
          <a key={t} href={href.project(id, t)} className={t === tab ? "active" : ""}>
            {t}
          </a>
        ))}
      </nav>
      {tab === "brief" && <Brief project={p} />}
      {tab === "fields" && <Fields project={p} />}
      {tab === "sources" && <Sources project={p} />}
      {tab === "runs" && <Runs project={p} />}
      {tab === "report" && <Report project={p} />}
    </div>
  );
}

/** Two clicks, so one stray click can't drop a project with its runs, report and drafts. */
function DeleteProject({ project }: { project: ProjectRow }): ReactElement {
  const client = useQueryClient();
  const [asking, setAsking] = useState(false);
  const remove = useMutation({
    mutationFn: () => call("DELETE", `/api/projects/${project.id}`),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["projects"] });
      window.location.hash = href.projects();
    },
  });
  if (!asking) {
    return (
      <button className="secondary" onClick={() => setAsking(true)}>
        Delete project
      </button>
    );
  }
  return (
    <span className="actions">
      <span className="hint">Delete its runs, report and drafts? People and companies stay in the registry.</span>
      <button className="danger" disabled={remove.isPending} onClick={() => remove.mutate()}>
        Delete
      </button>
      <button className="secondary" onClick={() => setAsking(false)}>
        Cancel
      </button>
      <ErrorBox error={remove.error} />
    </span>
  );
}

function useSchemas(mode: string): UseQueryResult<Schemas> {
  return useQuery({ queryKey: ["schemas", mode], queryFn: () => get<Schemas>(`/api/modes/${mode}/schemas`), staleTime: Infinity });
}

function Brief({ project }: { project: ProjectRow }): ReactElement {
  const client = useQueryClient();
  const schemas = useSchemas(project.mode);
  const [brief, setBrief] = useState<Json>(project.brief ?? {});
  const [sender, setSender] = useState({ name: project.sender_name ?? "", tone: project.sender_tone });
  useEffect(() => setBrief(project.brief ?? {}), [project.brief]);
  const save = useMutation({
    mutationFn: () =>
      call<ProjectRow>("PATCH", `/api/projects/${project.id}`, {
        brief,
        sender_name: sender.name || null,
        sender_tone: sender.tone,
      }),
    onSuccess: (saved) => client.setQueryData(["project", project.id], saved),
  });
  return (
    <>
      <Section title="Sender">
        <div className="form-grid">
          <label className="field">
            <span>Your name (signs every letter)</span>
            <input value={sender.name} onChange={(e) => setSender({ ...sender, name: e.target.value })} />
          </label>
          <label className="field">
            <span>Tone (picks salutation and sign-off)</span>
            <select value={sender.tone} onChange={(e) => setSender({ ...sender, tone: e.target.value })}>
              <option>warm</option>
              <option>neutral</option>
              <option>formal</option>
            </select>
          </label>
        </div>
      </Section>
      <WriteWithAi project={project} brief={brief} onDraft={setBrief} />
      <Section title="Brief" actions={<button onClick={() => save.mutate()} disabled={save.isPending}>Save</button>}>
        {schemas.data && <SchemaForm schema={schemas.data.brief} value={brief} onChange={setBrief} />}
        <ErrorBox error={save.error ?? schemas.error} />
        {save.isSuccess && <p className="hint">Saved.</p>}
      </Section>
    </>
  );
}

/** Drafts the brief from the company site (biz) or the CV (seeker) into the form, unsaved. */
function WriteWithAi({ project, brief, onDraft }: { project: ProjectRow; brief: Json; onDraft: (brief: Json) => void }): ReactElement {
  const client = useQueryClient();
  const biz = project.mode === "biz";
  const [source, setSource] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const site = source ?? "";
  const cv = source ?? (typeof brief.cv_text === "string" ? brief.cv_text : "");
  const write = useMutation({
    mutationFn: () =>
      call<BriefDraft>("POST", `/api/projects/${project.id}/brief/generate`, biz ? { site_url: site, note } : { cv_text: cv, note }),
    onSuccess: (draft) => {
      onDraft(draft.brief);
      void client.invalidateQueries({ queryKey: ["fields", project.id] });
    },
  });
  const draft = write.data;
  return (
    <Section
      title="Write with AI"
      actions={
        <button disabled={write.isPending || !(biz ? site : cv)} onClick={() => write.mutate()}>
          {write.isPending ? "Writing, about a minute..." : "Write the brief"}
        </button>
      }
    >
      <div className="form-grid">
        {biz ? (
          <label className="field">
            <span>Your company's website</span>
            <input type="url" placeholder="https://" value={site} onChange={(e) => setSource(e.target.value)} />
          </label>
        ) : (
          <label className="field">
            <span>Your CV, pasted as text</span>
            <textarea rows={8} value={cv} onChange={(e) => setSource(e.target.value)} />
          </label>
        )}
        <label className="field">
          <span>Note (optional)</span>
          <textarea rows={2} placeholder={biz ? "Goal, target regions, companies to avoid" : "Regions, salary or rate, work you won't take"} value={note} onChange={(e) => setNote(e.target.value)} />
        </label>
      </div>
      <ErrorBox error={write.error} />
      {draft && (
        <>
          <p className="hint">
            The form below now holds the draft (${draft.cost_usd.toFixed(2)}). It is not saved: check it, then press Save.
            {draft.field_version !== null && (
              <>
                {" "}
                Report fields were drafted too: <a href={href.project(project.id, "fields")}>review them on the Fields tab</a>.
              </>
            )}
          </p>
          <Concerns items={draft.concerns} />
        </>
      )}
    </Section>
  );
}

function Runs({ project }: { project: ProjectRow }): ReactElement {
  const client = useQueryClient();
  const schemas = useSchemas(project.mode);
  const [options, setOptions] = useState<Json>({});
  const runs = useQuery({
    queryKey: ["runs", project.id],
    queryFn: () => get<Run[]>(`/api/projects/${project.id}/runs`),
    refetchInterval: 5000,
  });
  const start = useMutation({
    mutationFn: () => call<Run>("POST", `/api/projects/${project.id}/runs`, { options }),
    onSuccess: (run) => {
      void client.invalidateQueries({ queryKey: ["runs", project.id] });
      window.location.hash = href.run(run.id);
    },
  });
  return (
    <>
      <Section title="Run the search" actions={<button onClick={() => start.mutate()} disabled={start.isPending || !project.brief}>Start</button>}>
        {!project.brief && <p className="hint">Write and save the brief first.</p>}
        {schemas.data && <SchemaForm schema={schemas.data.options} value={options} onChange={setOptions} />}
        <ErrorBox error={start.error} />
      </Section>
      <Section title="Runs">
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>Pipeline</th>
              <th>Status</th>
              <th>Phase</th>
              <th>Cost</th>
              <th>Started</th>
            </tr>
          </thead>
          <tbody>
            {runs.data?.map((r) => (
              <tr key={r.id}>
                <td>
                  <a href={href.run(r.id)}>{r.id}</a>
                </td>
                <td>{r.pipeline}</td>
                <td>
                  <Status status={r.status} />
                </td>
                <td>{r.phase}</td>
                <td>${r.cost_usd.toFixed(2)}</td>
                <td>{new Date(r.started_at).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>
    </>
  );
}
