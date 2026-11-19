import {
  Accordion,
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Checkbox,
  Code,
  Group,
  JsonInput,
  List,
  ScrollArea,
  SegmentedControl,
  Stack,
  Stepper,
  Table,
  Text,
  Textarea,
  TextInput,
  Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconArrowLeft, IconCheck, IconHandStop, IconPlayerPlay, IconPlayerStop, IconX } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactElement, useEffect, useRef, useState } from "react";
import { call, type FillReport, get, type Json, type Project, type Run, type RunEvent, type RunStatus } from "../api";
import { Concerns, ErrorBox, pipelineLabel, Status } from "../components/common";
import { href } from "../router";

const OPEN = new Set(["pending", "running", "awaiting_gate"]);
const EVENT_KINDS = [
  "status",
  "phase",
  "gate",
  "gate_answered",
  "agent",
  "strategy",
  "sources",
  "items",
  "scored",
  "collected",
  "screened",
  "stack_checked",
  "brief_review",
  "fields_proposed",
  "draft",
  "drafts",
  "skipped",
  "summary",
  "identified",
  "channels_found",
  "channels_saved",
  "fields_researched",
  "approach",
  "application",
  "sent",
  "entity",
];

const ACTIVE: ReadonlySet<RunStatus> = new Set<RunStatus>(["pending", "running"]);

function useRunEvents(runId: number, onEvent: () => void): RunEvent[] {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const notify = useRef(onEvent);
  notify.current = onEvent;
  useEffect(() => {
    setEvents([]);
    // EventSource resends Last-Event-ID on reconnect, so the server replays only what was missed.
    const source = new EventSource(`/api/runs/${runId}/events`);
    const receive = (message: MessageEvent<string>): void => {
      const event = JSON.parse(message.data) as RunEvent;
      setEvents((seen) => (seen.some((e) => e.seq === event.seq) ? seen : [...seen, event]));
      if (event.kind !== "agent") notify.current();
    };
    for (const kind of EVENT_KINDS) source.addEventListener(kind, receive);
    return () => source.close();
  }, [runId]);
  return events;
}

function words(name: string): string {
  const text = name.replaceAll("_", " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function RunView({ id }: { id: number }): ReactElement {
  const client = useQueryClient();
  const run = useQuery({
    queryKey: ["run", id],
    queryFn: () => get<Run>(`/api/runs/${id}`),
    // The event stream drives updates; polling covers a stream the browser silently stalls.
    refetchInterval: (query) => (ACTIVE.has(query.state.data?.status ?? "pending") ? 5000 : false),
  });
  const pipelines = useQuery({ queryKey: ["pipelines"], queryFn: () => get<Record<string, string[]>>("/api/pipelines"), staleTime: Infinity });
  const projectId = run.data?.project_id;
  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => get<Project>(`/api/projects/${projectId}`),
    enabled: projectId !== undefined,
  });
  const refresh = (): void => void client.invalidateQueries({ queryKey: ["run", id] });
  const events = useRunEvents(id, refresh);
  const searched = run.data && !OPEN.has(run.data.status) && typeof run.data.state.entity_id === "number" ? run.data.state.entity_id : null;
  useEffect(() => {
    if (searched === null) return;
    // A finished search rewrote the profile and its links; cached copies would show the old ones.
    for (const queryKey of [["entity"], ["entities"], ["graph"]]) void client.invalidateQueries({ queryKey });
  }, [client, searched]);
  const cancel = useMutation({ mutationFn: () => call<Run>("POST", `/api/runs/${id}/cancel`), onSuccess: refresh });
  const resume = useMutation({
    mutationFn: () => call<Run>("POST", `/api/runs/${id}/resume`),
    onSuccess: () => {
      refresh();
      notifications.show({ color: "indigo", message: `Run ${id} resumed` });
    },
  });
  const r = run.data;
  if (!r) return <ErrorBox error={run.error} />;
  const itemId = typeof r.state.item_id === "number" ? r.state.item_id : null;
  const entityId = typeof r.state.entity_id === "number" ? r.state.entity_id : null;
  const stopped = r.status === "failed" || r.status === "cancelled";

  return (
    <Stack maw={1200} mx="auto">
      <Group gap="md">
        <Anchor href={href.project(r.project_id, "runs")} size="sm">
          <Group gap={4}>
            <IconArrowLeft size={14} />
            {project.data?.name ?? "Project"}
          </Group>
        </Anchor>
        {entityId === null && (
          <Anchor href={href.project(r.project_id, "report")} size="sm">
            Report
          </Anchor>
        )}
        {itemId !== null && (
          <Anchor href={href.item(itemId)} size="sm">
            Lead
          </Anchor>
        )}
        {entityId !== null && (
          <Anchor href={href.entity(entityId)} size="sm">
            Profile
          </Anchor>
        )}
      </Group>
      <Group justify="space-between" align="flex-start">
        <div>
          <Group gap="sm">
            <Title order={2}>
              {pipelineLabel(r.pipeline)} #{r.id}
            </Title>
            <Status status={r.status} />
          </Group>
          <Text size="sm" c="dimmed">
            Started {new Date(r.started_at).toLocaleString()}
            {r.finished_at && ` · ended ${new Date(r.finished_at).toLocaleString()}`} · ${r.cost_usd.toFixed(2)} of usage
          </Text>
        </div>
        <Group gap="xs">
          {OPEN.has(r.status) && (
            <Button variant="default" leftSection={<IconPlayerStop size={16} />} onClick={() => cancel.mutate()} loading={cancel.isPending}>
              Cancel
            </Button>
          )}
          {stopped && (
            <Button leftSection={<IconPlayerPlay size={16} />} onClick={() => resume.mutate()} loading={resume.isPending}>
              Resume from {words(r.phase ?? "start").toLowerCase()}
            </Button>
          )}
        </Group>
      </Group>

      <Progress run={r} phases={pipelines.data?.[r.pipeline] ?? []} />
      {r.error && (
        <Alert color="red" variant="light" icon={<IconX size={18} />} title={r.status === "failed" ? "The run failed" : "Stopped"}>
          {r.error}
        </Alert>
      )}
      <ErrorBox error={cancel.error ?? resume.error} />

      {r.status === "awaiting_gate" && r.pending_gate && <Gate run={r} gate={r.pending_gate} onDone={refresh} />}
      {r.pipeline.endsWith(".fields") && r.status === "complete" && <FieldsDone run={r} />}
      {isSummary(r.state.summary) && <SummaryView summary={r.state.summary} />}
      {r.state.concerns && r.state.concerns.length > 0 && (
        <Card>
          <Concerns items={r.state.concerns} />
        </Card>
      )}
      <Activity events={events} />
    </Stack>
  );
}

/** The flow's phases as steps; the current one shows running, waiting or failed. */
function Progress({ run, phases }: { run: Run; phases: string[] }): ReactElement | null {
  if (phases.length < 2) return null;
  const at = run.phase ? phases.indexOf(run.phase) : 0;
  const active = run.status === "complete" ? phases.length : Math.max(at, 0);
  return (
    <Card py="md">
      <ScrollArea type="auto" offsetScrollbars>
        <Stepper active={active} size="xs" miw={phases.length * 110} allowNextStepsSelect={false}>
          {phases.map((phase, n) => {
            const current = n === active;
            const failed = current && (run.status === "failed" || run.status === "cancelled");
            const waiting = current && run.status === "awaiting_gate";
            return (
              <Stepper.Step
                key={phase}
                label={words(phase)}
                description={failed ? run.status : waiting ? "needs you" : undefined}
                loading={current && run.status === "running"}
                color={failed ? "red" : waiting ? "yellow" : undefined}
                icon={failed ? <IconX size={14} /> : waiting ? <IconHandStop size={14} /> : undefined}
                completedIcon={<IconCheck size={14} />}
              />
            );
          })}
        </Stepper>
      </ScrollArea>
    </Card>
  );
}

function describe(e: RunEvent): string {
  const p = e.payload;
  if (e.kind === "agent") return `${String(p.agent)} ${String(p.kind)}${p.detail ? `: ${String(p.detail).slice(0, 200)}` : ""}`;
  if (e.kind === "phase") return words(String(p.phase));
  if (e.kind === "gate") return `waiting for your ${String(p.kind)} approval`;
  if (e.kind === "status") return `${String(p.status)}${p.error ? `: ${String(p.error)}` : ""}`;
  if (e.kind === "entity") {
    const removed = Array.isArray(p.removed) ? p.removed.map(String) : [];
    const notes = Array.isArray(p.notes) ? p.notes.map(String) : [];
    return [removed.length ? `removed ${removed.join(", ")}` : "nothing removed", `${String(p.people)} people linked`, ...notes].join(" · ");
  }
  // Payloads can carry whole lists (a strategy's concerns); the log names their size, not their content.
  return Object.entries(p)
    .filter(([, v]) => v !== null && (typeof v !== "object" || Array.isArray(v)))
    .map(([k, v]) => `${k.replaceAll("_", " ")}: ${Array.isArray(v) ? v.length : String(v).slice(0, 120)}`)
    .join(" · ");
}

function Activity({ events }: { events: RunEvent[] }): ReactElement {
  const [shown, setShown] = useState<"steps" | "all">("steps");
  const visible = shown === "all" ? events : events.filter((e) => e.kind !== "agent");
  return (
    <Card>
      <Group justify="space-between" mb="sm">
        <Title order={4}>Activity</Title>
        <SegmentedControl
          size="xs"
          value={shown}
          onChange={(v) => setShown(v === "all" ? "all" : "steps")}
          data={[
            { value: "steps", label: "Steps" },
            { value: "all", label: `Everything (${events.length})` },
          ]}
        />
      </Group>
      <ScrollArea.Autosize mah={420}>
        <Table verticalSpacing={4} fz="sm">
          <Table.Tbody>
            {visible.map((e) => (
              <Table.Tr key={e.seq}>
                <Table.Td w={90} c="dimmed" style={{ fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}>
                  {new Date(e.created_at).toLocaleTimeString()}
                </Table.Td>
                <Table.Td w={140}>
                  <Badge size="sm" variant="light" color={e.kind === "agent" ? "gray" : e.kind === "gate" ? "yellow" : "indigo"}>
                    {e.kind.replaceAll("_", " ")}
                  </Badge>
                </Table.Td>
                <Table.Td>{describe(e)}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </ScrollArea.Autosize>
    </Card>
  );
}

type RunSummary = {
  summary: string;
  patterns: { pattern: string; item_ids: number[] }[];
  recommendations: string[];
};

function isSummary(value: unknown): value is RunSummary {
  return typeof value === "object" && value !== null && typeof (value as RunSummary).summary === "string";
}

function SummaryView({ summary }: { summary: RunSummary }): ReactElement {
  return (
    <Card>
      <Title order={4} mb="sm">
        Run summary
      </Title>
      <Stack gap="sm">
        {summary.summary.split(/\n\s*\n/).map((paragraph, n) => (
          <Text key={n}>{paragraph}</Text>
        ))}
        {summary.patterns.length > 0 && (
          <div>
            <Text fw={600} mb={4}>
              Patterns
            </Text>
            <List spacing={6}>
              {summary.patterns.map((p, n) => (
                <List.Item key={n}>
                  {p.pattern}{" "}
                  {p.item_ids.map((itemId) => (
                    <Anchor key={itemId} href={href.item(itemId)} size="sm" mr={6}>
                      #{itemId}
                    </Anchor>
                  ))}
                </List.Item>
              ))}
            </List>
          </div>
        )}
        {summary.recommendations.length > 0 && (
          <div>
            <Text fw={600} mb={4}>
              Recommendations
            </Text>
            <List type="ordered" spacing={6}>
              {summary.recommendations.map((rec, n) => (
                <List.Item key={n}>{rec}</List.Item>
              ))}
            </List>
          </div>
        )}
      </Stack>
    </Card>
  );
}

type BriefReview = { complete?: boolean; gaps?: { field: string; question: string }[]; suggestions?: string[] };

function FieldsDone({ run }: { run: Run }): ReactElement {
  const review = run.state.review as BriefReview | null;
  return (
    <Card>
      <Group justify="space-between" mb="sm">
        <Title order={4}>Brief review</Title>
        <Button component="a" href={href.project(run.project_id, "fields")}>
          Review the proposed fields
        </Button>
      </Group>
      <Stack gap="sm">
        {review?.gaps?.length ? (
          <List spacing={6}>
            {review.gaps.map((g) => (
              <List.Item key={g.field}>
                <Text span fw={600}>
                  {g.field}
                </Text>
                : {g.question}
              </List.Item>
            ))}
          </List>
        ) : (
          <Text>The brief has what the search needs.</Text>
        )}
        {review?.suggestions?.map((s, n) => (
          <Text key={n} size="sm" c="dimmed">
            {s}
          </Text>
        ))}
        {run.state.rationale ? <Text size="sm">{String(run.state.rationale)}</Text> : null}
      </Stack>
    </Card>
  );
}

type GateProps = { run: Run; gate: NonNullable<Run["pending_gate"]>; onDone: () => void };

const GATE_TITLES: Record<string, string> = {
  channels: "Approve the Telegram channels",
  application: "Review and submit the application",
};
const FILL_SOURCE = { profile: "Your details", cv: "CV file", letter: "Cover letter", answerer: "Answered by AI", page: "Already on the form" } as const;

function Gate({ run, gate, onDone }: GateProps): ReactElement {
  return (
    <Card style={{ borderColor: "var(--mantine-color-yellow-filled)", borderWidth: 2 }}>
      <Group gap="xs" mb="md">
        <IconHandStop size={20} color="var(--mantine-color-yellow-filled)" />
        <Title order={4}>{GATE_TITLES[gate.kind] ?? `Approve the ${gate.kind}`}</Title>
      </Group>
      {gate.kind === "channels" ? (
        <ChannelGate run={run} gate={gate} onDone={onDone} />
      ) : gate.kind === "application" ? (
        <ApplicationGate run={run} gate={gate} onDone={onDone} />
      ) : (
        <StrategyGate run={run} gate={gate} onDone={onDone} />
      )}
    </Card>
  );
}

/** The form waits in a Chrome window; the run ends when the user submits there or says they won't. */
function ApplicationGate({ run, gate, onDone }: GateProps): ReactElement {
  const report = gate.payload.application as FillReport | null;
  const [reason, setReason] = useState("");
  const answer = useMutation({
    mutationFn: (body: Json) => call<Run>("POST", `/api/runs/${run.id}/gate`, body),
    onSuccess: onDone,
  });
  const missing = report?.for_user.filter((f) => f.required) ?? [];
  const reasoned = report?.filled.filter((f) => f.reasoning) ?? [];
  return (
    <Stack>
      <Text size="sm">
        The form is open in the Chrome window. Check every answer, fill what is left, and click Submit there yourself. When the
        confirmation page appears the lead is marked as sent.
      </Text>
      {report && (
        <>
          <Text size="sm" c="dimmed">
            {report.ats} form at{" "}
            <Anchor href={report.url} target="_blank" rel="noreferrer">
              {report.url}
            </Anchor>
          </Text>
          {reasoned.length > 0 && (
            <Alert color="indigo" title={`Check these (${reasoned.length}): the AI reasoned them, no stated fact did`}>
              <List size="sm">
                {reasoned.map((f, n) => (
                  <List.Item key={n}>
                    <Text span fw={600} size="sm">
                      {f.label}
                    </Text>
                    <Text span size="sm">
                      {" "}
                      : {f.value?.replaceAll("\n", ", ")}
                    </Text>
                    <Text c="dimmed" size="xs">
                      {f.reasoning}
                    </Text>
                  </List.Item>
                ))}
              </List>
            </Alert>
          )}
          {report.for_user.length > 0 && (
            <Alert color={missing.length ? "yellow" : "gray"} title={`Left for you (${report.for_user.length}, ${missing.length} required)`}>
              <List size="sm">
                {report.for_user.map((f, n) => (
                  <List.Item key={n}>
                    <Text span fw={f.required ? 600 : 400} size="sm">
                      {f.label || "Unlabelled field"}
                    </Text>
                    <Text span c="dimmed" size="sm">
                      {" "}
                      : {f.reason}
                    </Text>
                  </List.Item>
                ))}
              </List>
            </Alert>
          )}
          {report.filled.length > 0 && (
            <Accordion variant="contained">
              <Accordion.Item value="filled">
                <Accordion.Control>Filled ({report.filled.length})</Accordion.Control>
                <Accordion.Panel>
                  <List size="sm">
                    {report.filled.map((f, n) => (
                      <List.Item key={n}>
                        {f.label}{" "}
                        <Badge size="xs" variant="light">
                          {FILL_SOURCE[f.source]}
                        </Badge>
                        {f.value && (
                          <Text span c="dimmed" size="sm">
                            {" "}
                            {f.value.replaceAll("\n", ", ")}
                          </Text>
                        )}
                      </List.Item>
                    ))}
                  </List>
                </Accordion.Panel>
              </Accordion.Item>
            </Accordion>
          )}
        </>
      )}
      <Group align="flex-end">
        <Button leftSection={<IconCheck size={16} />} loading={answer.isPending} onClick={() => answer.mutate({ approve: true })}>
          I submitted it
        </Button>
        <TextInput placeholder="Why not (optional)" value={reason} onChange={(e) => setReason(e.currentTarget.value)} />
        <Button variant="default" loading={answer.isPending} onClick={() => answer.mutate({ approve: false, feedback: reason || null })}>
          Not sending
        </Button>
      </Group>
      <ErrorBox error={answer.error} />
    </Stack>
  );
}

interface FoundChannel {
  username: string;
  title: string;
  subscribers: number | null;
  last_post: string;
  job_share: number;
  skill_share: number | null;
  query: string;
}

function ChannelGate({ run, gate, onDone }: GateProps): ReactElement {
  const offered = (gate.payload.found as { channels: FoundChannel[] } | null)?.channels ?? [];
  const [picked, setPicked] = useState(() => new Set(offered.map((c) => c.username)));
  const answer = useMutation({
    mutationFn: (body: Json) => call<Run>("POST", `/api/runs/${run.id}/gate`, body),
    onSuccess: onDone,
  });
  const toggle = (username: string): void =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (!next.delete(username)) next.add(username);
      return next;
    });
  const approve = (): void => answer.mutate({ approve: true, revised: { channels: offered.filter((c) => picked.has(c.username)) } });
  return (
    <Stack>
      <Text size="sm" c="dimmed">
        Found by searching Telegram for the strategy's skills. Checked channels are read from now on; unchecked ones are never offered again.
      </Text>
      <Table.ScrollContainer minWidth={700}>
        <Table highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th w={40} />
              <Table.Th>Channel</Table.Th>
              <Table.Th ta="right">Subscribers</Table.Th>
              <Table.Th ta="right">Job posts</Table.Th>
              <Table.Th ta="right">On your skills</Table.Th>
              <Table.Th>Last post</Table.Th>
              <Table.Th>Found by</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {offered.map((c) => (
              <Table.Tr key={c.username}>
                <Table.Td>
                  <Checkbox checked={picked.has(c.username)} onChange={() => toggle(c.username)} aria-label={`Use @${c.username}`} />
                </Table.Td>
                <Table.Td>
                  <Anchor href={`https://t.me/${c.username}`} target="_blank" rel="noreferrer">
                    @{c.username}
                  </Anchor>{" "}
                  <Text span c="dimmed" size="sm">
                    {c.title}
                  </Text>
                </Table.Td>
                <Table.Td ta="right">{c.subscribers ?? "?"}</Table.Td>
                <Table.Td ta="right">{Math.round(c.job_share * 100)}%</Table.Td>
                <Table.Td ta="right">{c.skill_share == null ? "?" : `${Math.round(c.skill_share * 100)}%`}</Table.Td>
                <Table.Td>{c.last_post.slice(0, 10)}</Table.Td>
                <Table.Td c="dimmed">{c.query}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      <Group>
        <Button onClick={approve} loading={answer.isPending}>
          Use {picked.size} of {offered.length} channels
        </Button>
      </Group>
      <ErrorBox error={answer.error} />
    </Stack>
  );
}

function StrategyGate({ run, gate, onDone }: GateProps): ReactElement {
  const draft = gate.payload.strategy as Json | null;
  const original = JSON.stringify(draft, null, 2);
  const [text, setText] = useState(original);
  const [feedback, setFeedback] = useState("");
  const [editing, setEditing] = useState(false);
  const answer = useMutation({
    mutationFn: (body: Json) => call<Run>("POST", `/api/runs/${run.id}/gate`, body),
    onSuccess: onDone,
  });
  const edited = editing && text !== original;
  const approve = (): void => answer.mutate(edited ? { approve: true, revised: JSON.parse(text) as Json } : { approve: true });
  return (
    <Stack>
      <Text size="sm" c="dimmed">
        Nothing is searched until you approve. Approve as is, edit it, or send it back with notes.
      </Text>
      {draft && !editing && <StrategySummary strategy={draft} />}
      {editing && <JsonInput autosize minRows={12} maxRows={30} formatOnBlur validationError="Not valid JSON" value={text} onChange={setText} />}
      <Group>
        <Button leftSection={<IconCheck size={16} />} onClick={approve} loading={answer.isPending}>
          {edited ? "Approve my edits" : "Approve"}
        </Button>
        <Button variant="default" onClick={() => setEditing(!editing)}>
          {editing ? "Show summary" : "Edit as JSON"}
        </Button>
      </Group>
      <Textarea label="Or send it back with notes" autosize minRows={2} value={feedback} onChange={(e) => setFeedback(e.currentTarget.value)} placeholder="What should change?" />
      <Group>
        <Button variant="light" disabled={!feedback.trim()} loading={answer.isPending} onClick={() => answer.mutate({ approve: false, feedback })}>
          Revise with notes
        </Button>
      </Group>
      <ErrorBox error={answer.error} />
    </Stack>
  );
}

function StrategySummary({ strategy }: { strategy: Json }): ReactElement {
  // Strategies differ per mode, so show top-level fields generically: text, tags, or JSON.
  return (
    <Table variant="vertical" layout="fixed" withTableBorder>
      <Table.Tbody>
        {Object.entries(strategy).map(([key, value]) => (
          <Table.Tr key={key}>
            <Table.Th w={200} style={{ verticalAlign: "top" }}>
              {words(key)}
            </Table.Th>
            <Table.Td>
              {typeof value === "string" || typeof value === "number" || typeof value === "boolean" ? (
                <Text size="sm">{String(value)}</Text>
              ) : Array.isArray(value) && value.every((v) => typeof v === "string") ? (
                <Group gap={6}>
                  {value.map((v) => (
                    <Badge key={v} variant="light" color="gray" tt="none" fw={500} style={{ whiteSpace: "normal", height: "auto" }}>
                      {v}
                    </Badge>
                  ))}
                </Group>
              ) : (
                <Code block>{JSON.stringify(value, null, 2)}</Code>
              )}
            </Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  );
}
