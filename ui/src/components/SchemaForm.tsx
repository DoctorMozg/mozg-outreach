import type { ReactElement } from "react";
import type { Json } from "../api";

// Renders the pydantic JSON schemas from /api/modes/{mode}/schemas; covers what briefs and options use.
type Schema = {
  type?: string;
  title?: string;
  description?: string;
  default?: unknown;
  enum?: string[];
  $ref?: string;
  anyOf?: Schema[];
  items?: Schema;
  properties?: Record<string, Schema>;
  required?: string[];
  maxLength?: number;
  minimum?: number;
  maximum?: number;
};

const LONG_TEXT = /(_text|notes|preferences|what|value_prop|goal|voice)$/;

function humanize(name: string): string {
  const words = name.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function resolve(schema: Schema, root: Json): Schema {
  if (schema.$ref) {
    const name = schema.$ref.split("/").pop() ?? "";
    const defs = (root.$defs ?? {}) as Record<string, Schema>;
    return resolve({ ...defs[name], ...schema, $ref: undefined }, root);
  }
  if (schema.anyOf) {
    const real = schema.anyOf.find((s) => s.type !== "null");
    return real ? resolve({ ...real, title: schema.title, description: schema.description, default: schema.default }, root) : schema;
  }
  return schema;
}

interface Props {
  schema: Json;
  value: Json;
  onChange: (value: Json) => void;
}

export function SchemaForm({ schema, value, onChange }: Props): ReactElement {
  return <ObjectFields schema={schema as Schema} root={schema} value={value} onChange={onChange} />;
}

function ObjectFields({ schema, root, value, onChange }: { schema: Schema; root: Json; value: Json; onChange: (v: Json) => void }): ReactElement {
  const required = new Set(schema.required ?? []);
  return (
    <div className="form-grid">
      {Object.entries(schema.properties ?? {}).map(([name, raw]) => {
        const field = resolve(raw, root);
        // A $ref'd field would otherwise show its model's class name, so prefer the property's own.
        const label = `${raw.title ?? humanize(name)}${required.has(name) ? " *" : ""}`;
        const current = value[name] ?? field.default;
        const set = (v: unknown) => onChange({ ...value, [name]: v });
        if (field.type === "object") {
          // A nullable section can be switched off, which sends null rather than its defaults.
          const optional = raw.anyOf?.some((s) => s.type === "null") ?? false;
          const on = !(optional && name in value && value[name] === null);
          return (
            <fieldset key={name}>
              <legend>
                {optional ? (
                  <label>
                    <input type="checkbox" checked={on} onChange={(e) => set(e.target.checked ? (field.default ?? {}) : null)} /> {label}
                  </label>
                ) : (
                  label
                )}
              </legend>
              {field.description && <p className="hint">{field.description}</p>}
              {on && <ObjectFields schema={field} root={root} value={(current as Json) ?? {}} onChange={set} />}
            </fieldset>
          );
        }
        const row = field.type === "array" && field.items ? resolve(field.items, root) : null;
        if (row?.type === "object") {
          const rows = Array.isArray(current) ? (current as Json[]) : [];
          return (
            <fieldset key={name}>
              <legend>{label}</legend>
              {field.description && <p className="hint">{field.description}</p>}
              {rows.map((entry, index) => (
                <div key={index} className="stack">
                  <ObjectFields schema={row} root={root} value={entry} onChange={(v) => set(rows.map((r, i) => (i === index ? v : r)))} />
                  <button className="secondary" onClick={() => set(rows.filter((_, i) => i !== index))}>
                    Remove
                  </button>
                </div>
              ))}
              <button className="secondary" onClick={() => set([...rows, {}])}>
                Add
              </button>
            </fieldset>
          );
        }
        return (
          <label key={name} className="field">
            <span>{label}</span>
            <Input name={name} field={field} value={current} onChange={set} />
            {field.description && <small className="hint">{field.description}</small>}
          </label>
        );
      })}
    </div>
  );
}

function Input({ name, field, value, onChange }: { name: string; field: Schema; value: unknown; onChange: (v: unknown) => void }): ReactElement {
  if (field.enum) {
    return (
      <select value={String(value ?? "")} onChange={(e) => onChange(e.target.value)}>
        {field.enum.map((option) => (
          <option key={option}>{option}</option>
        ))}
      </select>
    );
  }
  switch (field.type) {
    case "boolean":
      return <input type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} />;
    case "integer":
    case "number":
      return (
        <input
          type="number"
          value={value === undefined || value === null ? "" : String(value)}
          min={field.minimum}
          max={field.maximum}
          onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
        />
      );
    case "array":
      return (
        <textarea
          rows={3}
          placeholder="One per line"
          value={Array.isArray(value) ? value.join("\n") : ""}
          onChange={(e) => onChange(e.target.value.split("\n").filter((line) => line.trim() !== ""))}
        />
      );
    default:
      return LONG_TEXT.test(name) ? (
        <textarea rows={name.endsWith("_text") ? 10 : 3} value={String(value ?? "")} onChange={(e) => onChange(e.target.value)} />
      ) : (
        <input value={String(value ?? "")} onChange={(e) => onChange(e.target.value === "" ? null : e.target.value)} />
      );
  }
}
