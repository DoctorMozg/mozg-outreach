import {
  Accordion,
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Code,
  CopyButton,
  Grid,
  Group,
  List,
  Select,
  SegmentedControl,
  Stack,
  Switch,
  Tabs,
  Text,
  Textarea,
  TextInput,
  Timeline,
  Title,
} from "@mantine/core";
import { IconArrowLeft, IconCheck, IconCopy, IconEdit, IconMail, IconSparkles } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactElement, useState } from "react";
import { call, type Draft, get, type ItemDetail, type Run } from "../api";
import { Empty, ErrorBox, Score } from "../components/common";
import { Diff } from "../components/Diff";
import { href } from "../router";
import { Axes, FieldList } from "./Report";

const INTERACTIONS = ["sent", "replied", "called", "met", "declined", "note"] as const;
type InteractionKind = (typeof INTERACTIONS)[number];
const WRITE_LABEL = { company: "Write letters", job: "Write a cover letter", gig: "Write a proposal" } as const;

export function ItemView({ id }: { id: number }): ReactElement {
  const client = useQueryClient();
  const key = ["item", id];
  const item = useQuery({ queryKey: key, queryFn: () => get<ItemDetail>(`/api/items/${id}`) });
  const refresh = (): void => void client.invalidateQueries({ queryKey: key });
  const i = item.data;
  if (!i) return <ErrorBox error={item.error} />;

  return (
    <Stack maw={1400} mx="auto">
      <Anchor href={href.project(i.project_id, "report")} size="sm">
        <Group gap={4}>
          <IconArrowLeft size={14} />
          Back to the report
        </Group>
      </Anchor>
      <Group gap="sm" align="center">
        <Score value={i.score_total} size="xl" />
        <Title order={2}>{i.title}</Title>
        <Badge variant="outline" color="gray">
          {i.kind}
        </Badge>
        <Text size="sm" c="dimmed">
          {i.dated}
        </Text>
      </Group>
      <Grid gap="lg">
        <Grid.Col span={{ base: 12, lg: 7 }}>
          <Stack>
            <Drafts item={i} onSaved={refresh} />
            <History item={i} onSaved={refresh} />
          </Stack>
        </Grid.Col>
        <Grid.Col span={{ base: 12, lg: 5 }}>
          <Stack>
            <Actions item={i} onChange={refresh} />
            {i.score && (
              <Card>
                <Title order={4} mb="sm">
                  Why this score
                </Title>
                {i.score.blockers.length > 0 && (
                  <Group gap={4} mb="sm">
                    {i.score.blockers.map((b) => (
                      <Badge key={b} color="red" variant="light">
                        {b}
                      </Badge>
                    ))}
                  </Group>
                )}
                <Axes item={i} />
              </Card>
            )}
            {i.entities.length > 0 && (
              <Card>
                <Title order={4} mb="sm">
                  People and companies
                </Title>
                <Stack gap={6}>
                  {i.entities.map((e) => (
                    <Group key={`${e.entity_id}-${e.role}`} gap="xs">
                      <Anchor href={href.entity(e.entity_id)} fw={500}>
                        {e.name}
                      </Anchor>
                      <Badge size="xs" variant="light" color={e.kind === "person" ? "teal" : "indigo"}>
                        {e.kind}
                      </Badge>
                      <Text size="sm" c="dimmed">
                        {e.role}
                      </Text>
                    </Group>
                  ))}
                </Stack>
              </Card>
            )}
            <Card>
              <Title order={4} mb="sm">
                Fields
              </Title>
              <FieldList fields={i.fields} />
            </Card>
            {Object.keys(i.details).length > 0 && (
              <Card>
                <Title order={4} mb="sm">
                  Research details
                </Title>
                <Accordion variant="contained">
                  {Object.entries(i.details).map(([name, detail]) => (
                    <Accordion.Item key={name} value={name}>
                      <Accordion.Control>{name.replaceAll("_", " ")}</Accordion.Control>
                      <Accordion.Panel>
                        <Code block>{JSON.stringify(detail, null, 2)}</Code>
                      </Accordion.Panel>
                    </Accordion.Item>
                  ))}
                </Accordion>
              </Card>
            )}
          </Stack>
        </Grid.Col>
      </Grid>
    </Stack>
  );
}

function Actions({ item, onChange }: { item: ItemDetail; onChange: () => void }): ReactElement {
  const select = useMutation({
    mutationFn: (selected: boolean) => call("PATCH", `/api/items/${item.id}`, { selected }),
    onSuccess: onChange,
  });
  const start = useMutation({
    mutationFn: (action: "enrich" | "letters") => call<Run>("POST", `/api/items/${item.id}/${action}`),
    onSuccess: (run) => (window.location.hash = href.run(run.id)),
  });
  return (
    <Card>
      <Group justify="space-between">
        <Switch label="Selected" checked={item.selected} onChange={(e) => select.mutate(e.currentTarget.checked)} />
        <Group gap="xs">
          {item.kind !== "gig" && (
            <Button
              variant="default"
              leftSection={<IconSparkles size={16} />}
              disabled={!item.selected}
              loading={start.isPending && start.variables === "enrich"}
              onClick={() => start.mutate("enrich")}
            >
              Enrich
            </Button>
          )}
          <Button
            leftSection={<IconMail size={16} />}
            disabled={!item.selected}
            loading={start.isPending && start.variables === "letters"}
            onClick={() => start.mutate("letters")}
          >
            {WRITE_LABEL[item.kind]}
          </Button>
        </Group>
      </Group>
      {!item.selected && (
        <Text size="sm" c="dimmed" mt="xs">
          Select the lead to enrich it or draft letters.
        </Text>
      )}
      <ErrorBox error={select.error ?? start.error} />
    </Card>
  );
}

/** One tab per recipient and channel; each holds that thread's versions. */
function Drafts({ item, onSaved }: { item: ItemDetail; onSaved: () => void }): ReactElement {
  const threads = new Map<string, Draft[]>();
  for (const d of item.drafts) {
    const key = `${d.recipient} · ${d.channel}`;
    threads.set(key, [...(threads.get(key) ?? []), d]);
  }
  const names = [...threads.keys()];
  const [tab, setTab] = useState<string | null>(names[0] ?? null);
  if (names.length === 0) {
    return (
      <Card>
        <Empty icon={<IconMail size={32} />} title="No drafts yet">
          {item.selected ? `${WRITE_LABEL[item.kind]} from the panel on the right.` : "Select the lead, then write letters for it."}
        </Empty>
      </Card>
    );
  }
  return (
    <Card>
      <Tabs value={tab && threads.has(tab) ? tab : names[0]!} onChange={setTab}>
        <Tabs.List mb="md">
          {names.map((name) => (
            <Tabs.Tab key={name} value={name} rightSection={<Badge size="xs" variant="light" color="gray">{threads.get(name)!.length}</Badge>}>
              {name}
            </Tabs.Tab>
          ))}
        </Tabs.List>
        {names.map((name) => (
          <Tabs.Panel key={name} value={name}>
            <DraftThread versions={threads.get(name)!} onSaved={onSaved} />
          </Tabs.Panel>
        ))}
      </Tabs>
    </Card>
  );
}

type DraftView = "text" | "naturalized" | "previous";

function DraftThread({ versions, onSaved }: { versions: Draft[]; onSaved: () => void }): ReactElement {
  const latest = versions.at(-1)!;
  const [shown, setShown] = useState(latest.version);
  const [view, setView] = useState<DraftView>("text");
  const [editing, setEditing] = useState(false);
  const draft = versions.find((v) => v.version === shown) ?? latest;
  const previous = versions.find((v) => v.version === draft.version - 1);
  const [subject, setSubject] = useState(draft.subject ?? "");
  const [body, setBody] = useState(draft.body);
  const save = useMutation({
    mutationFn: () => call<Draft>("PUT", `/api/drafts/${draft.id}`, { subject: subject || null, body }),
    onSuccess: (saved) => {
      setShown(saved.version);
      setView("text");
      setEditing(false);
      onSaved();
    },
  });
  const r = draft.review;
  // A hand edit keeps no address of its own; it goes to whoever the version it was based on addressed.
  const addressed = versions.filter((v) => v.version <= draft.version && v.review.address).at(-1)?.review;
  const startEdit = (): void => {
    setSubject(draft.subject ?? "");
    setBody(draft.body);
    setEditing(true);
  };
  const views = [
    { value: "text", label: "Text" },
    ...(r.before_naturalize ? [{ value: "naturalized", label: "Naturalizer changes" }] : []),
    ...(previous ? [{ value: "previous", label: `Changes since v${previous.version}` }] : []),
  ];

  return (
    <Stack>
      <Group justify="space-between">
        <Group gap="xs">
          <Select
            size="xs"
            w={130}
            allowDeselect={false}
            value={String(shown)}
            onChange={(v) => {
              setShown(Number(v));
              setView("text");
              setEditing(false);
            }}
            data={versions.map((v) => ({ value: String(v.version), label: `v${v.version}${v.review.edited_by_user ? " (yours)" : ""}` }))}
          />
          {!editing && views.length > 1 && (
            <SegmentedControl size="xs" value={view} onChange={(v) => setView(v as DraftView)} data={views} />
          )}
        </Group>
        {!editing && (
          <Group gap="xs">
            <Button size="xs" variant="default" leftSection={<IconEdit size={14} />} onClick={startEdit}>
              Edit
            </Button>
            <CopyButton value={draft.subject ? `${draft.subject}\n\n${draft.body}` : draft.body}>
              {({ copied, copy }) => (
                <Button size="xs" color={copied ? "teal" : undefined} leftSection={copied ? <IconCheck size={14} /> : <IconCopy size={14} />} onClick={copy}>
                  {copied ? "Copied" : "Copy"}
                </Button>
              )}
            </CopyButton>
          </Group>
        )}
      </Group>
      {addressed && (
        <Text size="sm">
          To <b>{addressed.address}</b>{" "}
          <Badge size="xs" variant="light" color={addressed.address_confidence === "guessed" ? "orange" : "gray"}>
            {addressed.address_confidence}
          </Badge>
        </Text>
      )}
      {editing ? (
        <Stack gap="sm">
          {draft.subject !== null && <TextInput label="Subject" value={subject} onChange={(e) => setSubject(e.currentTarget.value)} />}
          <Textarea autosize minRows={10} maxRows={30} value={body} onChange={(e) => setBody(e.currentTarget.value)} classNames={{ input: "letter" }} />
          <Group>
            <Button onClick={() => save.mutate()} loading={save.isPending}>
              Save as v{latest.version + 1}
            </Button>
            <Button variant="default" onClick={() => setEditing(false)}>
              Discard
            </Button>
          </Group>
          <ErrorBox error={save.error} />
        </Stack>
      ) : (
        <Card withBorder bg="var(--mantine-color-body)">
          {draft.subject && (
            <Text fw={600} mb="sm">
              {draft.subject}
            </Text>
          )}
          {view === "naturalized" && r.before_naturalize ? (
            <Diff before={r.before_naturalize} after={draft.body} />
          ) : view === "previous" && previous ? (
            <Diff before={previous.body} after={draft.body} />
          ) : (
            <div className="letter">{draft.body}</div>
          )}
        </Card>
      )}
      {r.problems && r.problems.length > 0 && (
        <Alert color="red" variant="light" title="Still breaks the letter rules">
          <List size="sm">
            {r.problems.map((p, n) => (
              <List.Item key={n}>{p}</List.Item>
            ))}
          </List>
        </Alert>
      )}
      {r.review_notes && r.review_notes.length > 0 && (
        <Alert color="yellow" variant="light" title="Review notes">
          <List size="sm">
            {r.review_notes.map((note, n) => (
              <List.Item key={n}>{note}</List.Item>
            ))}
          </List>
        </Alert>
      )}
      {r.hook_used && (
        <Text size="xs" c="dimmed">
          Hook: {r.hook_used} · Angle: {r.angle_used} · Reader: {r.reader_calibration}
          {r.changes?.length ? ` · Naturalizer: ${r.changes.join("; ")}` : ""}
        </Text>
      )}
    </Stack>
  );
}

function History({ item, onSaved }: { item: ItemDetail; onSaved: () => void }): ReactElement {
  const [kind, setKind] = useState<InteractionKind>("sent");
  const [note, setNote] = useState("");
  const log = useMutation({
    mutationFn: () => call("POST", `/api/items/${item.id}/interactions`, { kind, note }),
    onSuccess: () => {
      setNote("");
      onSaved();
    },
  });
  return (
    <Card>
      <Title order={4} mb={4}>
        History
      </Title>
      <Text size="sm" c="dimmed" mb="md">
        Nothing is sent from here. Log what you did yourself.
      </Text>
      {item.interactions.length > 0 && (
        <Timeline bulletSize={14} lineWidth={2} mb="md">
          {item.interactions.map((h) => (
            <Timeline.Item key={h.id} title={h.kind}>
              {h.note && <Text size="sm">{h.note}</Text>}
              <Text size="xs" c="dimmed">
                {new Date(h.happened_at).toLocaleString()}
              </Text>
            </Timeline.Item>
          ))}
        </Timeline>
      )}
      <Group gap="xs" align="flex-end">
        <Select w={130} allowDeselect={false} value={kind} onChange={(v) => v && setKind(v as InteractionKind)} data={[...INTERACTIONS]} />
        <TextInput flex={1} placeholder="Note" value={note} onChange={(e) => setNote(e.currentTarget.value)} />
        <Button variant="light" onClick={() => log.mutate()} loading={log.isPending}>
          Log
        </Button>
      </Group>
      <ErrorBox error={log.error} />
    </Card>
  );
}
