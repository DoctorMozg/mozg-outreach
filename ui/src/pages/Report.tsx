import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactElement, Fragment, useState } from "react";
import { call, get, type Graph, type Item, type Project } from "../api";
import { ErrorBox, Score, Section, Value } from "../components/common";
import { GraphView } from "../components/GraphView";
import { href } from "../router";

export function Report({ project }: { project: Project }): ReactElement {
  const client = useQueryClient();
  const key = ["items", project.id];
  const items = useQuery({ queryKey: key, queryFn: () => get<Item[]>(`/api/projects/${project.id}/items`) });
  const graph = useQuery({ queryKey: ["graph", project.id], queryFn: () => get<Graph>(`/api/graph?project_id=${project.id}`) });
  const [open, setOpen] = useState<number | null>(null);
  const [onlySelected, setOnlySelected] = useState(false);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const toggleColumn = (name: string): void => {
    const next = new Set(hidden);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    setHidden(next);
  };

  const select = useMutation({
    mutationFn: (item: Item) => call<Item>("PATCH", `/api/items/${item.id}`, { selected: !item.selected }),
    onSuccess: () => client.invalidateQueries({ queryKey: key }),
  });
  const bulk = useMutation({
    mutationFn: async (action: "enrich" | "letters") => {
      const chosen = (items.data ?? []).filter((i) => i.selected);
      // One run per item; the server queues them behind its run limit.
      const results = await Promise.allSettled(chosen.map((i) => call("POST", `/api/items/${i.id}/${action}`)));
      const failed = results.filter((r) => r.status === "rejected").map((r) => String((r as PromiseRejectedResult).reason));
      if (failed.length) throw new Error(failed.join("\n"));
    },
    onSuccess: () => (window.location.hash = href.project(project.id, "runs")),
  });

  const rows = (items.data ?? []).filter((i) => !onlySelected || i.selected);
  const allColumns = [...new Set(rows.flatMap((i) => Object.keys(i.fields)))];
  const columns = allColumns.filter((c) => !hidden.has(c));
  const selectedCount = items.data?.filter((i) => i.selected).length ?? 0;
  const canEnrich = project.mode !== "freelance";
  const letterLabel = { biz: "Write letters", job: "Write cover letters", freelance: "Write proposals" }[project.mode];

  return (
    <>
      <Section
        title={`Report (${items.data?.length ?? 0})`}
        actions={
          <span className="actions">
            <label>
              <input type="checkbox" checked={onlySelected} onChange={(e) => setOnlySelected(e.target.checked)} /> selected only
            </label>
            {canEnrich && (
              <button className="secondary" disabled={!selectedCount || bulk.isPending} onClick={() => bulk.mutate("enrich")}>
                Enrich {selectedCount}
              </button>
            )}
            <button disabled={!selectedCount || bulk.isPending} onClick={() => bulk.mutate("letters")}>
              {letterLabel} for {selectedCount}
            </button>
          </span>
        }
      >
        <ErrorBox error={items.error ?? bulk.error ?? select.error} />
        <details className="columns">
          <summary>
            Columns ({columns.length} of {allColumns.length})
          </summary>
          {allColumns.map((c) => (
            <label key={c}>
              <input type="checkbox" checked={!hidden.has(c)} onChange={() => toggleColumn(c)} /> {c}
            </label>
          ))}
        </details>
        <table className="report">
          <thead>
            <tr>
              <th />
              <th>Score</th>
              <th>Lead</th>
              {columns.map((c) => (
                <th key={c}>{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((item) => (
              <Fragment key={item.id}>
                <tr className={item.selected ? "chosen" : ""}>
                  <td>
                    <input type="checkbox" checked={item.selected} onChange={() => select.mutate(item)} />
                  </td>
                  <td>
                    <button className="link" onClick={() => setOpen(open === item.id ? null : item.id)} title="Why this score">
                      <Score value={item.score_total} />
                    </button>
                  </td>
                  <td>
                    <a href={href.item(item.id)}>{item.title}</a>
                    {item.score?.blockers.map((b) => (
                      <span key={b} className="tag danger">
                        {b}
                      </span>
                    ))}
                  </td>
                  {columns.map((c) => (
                    <td key={c}>
                      <Value field={item.fields[c]} />
                    </td>
                  ))}
                </tr>
                {open === item.id && item.score && (
                  <tr className="axes">
                    <td />
                    <td colSpan={columns.length + 2}>
                      <Axes item={item} />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </Section>
      <Section title="Connections">{graph.data && <GraphView graph={graph.data} />}</Section>
    </>
  );
}

export function Axes({ item }: { item: Item }): ReactElement | null {
  if (!item.score) return null;
  return (
    <table className="compact">
      <tbody>
        {item.score.axes.map((a) => (
          <tr key={a.axis}>
            <td>{a.axis}</td>
            <td>
              <Score value={a.value} />
            </td>
            <td className="muted">x{a.weight}</td>
            <td>{a.reason}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
