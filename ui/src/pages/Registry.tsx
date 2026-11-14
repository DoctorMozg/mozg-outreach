import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactElement, useState } from "react";
import { call, get, type Entity, type EntityDetail, type Graph, type MergeSuggestion } from "../api";
import { ErrorBox, Section, Value } from "../components/common";
import { GraphView } from "../components/GraphView";
import { href } from "../router";

export function Registry(): ReactElement {
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const [seenSince, setSeenSince] = useState("");
  const entities = useQuery({
    queryKey: ["entities", q, kind, seenSince],
    queryFn: () =>
      get<Entity[]>(
        `/api/entities?${new URLSearchParams({ q, ...(kind ? { kind } : {}), ...(seenSince ? { seen_since: seenSince } : {}) }).toString()}`,
      ),
  });
  return (
    <div className="stack">
      <h2>People and companies</h2>
      <Merges />
      <Section title="Registry">
        <div className="inline">
          <input placeholder="Search names" value={q} onChange={(e) => setQ(e.target.value)} />
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="">all</option>
            <option value="company">companies</option>
            <option value="person">people</option>
          </select>
          <label>
            seen since <input type="date" value={seenSince} onChange={(e) => setSeenSince(e.target.value)} />
          </label>
        </div>
        <ErrorBox error={entities.error} />
        <ul className="list">
          {entities.data?.map((e) => (
            <li key={e.id}>
              <a href={href.entity(e.id)}>{e.name}</a> <span className="tag">{e.kind}</span>
            </li>
          ))}
        </ul>
      </Section>
    </div>
  );
}

function Merges(): ReactElement | null {
  const client = useQueryClient();
  const suggestions = useQuery({ queryKey: ["merges"], queryFn: () => get<MergeSuggestion[]>("/api/merge-suggestions") });
  const decide = useMutation({
    mutationFn: ({ id, accept }: { id: number; accept: boolean }) => call("POST", `/api/merge-suggestions/${id}/${accept ? "accept" : "reject"}`),
    onSuccess: () => client.invalidateQueries(),
  });
  if (!suggestions.data?.length) return null;
  return (
    <Section title="Possible duplicates">
      <p className="hint">Exact identifiers merge on their own; these only look alike. Merging keeps the first and moves everything onto it.</p>
      <ul className="list">
        {suggestions.data.map((s) => (
          <li key={s.id}>
            <a href={href.entity(s.a.id)}>{s.a.name}</a> and <a href={href.entity(s.b.id)}>{s.b.name}</a> <span className="muted">{s.reason}</span>{" "}
            <button onClick={() => decide.mutate({ id: s.id, accept: true })}>Merge</button>{" "}
            <button className="secondary" onClick={() => decide.mutate({ id: s.id, accept: false })}>
              Different
            </button>
          </li>
        ))}
      </ul>
      <ErrorBox error={decide.error} />
    </Section>
  );
}

export function EntityView({ id }: { id: number }): ReactElement {
  const entity = useQuery({ queryKey: ["entity", id], queryFn: () => get<EntityDetail>(`/api/entities/${id}`) });
  const [depth, setDepth] = useState(1);
  const graph = useQuery({ queryKey: ["graph", "entity", id, depth], queryFn: () => get<Graph>(`/api/graph?entity_id=${id}&depth=${depth}`) });
  const e = entity.data;
  if (!e) return <ErrorBox error={entity.error} />;
  return (
    <div className="stack">
      <h2>
        {e.name} <span className="tag">{e.kind}</span>
      </h2>
      <Section title="Profile">
        <table className="compact">
          <tbody>
            {e.identifiers.map((i) => (
              <tr key={`${i.kind}-${i.value}`}>
                <th>{i.kind}</th>
                <td>{i.value}</td>
              </tr>
            ))}
            {Object.entries(e.profile).map(([name, field]) => (
              <tr key={name}>
                <th>{name}</th>
                <td>
                  <Value field={field} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>
      <Section title="Connections">
        <ul className="list">
          {e.relations.map((r, n) => (
            <li key={n}>
              {r.direction === "out" ? `${r.type} ` : ""}
              <a href={href.entity(r.other_id)}>{r.other_name}</a>
              {r.direction === "in" ? ` (${r.type})` : ""} {r.detail && <span className="muted">{r.detail}</span>}
              {r.source_url && (
                <a className="source" href={r.source_url} target="_blank" rel="noreferrer noopener">
                  src
                </a>
              )}
            </li>
          ))}
          {e.leads.map((l) => (
            <li key={`lead-${l.item_id}`}>
              lead <a href={href.item(l.item_id)}>{l.title}</a> <span className="muted">{l.role}</span>
            </li>
          ))}
        </ul>
        <label className="inline">
          Graph depth
          <select value={depth} onChange={(ev) => setDepth(Number(ev.target.value))}>
            {[1, 2, 3].map((d) => (
              <option key={d}>{d}</option>
            ))}
          </select>
        </label>
        {graph.data && <GraphView graph={graph.data} />}
      </Section>
      <Section title={`Seen in ${e.sightings.length} places`}>
        <ul className="list">
          {e.sightings.map((s, n) => (
            <li key={n}>
              <span className="tag">{s.source_kind}</span>{" "}
              <a href={s.source_url} target="_blank" rel="noreferrer noopener">
                {s.source_url}
              </a>
              {s.quote && <blockquote>{s.quote}</blockquote>}
            </li>
          ))}
        </ul>
      </Section>
    </div>
  );
}
