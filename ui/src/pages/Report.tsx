import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactElement, Fragment, useState } from "react";
import { call, get, type Graph, type Item, type Project } from "../api";
import { ErrorBox, Score, Section, Value } from "../components/common";
import { GraphView } from "../components/GraphView";
import { href } from "../router";

export function Report({ project }: { project: Project }): ReactElement {
  const client = useQueryClient();
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [sort, setSort] = useState<"score" | "date">("score");
  const range = dateWindow(since, until);
  const key = ["items", project.id, since, until, sort];
  const items = useQuery({
    queryKey: key,
    queryFn: () => get<Item[]>(`/api/projects/${project.id}/items?${new URLSearchParams({ ...range, sort }).toString()}`),
  });
  const graph = useQuery({
    queryKey: ["graph", project.id, since, until],
    queryFn: () => get<Graph>(`/api/graph?${new URLSearchParams({ project_id: String(project.id), ...range }).toString()}`),
  });
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
      // Gigs are answered on their own board, so there is nothing to enrich.
      const chosen = (items.data ?? []).filter((i) => i.selected && (action === "letters" || i.kind !== "gig"));
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
  const enrichable = items.data?.filter((i) => i.selected && i.kind !== "gig").length ?? 0;
  const letterLabel = { biz: "Write letters", seeker: "Write cover letters and proposals" }[project.mode];

  return (
    <>
      <Section
        title={`Report (${items.data?.length ?? 0})`}
        actions={
          <span className="actions">
            <label>
              <input type="checkbox" checked={onlySelected} onChange={(e) => setOnlySelected(e.target.checked)} /> selected only
            </label>
            {(project.mode === "biz" || enrichable > 0) && (
              <button className="secondary" disabled={!enrichable || bulk.isPending} onClick={() => bulk.mutate("enrich")}>
                Enrich {enrichable}
              </button>
            )}
            <button disabled={!selectedCount || bulk.isPending} onClick={() => bulk.mutate("letters")}>
              {letterLabel} for {selectedCount}
            </button>
          </span>
        }
      >
        <ErrorBox error={items.error ?? bulk.error ?? select.error} />
        <div className="inline">
          <label>
            from <input type="date" value={since} onChange={(e) => setSince(e.target.value)} />
          </label>
          <label>
            to <input type="date" value={until} onChange={(e) => setUntil(e.target.value)} />
          </label>
          <label>
            sort by{" "}
            <select value={sort} onChange={(e) => setSort(e.target.value === "date" ? "date" : "score")}>
              <option value="score">score</option>
              <option value="date">date, newest first</option>
            </select>
          </label>
        </div>
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
              <th title="Posted on, or the day a run found it">Date</th>
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
                  <td className="muted">{item.dated}</td>
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
                    <td colSpan={columns.length + 3}>
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

/** Only the ends the user set, so an empty input means no bound. */
function dateWindow(since: string, until: string): Record<string, string> {
  return { ...(since ? { since } : {}), ...(until ? { until } : {}) };
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
