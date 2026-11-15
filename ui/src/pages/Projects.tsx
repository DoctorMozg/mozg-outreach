import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactElement, useState } from "react";
import { call, get, type Mode, type Project } from "../api";
import { ErrorBox, Section } from "../components/common";
import { href } from "../router";

const MODES: { mode: Mode; label: string }[] = [
  { mode: "biz", label: "Business: find client companies" },
  { mode: "seeker", label: "Seeker: salaried jobs and freelance gigs" },
];

export function Projects(): ReactElement {
  const projects = useQuery({ queryKey: ["projects"], queryFn: () => get<Project[]>("/api/projects") });
  return (
    <div className="stack">
      <Section title="Projects">
        <ErrorBox error={projects.error} />
        {projects.data?.length === 0 && <p className="hint">No projects yet.</p>}
        <ul className="list">
          {projects.data?.map((p) => (
            <li key={p.id}>
              <a href={href.project(p.id)}>{p.name}</a> <span className="tag">{p.mode}</span>
              {!p.brief && <span className="muted"> brief missing</span>}
            </li>
          ))}
        </ul>
      </Section>
      <NewProject />
    </div>
  );
}

function NewProject(): ReactElement {
  const client = useQueryClient();
  const [name, setName] = useState("");
  const [mode, setMode] = useState<Mode>("biz");
  const [sender, setSender] = useState("");
  const create = useMutation({
    mutationFn: () => call<Project>("POST", "/api/projects", { name, mode, sender_name: sender || null }),
    onSuccess: (project) => {
      void client.invalidateQueries({ queryKey: ["projects"] });
      window.location.hash = href.project(project.id);
    },
  });
  return (
    <Section title="New project">
      <form
        className="form-grid"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        <label className="field">
          <span>Name *</span>
          <input value={name} onChange={(e) => setName(e.target.value)} required />
        </label>
        <label className="field">
          <span>Mode</span>
          <select value={mode} onChange={(e) => setMode(e.target.value as Mode)}>
            {MODES.map((m) => (
              <option key={m.mode} value={m.mode}>
                {m.label}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Your name (signs letters)</span>
          <input value={sender} onChange={(e) => setSender(e.target.value)} />
        </label>
        <div>
          <button disabled={!name || create.isPending}>Create</button>
        </div>
        <ErrorBox error={create.error} />
      </form>
    </Section>
  );
}
