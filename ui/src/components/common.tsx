import type { ReactElement, ReactNode } from "react";
import type { FieldValue, RunStatus } from "../api";

export function ErrorBox({ error }: { error: unknown }): ReactElement | null {
  if (!error) return null;
  return <div className="error">{error instanceof Error ? error.message : String(error)}</div>;
}

export function Status({ status }: { status: RunStatus }): ReactElement {
  return <span className={`status status-${status}`}>{status.replace("_", " ")}</span>;
}

export function Score({ value }: { value: number | null }): ReactElement {
  if (value === null) return <span className="score muted">n/a</span>;
  const band = value >= 70 ? "high" : value >= 45 ? "mid" : "low";
  return <span className={`score score-${band}`}>{value.toFixed(0)}</span>;
}

export function Value({ field }: { field: FieldValue | undefined }): ReactElement {
  if (!field || field.value === null || field.value === undefined) return <span className="muted">-</span>;
  const shown = Array.isArray(field.value) ? field.value.join(", ") : String(field.value);
  return (
    <span className={`value confidence-${field.confidence}`} title={field.quote ?? undefined}>
      {shown}
      {field.source_url && (
        <a className="source" href={field.source_url} target="_blank" rel="noreferrer noopener">
          src
        </a>
      )}
    </span>
  );
}

export function Section({ title, children, actions }: { title: string; children: ReactNode; actions?: ReactNode }): ReactElement {
  return (
    <section className="card">
      <header>
        <h3>{title}</h3>
        {actions}
      </header>
      {children}
    </section>
  );
}

export function Concerns({ items }: { items: string[] | undefined }): ReactElement | null {
  if (!items?.length) return null;
  return (
    <ul className="concerns">
      {items.map((c, n) => (
        <li key={n}>{c}</li>
      ))}
    </ul>
  );
}
