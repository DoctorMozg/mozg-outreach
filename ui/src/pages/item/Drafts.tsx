import { Alert, Badge, Button, Card, CopyButton, Group, List, Select, SegmentedControl, Stack, Tabs, Text, Textarea, TextInput } from "@mantine/core";
import { IconCheck, IconCopy, IconEdit, IconMail, IconSparkles } from "@tabler/icons-react";
import { useMutation } from "@tanstack/react-query";
import { type ReactElement, useState } from "react";
import { call, type CandidateReview, type Draft, type ItemDetail, type MailThread, type Run } from "../../api";
import { Empty, ErrorBox } from "../../components/common";
import { Diff } from "../../components/Diff";
import { href } from "../../router";
import { GmailDraft } from "./Gmail";

// A company's letters come from its approach plan: enrichment and Re-plan write them.
export const WRITE_LABEL = { job: "Write a cover letter", gig: "Write a proposal" } as const;

/** Drafts of one letter share a tab: same approach step, recipient and channel. */
export const draftKey = (d: Draft): string => `${d.step ?? "-"}|${d.recipient}|${d.channel}`;
interface DraftsProps {
  item: ItemDetail;
  onSaved: () => void;
  tab: string | null;
  onTab: (key: string | null) => void;
}

export function Drafts({ item, onSaved, tab, onTab }: DraftsProps): ReactElement {
  const threads = new Map<string, Draft[]>();
  for (const d of item.drafts) threads.set(draftKey(d), [...(threads.get(draftKey(d)) ?? []), d]);
  const names = [...threads.keys()];
  if (names.length === 0) {
    const how =
      item.kind === "company"
        ? "Enrich the company: its approach plan writes a letter for each step."
        : item.kind === "job"
          ? "Enrich the job: it finds who to send it to and drafts the cover letter."
          : `${WRITE_LABEL[item.kind]} from the panel on the right.`;
    return (
      <Card>
        <Empty icon={<IconMail size={32} />} title="No drafts yet">
          {how}
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

type DraftView = "text" | "naturalized" | "previous" | "candidates" | "sent";

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
  const thread = mail.find((m) => m.recipient === draft.recipient && m.step === draft.step);
  const sentBody = thread?.draft_id === draft.id ? thread.sent_body : null;
  const views = [
    { value: "text", label: "Text" },
    ...(sentBody ? [{ value: "sent", label: "What you sent" }] : []),
    ...(r.before_naturalize ? [{ value: "naturalized", label: "Naturalizer changes" }] : []),
    ...(previous ? [{ value: "previous", label: `Changes since v${previous.version}` }] : []),
    ...(r.candidates?.length ? [{ value: "candidates", label: `Other drafts (${r.candidates.length - 1})` }] : []),
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
          {(draft.channel === "email" || draft.channel === "cover_letter") && addressed.address?.includes("@") && !editing && (
            <GmailDraft draft={draft} guessed={addressed.address_confidence === "guessed"} thread={thread} onSaved={onSaved} />
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
          {view === "sent" && sentBody ? (
            <Diff before={draft.body} after={sentBody} />
          ) : view === "candidates" && r.candidates ? (
            <Candidates candidates={r.candidates} />
          ) : view === "naturalized" && r.before_naturalize ? (
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

function Candidates({ candidates }: { candidates: CandidateReview[] }): ReactElement {
  return (
    <Stack gap="md">
      <Text size="xs" c="dimmed">
        Each draft opened a different way. Drafts that broke fewer letter rules went to a judge, which scored them twice in swapped order; the letter above is the chosen one after naturalizing.
      </Text>
      {candidates.map((c) => (
        <Stack key={c.variant} gap={4}>
          <Group gap="xs">
            <Badge size="sm" variant={c.chosen ? "filled" : "light"} color={c.chosen ? "teal" : "gray"}>
              {c.variant}
              {c.chosen ? " · chosen" : ""}
            </Badge>
            {c.score !== null && (
              <Text size="xs" c="dimmed">
                {c.score} points
              </Text>
            )}
            {c.problems.length > 0 && (
              <Text size="xs" c="red">
                {c.problems.join("; ")}
              </Text>
            )}
          </Group>
          {c.subject && (
            <Text size="sm" fw={600}>
              {c.subject}
            </Text>
          )}
          <div className="letter">{c.text}</div>
        </Stack>
      ))}
    </Stack>
  );
}
