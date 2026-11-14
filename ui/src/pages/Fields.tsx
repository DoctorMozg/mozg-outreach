import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactElement, useEffect, useState } from "react";
import { call, get, type FieldDefinition, type FieldSpecVersion, type Project, type Run } from "../api";
import { ErrorBox, Section } from "../components/common";
import { href } from "../router";

const TYPES: FieldDefinition["type"][] = ["text", "number", "boolean", "url", "email", "date", "list"];
const BLANK: FieldDefinition = { id: "", label: "", type: "text", description: "", required: false, example: null };

export function Fields({ project }: { project: Project }): ReactElement {
  const client = useQueryClient();
  const key = ["fields", project.id];
  const versions = useQuery({ queryKey: key, queryFn: () => get<FieldSpecVersion[]>(`/api/projects/${project.id}/fields`) });
  const latest = versions.data?.at(-1);
  const accepted = versions.data?.filter((v) => v.accepted_at).at(-1);
  const [rows, setRows] = useState<FieldDefinition[]>([]);
  useEffect(() => setRows(latest?.spec.fields ?? []), [latest]);

  const refresh = () => client.invalidateQueries({ queryKey: key });
  const propose = useMutation({
    mutationFn: () => call<Run>("POST", `/api/projects/${project.id}/fields/propose`),
    onSuccess: (run) => (window.location.hash = href.run(run.id)),
  });
  const save = useMutation({
    mutationFn: () => call<FieldSpecVersion>("POST", `/api/projects/${project.id}/fields`, { fields: rows }),
    onSuccess: refresh,
  });
  const accept = useMutation({
    mutationFn: (version: number) => call("POST", `/api/projects/${project.id}/fields/${version}/accept`),
    onSuccess: refresh,
  });
  const edit = (n: number, patch: Partial<FieldDefinition>) => setRows(rows.map((r, i) => (i === n ? { ...r, ...patch } : r)));
  const dirty = JSON.stringify(rows) !== JSON.stringify(latest?.spec.fields ?? []);

  return (
    <Section
      title="Report fields"
      actions={
        <span className="actions">
          <button className="secondary" onClick={() => propose.mutate()} disabled={propose.isPending || !project.brief}>
            Propose from brief
          </button>
          <button onClick={() => save.mutate()} disabled={!dirty || save.isPending}>
            Save as new version
          </button>
          {latest && !latest.accepted_at && !dirty && (
            <button onClick={() => accept.mutate(latest.version)} disabled={accept.isPending}>
              Accept v{latest.version}
            </button>
          )}
        </span>
      }
    >
      <p className="hint">
        {accepted ? `Runs use v${accepted.version}.` : "Accept a version before running the search."} Accepted fields keep their type;
        add a new id to change one.
      </p>
      <table className="edit">
        <thead>
          <tr>
            <th>id</th>
            <th>Label</th>
            <th>Type</th>
            <th>What to collect</th>
            <th>Req.</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((r, n) => (
            <tr key={n}>
              <td>
                <input value={r.id} onChange={(e) => edit(n, { id: e.target.value })} />
              </td>
              <td>
                <input value={r.label} onChange={(e) => edit(n, { label: e.target.value })} />
              </td>
              <td>
                <select value={r.type} onChange={(e) => edit(n, { type: e.target.value as FieldDefinition["type"] })}>
                  {TYPES.map((t) => (
                    <option key={t}>{t}</option>
                  ))}
                </select>
              </td>
              <td>
                <textarea rows={2} value={r.description} onChange={(e) => edit(n, { description: e.target.value })} />
              </td>
              <td>
                <input type="checkbox" checked={r.required} onChange={(e) => edit(n, { required: e.target.checked })} />
              </td>
              <td>
                <button className="link" onClick={() => setRows(rows.filter((_, i) => i !== n))}>
                  remove
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <button className="secondary" onClick={() => setRows([...rows, BLANK])}>
        Add field
      </button>
      <ErrorBox error={save.error ?? accept.error ?? propose.error ?? versions.error} />
    </Section>
  );
}
