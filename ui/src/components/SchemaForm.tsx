import { ActionIcon, Button, Card, Fieldset, Group, NumberInput, Select, Stack, Switch, Text, Textarea, TextInput, Tooltip } from "@mantine/core";
import { IconPlus, IconTrash } from "@tabler/icons-react";
import { type ReactElement, useEffect, useState } from "react";
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

interface ObjectProps {
  schema: Schema;
  root: Json;
  value: Json;
  onChange: (v: Json) => void;
}

function ObjectFields({ schema, root, value, onChange }: ObjectProps): ReactElement {
  const required = new Set(schema.required ?? []);
  return (
    <Stack gap="md">
      {Object.entries(schema.properties ?? {}).map(([name, raw]) => {
        const field = resolve(raw, root);
        // A $ref'd field would otherwise show its model's class name, so prefer the property's own.
        const label = raw.title ?? humanize(name);
        const current = value[name] ?? field.default;
        const set = (v: unknown): void => onChange({ ...value, [name]: v });
        if (field.type === "object") {
          // A nullable section can be switched off, which sends null rather than its defaults.
          const optional = raw.anyOf?.some((s) => s.type === "null") ?? false;
          // Off when saved as null, or when a section with no default was never filled in.
          const on = !optional || (current !== null && current !== undefined);
          return (
            <Fieldset
              key={name}
              legend={
                optional ? (
                  <Switch label={label} checked={on} onChange={(e) => set(e.currentTarget.checked ? (field.default ?? {}) : null)} />
                ) : (
                  <Text fw={600} size="sm">
                    {label}
                  </Text>
                )
              }
            >
              {field.description && (
                <Text size="xs" c="dimmed" mb="sm">
                  {field.description}
                </Text>
              )}
              {on && <ObjectFields schema={field} root={root} value={(current as Json) ?? {}} onChange={set} />}
            </Fieldset>
          );
        }
        const row = field.type === "array" && field.items ? resolve(field.items, root) : null;
        if (row?.type === "object") {
          const rows = Array.isArray(current) ? (current as Json[]) : [];
          return (
            <Fieldset key={name} legend={<Text fw={600} size="sm">{label}</Text>}>
              {field.description && (
                <Text size="xs" c="dimmed" mb="sm">
                  {field.description}
                </Text>
              )}
              <Stack gap="sm">
                {rows.map((entry, index) => (
                  <Card key={index} padding="sm" bg="var(--mantine-color-default-hover)">
                    <Group justify="flex-end" mb={-8}>
                      <Tooltip label="Remove">
                        <ActionIcon variant="subtle" color="red" onClick={() => set(rows.filter((_, i) => i !== index))} aria-label="Remove">
                          <IconTrash size={16} />
                        </ActionIcon>
                      </Tooltip>
                    </Group>
                    <ObjectFields schema={row} root={root} value={entry} onChange={(v) => set(rows.map((r, i) => (i === index ? v : r)))} />
                  </Card>
                ))}
                <Group>
                  <Button variant="light" size="xs" leftSection={<IconPlus size={14} />} onClick={() => set([...rows, {}])}>
                    Add {label.toLowerCase().replace(/s$/, "")}
                  </Button>
                </Group>
              </Stack>
            </Fieldset>
          );
        }
        return <Input key={name} name={name} label={label} required={required.has(name)} field={field} value={current} onChange={set} />;
      })}
    </Stack>
  );
}

interface InputProps {
  name: string;
  label: string;
  required: boolean;
  field: Schema;
  value: unknown;
  onChange: (v: unknown) => void;
}

function Input({ name, label, required, field, value, onChange }: InputProps): ReactElement {
  const common = { label, description: field.description, withAsterisk: required };
  if (field.enum) {
    return <Select {...common} data={field.enum} value={value === undefined || value === null ? null : String(value)} onChange={onChange} />;
  }
  switch (field.type) {
    case "boolean":
      return <Switch label={label} description={field.description} checked={Boolean(value)} onChange={(e) => onChange(e.currentTarget.checked)} />;
    case "integer":
    case "number":
      return (
        <NumberInput
          {...common}
          maw={260}
          value={typeof value === "number" ? value : ""}
          min={field.minimum}
          max={field.maximum}
          allowDecimal={field.type === "number"}
          onChange={(v) => onChange(v === "" ? null : Number(v))}
        />
      );
    case "array":
      return <LinesInput label={label} required={required} description={field.description} value={value} onChange={onChange} />;
    default:
      return LONG_TEXT.test(name) ? (
        <Textarea
          {...common}
          autosize
          minRows={name.endsWith("_text") ? 8 : 3}
          maxRows={name.endsWith("_text") ? 24 : 10}
          value={String(value ?? "")}
          onChange={(e) => onChange(e.currentTarget.value)}
        />
      ) : (
        <TextInput {...common} value={String(value ?? "")} onChange={(e) => onChange(e.currentTarget.value === "" ? null : e.currentTarget.value)} />
      );
  }
}

const lines = (text: string): string[] => text.split("\n").filter((line) => line.trim() !== "");

interface LinesProps {
  label: string;
  required: boolean;
  description: string | undefined;
  value: unknown;
  onChange: (v: string[]) => void;
}

/** One entry per line; keeps the raw text so Enter can start a new line before it has content. */
function LinesInput({ label, required, description, value, onChange }: LinesProps): ReactElement {
  const joined = Array.isArray(value) ? value.map(String).join("\n") : "";
  const [text, setText] = useState(joined);
  // Take outside changes (a drafted brief) but not our own echo, which would eat the open line.
  useEffect(() => {
    setText((current) => (lines(current).join("\n") === joined ? current : joined));
  }, [joined]);
  return (
    <Textarea
      label={label}
      withAsterisk={required}
      description={[description, "One per line."].filter(Boolean).join(" ")}
      autosize
      minRows={2}
      maxRows={10}
      value={text}
      onChange={(e) => {
        setText(e.currentTarget.value);
        onChange(lines(e.currentTarget.value));
      }}
    />
  );
}
