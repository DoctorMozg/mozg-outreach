import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactElement, useState } from "react";
import { call, get, type Json, type Project, type Source, type SourceKind } from "../api";
import { ErrorBox, Section } from "../components/common";

// Starting configs per feed; the API validates them against each adapter's model.
const EXAMPLES: Record<string, Json> = {
  greenhouse: { boards: ["company-board-token"] },
  ashby: { boards: ["company"] },
  lever: { companies: ["company"] },
  telegram: { channels: ["@channel_name"], keywords: [], kind: "job" },
};

export function Sources({ project }: { project: Project }): ReactElement {
  const client = useQueryClient();
  const key = ["sources", project.id];
  const sources = useQuery({ queryKey: key, queryFn: () => get<Source[]>(`/api/projects/${project.id}/sources`) });
  const kinds = useQuery({ queryKey: ["source-kinds"], queryFn: () => get<SourceKind[]>("/api/source-kinds"), staleTime: Infinity });
  const usable = kinds.data?.filter((k) => k.modes.includes(project.mode)) ?? [];
  const [kind, setKind] = useState("");
  const [config, setConfig] = useState("{}");
  const refresh = () => client.invalidateQueries({ queryKey: key });

  const add = useMutation({
    mutationFn: () => call("POST", `/api/projects/${project.id}/sources`, { kind, config: JSON.parse(config) as Json }),
    onSuccess: refresh,
  });
  const toggle = useMutation({
    mutationFn: (s: Source) => call("PATCH", `/api/sources/${s.id}`, { enabled: !s.enabled }),
    onSuccess: refresh,
  });
  const remove = useMutation({ mutationFn: (s: Source) => call("DELETE", `/api/sources/${s.id}`), onSuccess: refresh });
  const pick = (k: string) => {
    setKind(k);
    setConfig(JSON.stringify(EXAMPLES[k] ?? {}, null, 2));
  };

  return (
    <>
      <Section title="Data sources">
        <table>
          <thead>
            <tr>
              <th>Kind</th>
              <th>Config</th>
              <th>Read up to</th>
              <th>On</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {sources.data?.map((s) => (
              <tr key={s.id}>
                <td>{s.kind}</td>
                <td>
                  <code>{JSON.stringify(s.config)}</code>
                </td>
                <td className="muted">{s.cursor ?? "-"}</td>
                <td>
                  <input type="checkbox" checked={s.enabled} onChange={() => toggle.mutate(s)} />
                </td>
                <td>
                  <button className="link" onClick={() => remove.mutate(s)}>
                    remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {project.mode !== "job" && <p className="hint">Business and freelance searches run on web search; feeds serve job projects.</p>}
      </Section>
      <Section title="Add a source">
        <div className="form-grid">
          <label className="field">
            <span>Kind</span>
            <select value={kind} onChange={(e) => pick(e.target.value)}>
              <option value="">choose...</option>
              {usable.map((k) => (
                <option key={k.kind} value={k.kind}>
                  {k.kind}
                </option>
              ))}
            </select>
            {kind && <small className="hint">{usable.find((k) => k.kind === kind)?.note}</small>}
          </label>
          <label className="field">
            <span>Config (JSON)</span>
            <textarea rows={4} value={config} onChange={(e) => setConfig(e.target.value)} />
          </label>
          <div>
            <button disabled={!kind || add.isPending} onClick={() => add.mutate()}>
              Add
            </button>
          </div>
        </div>
        <ErrorBox error={add.error ?? toggle.error ?? remove.error} />
      </Section>
    </>
  );
}
