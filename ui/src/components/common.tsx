import { Accordion, Alert, Anchor, Badge, type BadgeProps, Card, Group, List, Text, Tooltip } from "@mantine/core";
import { IconAlertTriangle, IconExternalLink, IconInfoCircle } from "@tabler/icons-react";
import type { ReactElement, ReactNode } from "react";
import type { Activity, ApproachStep, FieldValue, Mode, RunStatus } from "../api";

export function ErrorBox({ error }: { error: unknown }): ReactElement | null {
  if (!error) return null;
  return (
    <Alert color="red" variant="light" icon={<IconAlertTriangle size={18} />}>
      {error instanceof Error ? error.message : String(error)}
    </Alert>
  );
}

const STATUS_COLORS: Record<RunStatus, string> = {
  pending: "gray",
  running: "blue",
  awaiting_gate: "yellow",
  complete: "green",
  failed: "red",
  cancelled: "gray",
};

export function Status({ status }: { status: RunStatus }): ReactElement {
  return (
    <Badge color={STATUS_COLORS[status]} variant="light">
      {status === "awaiting_gate" ? "needs you" : status}
    </Badge>
  );
}

export function scoreColor(value: number | null): string {
  if (value === null) return "gray";
  return value >= 70 ? "teal" : value >= 45 ? "yellow" : "red";
}

export function Score({ value, size = "md" }: { value: number | null; size?: BadgeProps["size"] }): ReactElement {
  return (
    <Badge color={scoreColor(value)} variant="light" size={size} miw={42} style={{ fontVariantNumeric: "tabular-nums" }}>
      {value === null ? "n/a" : value.toFixed(0)}
    </Badge>
  );
}

/** Labels of the interaction kinds the user can log, in menu order. */
export const INTERACTION_LABELS = {
  sent: "Sent",
  replied: "Replied",
  bounced: "Bounced",
  called: "Called",
  met: "Met",
  declined: "Declined by me",
  refused: "Refused by them",
  note: "Note",
} as const;
export type InteractionKind = keyof typeof INTERACTION_LABELS;

export function interactionLabel(kind: string): string {
  return INTERACTION_LABELS[kind as InteractionKind] ?? kind;
}

const ACTIVITY_COLORS: Record<string, string> = { sent: "blue", replied: "teal", bounced: "red", called: "grape", met: "grape", declined: "red", refused: "orange" };

/** The lead's last logged status and note; `projectId` is the list's project, to flag another one's log. */
export function ActivityBadge({ activity, projectId, size = "xs" }: { activity: Activity | null; projectId: number; size?: BadgeProps["size"] }): ReactElement | null {
  if (!activity) return null;
  const when = activity.status_at ? new Date(activity.status_at).toLocaleDateString() : null;
  const elsewhere = activity.project_id !== projectId;
  const label = [when && `${interactionLabel(activity.status ?? "note")} on ${when}`, elsewhere && "logged in another project", activity.note].filter(Boolean).join(" · ");
  return (
    <Tooltip label={label} multiline maw={360}>
      <Badge size={size} variant={elsewhere ? "outline" : "filled"} color={activity.status ? (ACTIVITY_COLORS[activity.status] ?? "gray") : "gray"}>
        {activity.status ?? "note"}
      </Badge>
    </Tooltip>
  );
}

const STEP_CHANNELS = { email: "email", linkedin: "LinkedIn", phone: "call" } as const;

/** Whole days from today (local) to a YYYY-MM-DD date; negative when it is past. */
function daysUntil(day: string): number {
  const now = new Date();
  const [y, m, d] = day.split("-").map(Number) as [number, number, number];
  return Math.round((Date.UTC(y, m - 1, d) - Date.UTC(now.getFullYear(), now.getMonth(), now.getDate())) / 86_400_000);
}

const plural = (n: number): string => `${n} day${n === 1 ? "" : "s"}`;

/** "in 10 days", "today", "3 days late": how far the next touch is. */
function countdown(days: number): string {
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  return days > 0 ? `in ${plural(days)}` : `${plural(-days)} late`;
}

/** Where a company's approach sequence stands: the next step, to whom, and when it is due. */
export function StepBadge({ step, size = "xs" }: { step: ApproachStep | null; size?: BadgeProps["size"] }): ReactElement | null {
  if (!step) return null;
  if (step.step === null) {
    return (
      <Badge size={size} variant="light" color="gray">
        sequence done
      </Badge>
    );
  }
  const days = step.due ? daysUntil(step.due) : null;
  const color = days === null ? "gray" : days < 0 ? "red" : days === 0 ? "orange" : "blue";
  const when = days === null ? "not started" : countdown(days);
  const what = [step.channel && STEP_CHANNELS[step.channel], step.reader && `to ${step.reader}`].filter(Boolean).join(" ");
  const due = step.due ? ` (due ${step.due})` : "";
  return (
    <Tooltip label={`Approach step ${step.step} of ${step.total}: ${what}, ${when}${due}`}>
      <Badge size={size} variant="light" color={color}>
        step {step.step}/{step.total}
        {step.channel ? ` · ${STEP_CHANNELS[step.channel]}` : ""} · {when}
      </Badge>
    </Tooltip>
  );
}

export function ModeBadge({ mode }: { mode: Mode }): ReactElement {
  return (
    <Badge size="xs" variant="light" color={mode === "biz" ? "indigo" : "grape"}>
      {mode}
    </Badge>
  );
}

/** A researched value with its evidence: the quote on hover and a link to the page it came from. */
export function Value({ field }: { field: FieldValue | undefined }): ReactElement {
  if (!field || field.value === null || field.value === undefined || field.value === "") {
    return <Text span c="dimmed">-</Text>;
  }
  const shown = Array.isArray(field.value) ? field.value.join(", ") : String(field.value);
  const guessed = field.confidence === "guessed";
  const text = (
    <Text span inherit fs={guessed ? "italic" : undefined} c={guessed ? "dimmed" : undefined}>
      {shown}
    </Text>
  );
  return (
    <span>
      {field.quote ? <Tooltip label={`"${field.quote}"`}>{text}</Tooltip> : text}
      {guessed && (
        <Badge size="xs" variant="outline" color="orange" ml={6}>
          guessed
        </Badge>
      )}
      {field.source_url && (
        <Anchor href={field.source_url} target="_blank" rel="noreferrer noopener" ml={4} aria-label="Source" style={{ verticalAlign: "-2px" }}>
          <IconExternalLink size={14} />
        </Anchor>
      )}
    </span>
  );
}

// Agents prefix a note with the source it is about ("Bayut: ..."), which is what groups them.
const PREFIX = /^([^:]{3,240}):\s+(.+)$/s;

export function groupConcerns(items: string[]): [string, string[]][] {
  const groups = new Map<string, string[]>();
  const keyed = items.map((item) => PREFIX.exec(item));
  const counts = new Map<string, number>();
  for (const match of keyed) if (match) counts.set(match[1]!, (counts.get(match[1]!) ?? 0) + 1);
  items.forEach((item, n) => {
    const match = keyed[n];
    const shared = match && (counts.get(match[1]!) ?? 0) > 1;
    const group = shared ? match[1]! : "General";
    groups.set(group, [...(groups.get(group) ?? []), shared ? match[2]! : item]);
  });
  // General notes first: they are about the run as a whole.
  return [...groups.entries()].sort(([a], [b]) => (a === "General" ? -1 : b === "General" ? 1 : 0));
}

/** Notes the agents flagged; a few show as a list, many collapse into groups by source. */
export function Concerns({ items, title = "Notes from the agents" }: { items: string[] | undefined; title?: string }): ReactElement | null {
  if (!items?.length) return null;
  if (items.length <= 4) {
    return (
      <Alert variant="light" color="yellow" icon={<IconInfoCircle size={18} />} title={title}>
        <List size="sm" spacing={4}>
          {items.map((c, n) => (
            <List.Item key={n}>{c}</List.Item>
          ))}
        </List>
      </Alert>
    );
  }
  const groups = groupConcerns(items);
  return (
    <div>
      <Group gap={6} mb={6}>
        <IconInfoCircle size={16} color="var(--mantine-color-yellow-filled)" />
        <Text size="sm" fw={600}>
          {title} ({items.length})
        </Text>
      </Group>
      <Accordion variant="contained" multiple chevronPosition="left">
        {groups.map(([group, notes]) => (
          <Accordion.Item key={group} value={group}>
            <Accordion.Control>
              <Group justify="space-between" wrap="nowrap" pr="sm">
                <Text size="sm" lineClamp={1}>
                  {group}
                </Text>
                <Badge size="sm" variant="light" color="gray">
                  {notes.length}
                </Badge>
              </Group>
            </Accordion.Control>
            <Accordion.Panel>
              <List size="sm" spacing={6}>
                {notes.map((note, n) => (
                  <List.Item key={n}>{note}</List.Item>
                ))}
              </List>
            </Accordion.Panel>
          </Accordion.Item>
        ))}
      </Accordion>
    </div>
  );
}

export function Empty({ icon, title, children }: { icon: ReactNode; title: string; children?: ReactNode }): ReactElement {
  return (
    <Card withBorder={false} bg="var(--mantine-color-default-hover)" py="xl">
      <Group justify="center" mb="xs" c="dimmed">
        {icon}
      </Group>
      <Text ta="center" fw={600}>
        {title}
      </Text>
      {children && (
        <Text ta="center" size="sm" c="dimmed" mt={4}>
          {children}
        </Text>
      )}
    </Card>
  );
}

const PIPELINE_LABELS: Record<string, string> = {
  search: "Search",
  fields: "Field proposal",
  enrich: "Enrich lead",
  letters: "Write letters",
};

export function pipelineLabel(pipeline: string): string {
  const step = pipeline.split(".").pop() ?? pipeline;
  return PIPELINE_LABELS[step] ?? pipeline;
}
