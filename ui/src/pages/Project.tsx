import { type UseQueryResult, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactElement, useEffect, useState } from "react";
import { call, get, type Json, type Project as ProjectRow, type Run } from "../api";
import { ErrorBox, Section, Status } from "../components/common";
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
  const tabs = TABS.filter((t) => t !== "fields" || p.mode === "biz");
  return (
    <div className="stack">
      <h2>
        {p.name} <span className="tag">{p.mode}</span>
      </h2>
      <nav className="tabs">
        {tabs.map((t) => (
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
      <Section title="Brief" actions={<button onClick={() => save.mutate()} disabled={save.isPending}>Save</button>}>
        {schemas.data && <SchemaForm schema={schemas.data.brief} value={brief} onChange={setBrief} />}
        <ErrorBox error={save.error ?? schemas.error} />
        {save.isSuccess && <p className="hint">Saved.</p>}
      </Section>
    </>
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
