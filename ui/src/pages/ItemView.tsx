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
  Tooltip,
} from "@mantine/core";
import { IconArrowLeft, IconBrandGmail, IconCheck, IconCopy, IconDownload, IconEdit, IconForms, IconMail, IconRefresh, IconRoute, IconSparkles } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactElement, useState } from "react";
import {
  call,
  type Cv,
  type CvChoice,
  type Draft,
  enrichHint,
  get,
  type ItemDetail,
  type MailCheck,
  type MailStatus,
  type MailThread,
  type PlanContact,
  type PlannedTouch,
  type Run,
  type StoredApproach,
} from "../api";
import { ActivityBadge, Empty, ErrorBox, INTERACTION_LABELS, type InteractionKind, interactionLabel, Score, StepBadge } from "../components/common";
import { Diff } from "../components/Diff";
import { href } from "../router";
import { Axes, FieldList } from "./Report";

// A company's letters come from its approach plan: enrichment and Re-plan write them.
const WRITE_LABEL = { job: "Write a cover letter", gig: "Write a proposal" } as const;

/** Drafts of one letter share a tab: same approach step, recipient and channel. */
const draftKey = (d: Draft): string => `${d.step ?? "-"}|${d.recipient}|${d.channel}`;

export function ItemView({ id }: { id: number }): ReactElement {
  const client = useQueryClient();
  const key = ["item", id];
  const item = useQuery({ queryKey: key, queryFn: () => get<ItemDetail>(`/api/items/${id}`) });
  const refresh = (): void => void client.invalidateQueries({ queryKey: key });
  const [draftTab, setDraftTab] = useState<string | null>(null);
  const openDraft = (key: string): void => {
    setDraftTab(key);
    document.getElementById("drafts")?.scrollIntoView({ behavior: "smooth" });
  };
  const i = item.data;
  if (!i) return <ErrorBox error={item.error} />;
  // The CV pick and the approach plan have their own cards; every other section is shown raw.
  const research = Object.entries(i.details).filter(([name]) => name !== "cv_choice" && name !== "approach");

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
        <ActivityBadge activity={i.activity} projectId={i.project_id} size="lg" />
        <StepBadge step={i.approach_step} size="lg" />
        <Text size="sm" c="dimmed">
          {i.dated}
        </Text>
      </Group>
      <Grid gap="lg">
        <Grid.Col span={{ base: 12, lg: 7 }}>
          <Stack>
            {i.kind === "company" && i.enriched && <Approach item={i} onChange={refresh} onOpenDraft={openDraft} />}
            <Drafts item={i} onSaved={refresh} tab={draftTab} onTab={setDraftTab} />
            <History item={i} onSaved={refresh} />
          </Stack>
        </Grid.Col>
        <Grid.Col span={{ base: 12, lg: 5 }}>
          <Stack>
            <Actions item={i} onChange={refresh} />
            {i.kind !== "company" && <CvPick item={i} onChange={refresh} />}
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
            {research.length > 0 && (
              <Card>
                <Title order={4} mb="sm">
                  Research details
                </Title>
                <Accordion variant="contained">
                  {research.map(([name, detail]) => (
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

const CHANNEL_LABEL = { email: "Email", linkedin: "LinkedIn", phone: "Call" } as const;
const PURPOSE_COLOR = { open: "teal", follow_up: "indigo", breakup: "gray" } as const;

/** The plan from enrichment: who hears first, through which route, and the follow-ups after. */
interface ApproachProps {
  item: ItemDetail;
  onChange: () => void;
  onOpenDraft: (key: string) => void;
}

function Approach({ item, onChange, onOpenDraft }: ApproachProps): ReactElement {
  const approach = (item.details as { approach?: StoredApproach }).approach ?? null;
  const mark = useMutation({
    mutationFn: ({ step, done }: { step: number; done: boolean }) =>
      call(done ? "POST" : "DELETE", `/api/items/${item.id}/approach/steps/${step}/done`, done ? { on: null } : undefined),
    onSuccess: onChange,
  });
  const replan = useMutation({
    mutationFn: () => call<Run>("POST", `/api/items/${item.id}/approach`),
    onSuccess: (run) => (window.location.hash = href.run(run.id)),
  });
  const button = (
    <Button
      size="xs"
      variant="default"
      leftSection={<IconRoute size={14} />}
      disabled={!item.selected}
      loading={replan.isPending}
      onClick={() => replan.mutate()}
    >
      {approach ? "Re-plan" : "Plan the approach"}
    </Button>
  );
  if (!approach) {
    return (
      <Card>
        <Group justify="space-between">
          <Title order={4}>Approach</Title>
          {button}
        </Group>
        <Text size="sm" c="dimmed" mt="xs">
          No plan yet. Enrichment plans one; this plans the approach and writes its letters.
        </Text>
        <ErrorBox error={replan.error} />
      </Card>
    );
  }
  const { plan } = approach;
  const contacts = new Map(approach.contacts.map((c) => [c.ref, c]));
  const done = new Map((approach.done ?? []).map((d) => [d.step, d.on]));
  const current = plan.touches.find((t) => !done.has(t.step))?.step ?? null;
  return (
    <Card>
      <Group justify="space-between" mb="xs">
        <Title order={4}>Approach</Title>
        <Group gap="xs">
          <Text size="xs" c="dimmed">
            planned {approach.planned_on}
          </Text>
          {button}
        </Group>
      </Group>
      <Text size="sm">{plan.summary}</Text>
      <Text size="sm" c="dimmed" mt={4}>
        {plan.rationale}
      </Text>
      {plan.touches.length > 0 ? (
        <Timeline mt="md" bulletSize={22} lineWidth={2}>
          {plan.touches.map((t) => {
            const doneOn = done.get(t.step);
            const letter = item.drafts.find((d) => d.step === t.step);
            return (
              <Timeline.Item
                key={t.step}
                color={doneOn ? "teal" : t.step === current ? "blue" : "gray"}
                bullet={doneOn ? <IconCheck size={12} /> : <Text size="xs">{t.step}</Text>}
                title={<TouchTitle touch={t} contacts={contacts} />}
              >
                <TouchBody touch={t} approach={approach} />
                <Group gap="xs" mt={4}>
                  {letter && (
                    <Button size="compact-xs" variant="subtle" leftSection={<IconMail size={12} />} onClick={() => onOpenDraft(draftKey(letter))}>
                      Letter
                    </Button>
                  )}
                  {doneOn ? (
                    <>
                      <Badge size="xs" color="teal" variant="light">
                        done {doneOn}
                      </Badge>
                      <Button size="compact-xs" variant="subtle" color="gray" loading={mark.isPending && mark.variables?.step === t.step} onClick={() => mark.mutate({ step: t.step, done: false })}>
                        Undo
                      </Button>
                    </>
                  ) : (
                    <Tooltip label="Logs it on the lead; a letter Gmail saw sent is not logged twice">
                      <Button size="compact-xs" variant={t.step === current ? "light" : "subtle"} leftSection={<IconCheck size={12} />} loading={mark.isPending && mark.variables?.step === t.step} onClick={() => mark.mutate({ step: t.step, done: true })}>
                        Mark done
                      </Button>
                    </Tooltip>
                  )}
                </Group>
              </Timeline.Item>
            );
          })}
        </Timeline>
      ) : (
        <Text size="sm" c="orange" mt="sm">
          The plan has no usable touches.
        </Text>
      )}
      {plan.avoid.length > 0 && (
        <>
          <Text size="sm" fw={500} mt="md">
            Avoid
          </Text>
          <List size="sm">
            {plan.avoid.map((a) => (
              <List.Item key={a}>{a}</List.Item>
            ))}
          </List>
        </>
      )}
      {plan.concerns.length > 0 && (
        <Alert color="yellow" mt="md" p="xs">
          <List size="sm">
            {plan.concerns.map((c) => (
              <List.Item key={c}>{c}</List.Item>
            ))}
          </List>
        </Alert>
      )}
      <ErrorBox error={replan.error ?? mark.error} />
    </Card>
  );
}

function TouchTitle({ touch, contacts }: { touch: PlannedTouch; contacts: Map<string, PlanContact> }): ReactElement {
  const contact = contacts.get(touch.contact_ref);
  const route = contact?.routes.find((r) => r.ref === touch.route_ref);
  const reader = touch.addressed_to ? contacts.get(touch.addressed_to) : contact;
  const who = reader?.name ? `${reader.name}, ${reader.title}` : "the company";
  return (
    <Group gap={6} wrap="wrap">
      <Text size="sm" fw={500}>
        Day {touch.day}
      </Text>
      <Badge size="xs" variant="light" color={PURPOSE_COLOR[touch.purpose]}>
        {touch.purpose.replace("_", "-")}
      </Badge>
      <Text size="sm">
        {route ? CHANNEL_LABEL[route.channel] : touch.route_ref} to {who}
      </Text>
      {route && (
        <Text size="xs" c={route.confidence === "guessed" ? "orange" : "dimmed"}>
          {route.address}
          {route.confidence === "guessed" && " (guessed)"}
          {route.confidence === "shared_inbox" && touch.addressed_to && " (shared inbox)"}
        </Text>
      )}
    </Group>
  );
}

function TouchBody({ touch, approach }: { touch: PlannedTouch; approach: StoredApproach }): ReactElement {
  const hook = approach.hooks.find((h) => h.ref === touch.hook_ref)?.hook;
  return (
    <Stack gap={2} mt={2}>
      <Text size="sm">
        <Text span c="dimmed">
          Angle:{" "}
        </Text>
        {touch.angle}
      </Text>
      {hook && (
        <Text size="sm">
          <Text span c="dimmed">
            Hook:{" "}
          </Text>
          {hook.source_url ? (
            <Anchor href={hook.source_url} target="_blank" size="sm">
              {hook.signal}
            </Anchor>
          ) : (
            hook.signal
          )}
        </Text>
      )}
      <Text size="sm">
        <Text span c="dimmed">
          Ask:{" "}
        </Text>
        {touch.ask}
      </Text>
      {touch.talking_points && (
        <Text size="sm" c="dimmed">
          {touch.talking_points}
        </Text>
      )}
    </Stack>
  );
}

/** The CV this job or gig gets: picked by the ratings at the first letter, or by the user. */
function CvPick({ item, onChange }: { item: ItemDetail; onChange: () => void }): ReactElement | null {
  const cvs = useQuery({ queryKey: ["cvs"], queryFn: () => get<Cv[]>("/api/cvs") });
  const choice = (item.details as { cv_choice?: CvChoice }).cv_choice ?? null;
  const pick = useMutation({
    mutationFn: (cvId: number | null) => call("PUT", `/api/items/${item.id}/cv`, { cv_id: cvId }),
    onSuccess: onChange,
  });
  if (!cvs.data?.length) return null;
  const picked = cvs.data.find((c) => c.id === choice?.cv_id);
  const how = { ratings: "picked by the ratings", only: "your only CV", user: "your pick" } as const;
  return (
    <Card>
      <Group justify="space-between" mb="xs">
        <Title order={4}>CV to send</Title>
        {picked && (
          <Button size="xs" variant="default" component="a" href={`/api/cvs/${picked.id}/pdf`} leftSection={<IconDownload size={14} />}>
            {picked.filename}
          </Button>
        )}
      </Group>
      <Group gap="xs" align="center">
        <Select
          w={260}
          placeholder="Picked when the letter is written"
          data={cvs.data.map((c) => ({ value: String(c.id), label: c.name }))}
          value={picked ? String(picked.id) : null}
          onChange={(v) => pick.mutate(v === null ? null : Number(v))}
          clearable
        />
        {choice && picked && (
          <Text size="sm" c="dimmed">
            {how[choice.picked_by]}
          </Text>
        )}
      </Group>
      {choice && choice.ranked.length > 0 && (
        <Stack gap={4} mt="sm">
          {choice.ranked.map((r) => (
            <Text key={r.cv_id} size="sm">
              <Badge size="xs" variant="light" color={r.cv_id === choice.cv_id ? "teal" : "gray"} mr={6}>
                {r.total === null ? "n/a" : r.total.toFixed(0)}
              </Badge>
              {r.name}
              {!r.in_listing_language && choice.listing_language && (
                <Text span c="orange" size="xs">
                  {" "}
                  not in {choice.listing_language}
                </Text>
              )}
              {r.reason && (
                <Text span c="dimmed" size="xs">
                  {" "}
                  {r.reason}
                </Text>
              )}
            </Text>
          ))}
        </Stack>
      )}
      <Text size="xs" c="dimmed" mt="xs">
        Clearing the pick lets the next letter pick again.
      </Text>
      <ErrorBox error={cvs.error ?? pick.error} />
    </Card>
  );
}

function Actions({ item, onChange }: { item: ItemDetail; onChange: () => void }): ReactElement {
  const select = useMutation({
    mutationFn: (selected: boolean) => call("PATCH", `/api/items/${item.id}`, { selected }),
    onSuccess: onChange,
  });
  const start = useMutation({
    mutationFn: (action: "enrich" | "letters" | "apply") => call<Run>("POST", `/api/items/${item.id}/${action}`),
    onSuccess: (run) => (window.location.hash = href.run(run.id)),
  });
  const enrich = enrichHint(item);
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
              {enrich.label}
            </Button>
          )}
          {item.kind !== "company" && (
            <Button
              leftSection={<IconMail size={16} />}
              disabled={!item.selected}
              loading={start.isPending && start.variables === "letters"}
              onClick={() => start.mutate("letters")}
            >
              {WRITE_LABEL[item.kind]}
            </Button>
          )}
          {item.kind === "job" && (
            <Tooltip label="Fills the application form in a Chrome window; you review it and click Submit there">
              <Button
                variant="default"
                leftSection={<IconForms size={16} />}
                disabled={!item.selected}
                loading={start.isPending && start.variables === "apply"}
                onClick={() => start.mutate("apply")}
              >
                Fill application
              </Button>
            </Tooltip>
          )}
        </Group>
      </Group>
      {!item.selected && (
        <Text size="sm" c="dimmed" mt="xs">
          Select the lead to enrich it or draft letters.
        </Text>
      )}
      {enrich.note && item.kind !== "gig" && (
        <Text size="sm" c="dimmed" mt="xs">
          {enrich.note}
        </Text>
      )}
      <ErrorBox error={select.error ?? start.error} />
    </Card>
  );
}

/** One tab per recipient and channel; each holds that thread's versions. */
interface DraftsProps {
  item: ItemDetail;
  onSaved: () => void;
  tab: string | null;
  onTab: (key: string | null) => void;
}

function Drafts({ item, onSaved, tab, onTab }: DraftsProps): ReactElement {
  const threads = new Map<string, Draft[]>();
  for (const d of item.drafts) threads.set(draftKey(d), [...(threads.get(draftKey(d)) ?? []), d]);
  const names = [...threads.keys()];
  if (names.length === 0) {
    const how = item.kind === "company" ? "Enrich the company: its approach plan writes a letter for each step." : `${WRITE_LABEL[item.kind]} from the panel on the right.`;
    return (
      <Card>
        <Empty icon={<IconMail size={32} />} title="No drafts yet">
          {item.selected ? how : "Select the lead, then write letters for it."}
        </Empty>
      </Card>
    );
  }
  const label = (d: Draft): string => [d.step && `${d.step}.`, d.recipient, "·", d.channel].filter(Boolean).join(" ");
  return (
    <Card id="drafts">
      <Tabs value={tab && threads.has(tab) ? tab : names[0]!} onChange={onTab}>
        <Tabs.List mb="md">
          {names.map((name) => (
            <Tabs.Tab key={name} value={name} rightSection={<Badge size="xs" variant="light" color="gray">{threads.get(name)!.length}</Badge>}>
              {label(threads.get(name)![0]!)}
            </Tabs.Tab>
          ))}
        </Tabs.List>
        {names.map((name) => (
          <Tabs.Panel key={name} value={name}>
            <DraftThread versions={threads.get(name)!} mail={item.mail} onSaved={onSaved} />
          </Tabs.Panel>
        ))}
      </Tabs>
    </Card>
  );
}

type DraftView = "text" | "naturalized" | "previous";

function DraftThread({ versions, mail, onSaved }: { versions: Draft[]; mail: MailThread[]; onSaved: () => void }): ReactElement {
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
  const [suggesting, setSuggesting] = useState(false);
  const [suggestion, setSuggestion] = useState("");
  const revise = useMutation({
    mutationFn: () => call<Run>("POST", `/api/drafts/${draft.id}/revise`, { suggestion }),
    onSuccess: (run) => (window.location.hash = href.run(run.id)),
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
            <Button size="xs" variant={suggesting ? "light" : "default"} leftSection={<IconSparkles size={14} />} onClick={() => setSuggesting(!suggesting)}>
              Suggest a change
            </Button>
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
      {suggesting && !editing && (
        <Stack gap="xs">
          <Textarea
            autosize
            minRows={2}
            maxRows={8}
            placeholder="What should change? For example: shorter, mention the notice period, write it in Russian."
            value={suggestion}
            onChange={(e) => setSuggestion(e.currentTarget.value)}
          />
          <Group>
            <Button size="xs" leftSection={<IconSparkles size={14} />} disabled={!suggestion.trim()} loading={revise.isPending} onClick={() => revise.mutate()}>
              Rewrite v{draft.version} as v{latest.version + 1}
            </Button>
            <Text size="xs" c="dimmed">
              Runs the copywriter and naturalizer again with your note.
            </Text>
          </Group>
          <ErrorBox error={revise.error} />
        </Stack>
      )}
      {addressed && (
        <Group justify="space-between" gap="xs">
          <Text size="sm">
            To <b>{addressed.address}</b>{" "}
            <Badge size="xs" variant="light" color={addressed.address_confidence === "guessed" ? "orange" : "gray"}>
              {addressed.address_confidence}
            </Badge>
          </Text>
          {draft.channel === "email" && !editing && (
            <GmailDraft draft={draft} guessed={addressed.address_confidence === "guessed"} thread={mail.find((m) => m.recipient === draft.recipient && m.step === draft.step)} onSaved={onSaved} />
          )}
        </Group>
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

function useGmail(): string | null {
  const status = useQuery({ queryKey: ["mail"], queryFn: () => get<MailStatus>("/api/mail"), staleTime: Infinity });
  return status.data?.address ?? null;
}

function GmailDraft({ draft, guessed, thread, onSaved }: { draft: Draft; guessed: boolean; thread: MailThread | undefined; onSaved: () => void }): ReactElement | null {
  const gmail = useGmail();
  const push = useMutation({ mutationFn: () => call<MailThread>("POST", `/api/drafts/${draft.id}/gmail`), onSuccess: onSaved });
  if (!gmail) return null;
  if (thread?.sent_at) {
    const outcome = thread.bounced_at ? "bounced" : thread.replied_at ? "replied" : null;
    return (
      <Group gap={4}>
        <Badge variant="light" color="blue" leftSection={<IconBrandGmail size={12} />}>
          Sent {new Date(thread.sent_at).toLocaleDateString()}
        </Badge>
        {outcome && (
          <Badge variant="light" color={outcome === "bounced" ? "red" : "teal"}>
            {interactionLabel(outcome)}
          </Badge>
        )}
      </Group>
    );
  }
  if (thread?.draft_id === draft.id) {
    return (
      <Tooltip label={`Send it from ${gmail}; the lead is marked sent within five minutes.`} multiline maw={300}>
        <Badge variant="light" color="gray" leftSection={<IconBrandGmail size={12} />}>
          v{draft.version} in Gmail Drafts
        </Badge>
      </Tooltip>
    );
  }
  const tip = [
    thread ? "Replaces the letter's earlier Gmail draft; edits you made to it in Gmail are lost." : `Saves it as a draft in ${gmail}; you send it from Gmail.`,
    !draft.subject && "It goes in as a reply in the thread of your last email to them, so send that one first.",
    guessed && "The address is a guess, so check it before sending.",
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <Stack gap={4} align="flex-end">
      <Tooltip label={tip} multiline maw={320}>
        <Button size="xs" variant="light" leftSection={<IconBrandGmail size={14} />} loading={push.isPending} onClick={() => push.mutate()}>
          {thread ? `Put v${draft.version} in Gmail instead` : "Put in Gmail drafts"}
        </Button>
      </Tooltip>
      <ErrorBox error={push.error} />
    </Stack>
  );
}

function CheckGmail({ onSaved }: { onSaved: () => void }): ReactElement | null {
  const gmail = useGmail();
  const check = useMutation({ mutationFn: () => call<MailCheck>("POST", "/api/mail/check"), onSuccess: onSaved });
  if (!gmail) return null;
  const found = check.data;
  return (
    <Group gap="xs" mb="md">
      <Button size="xs" variant="default" leftSection={<IconRefresh size={14} />} loading={check.isPending} onClick={() => check.mutate()}>
        Check Gmail now
      </Button>
      {found && (
        <Text size="xs" c="dimmed">
          {found.logged.length ? `Logged: ${found.logged.length}` : `Nothing new in ${found.checked} watched letter${found.checked === 1 ? "" : "s"}`}
        </Text>
      )}
      <ErrorBox error={check.error} />
    </Group>
  );
}

function History({ item, onSaved }: { item: ItemDetail; onSaved: () => void }): ReactElement {
  const gmail = useGmail();
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
        {gmail
          ? `Nothing is sent from here. Letters you send from ${gmail} are logged on their own; log anything else yourself.`
          : "Nothing is sent from here. Log what you did yourself."}
      </Text>
      <CheckGmail onSaved={onSaved} />
      {item.interactions.length > 0 && (
        <Timeline bulletSize={14} lineWidth={2} mb="md">
          {item.interactions.map((h) => (
            <Timeline.Item key={h.id} title={interactionLabel(h.kind)}>
              {h.note && <Text size="sm">{h.note}</Text>}
              <Text size="xs" c="dimmed">
                {new Date(h.happened_at).toLocaleString()}
              </Text>
            </Timeline.Item>
          ))}
        </Timeline>
      )}
      <Group gap="xs" align="flex-end">
        <Select w={170} allowDeselect={false} value={kind} onChange={(v) => v && setKind(v as InteractionKind)} data={Object.entries(INTERACTION_LABELS).map(([value, label]) => ({ value, label }))} />
        <TextInput flex={1} placeholder="Note" value={note} onChange={(e) => setNote(e.currentTarget.value)} />
        <Button variant="light" onClick={() => log.mutate()} loading={log.isPending}>
          Log
        </Button>
      </Group>
      <ErrorBox error={log.error} />
    </Card>
  );
}
