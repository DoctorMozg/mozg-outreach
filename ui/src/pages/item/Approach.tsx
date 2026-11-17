import { Alert, Anchor, Badge, Button, Card, Group, List, Stack, Text, Timeline, Title, Tooltip } from "@mantine/core";
import { IconCheck, IconMail } from "@tabler/icons-react";
import { useMutation } from "@tanstack/react-query";
import { type ReactElement } from "react";
import { call, type ItemDetail, type PlanContact, type PlannedTouch, type Run, type StoredApproach } from "../../api";
import { ErrorBox } from "../../components/common";
import { NoteButton, REPLAN_NOTE } from "../../components/NoteButton";
import { href } from "../../router";
import { draftKey } from "./Drafts";

const CHANNEL_LABEL = { email: "Email", linkedin: "LinkedIn", phone: "Call", form: "Contact form" } as const;
const PURPOSE_COLOR = { open: "teal", follow_up: "indigo", breakup: "gray" } as const;

/** The plan from enrichment: who hears first, through which route, and the follow-ups after. */
interface ApproachProps {
  item: ItemDetail;
  onChange: () => void;
  onOpenDraft: (key: string) => void;
}

export function Approach({ item, onChange, onOpenDraft }: ApproachProps): ReactElement {
  const approach = (item.details as { approach?: StoredApproach }).approach ?? null;
  const mark = useMutation({
    mutationFn: ({ step, done }: { step: number; done: boolean }) =>
      call(done ? "POST" : "DELETE", `/api/items/${item.id}/approach/steps/${step}/done`, done ? { on: null } : undefined),
    onSuccess: onChange,
  });
  const replan = useMutation({
    mutationFn: (feedback: string | null) => call<Run>("POST", `/api/items/${item.id}/approach`, { feedback }),
    onSuccess: (run) => (window.location.hash = href.run(run.id)),
  });
  const button = (
    <NoteButton
      label={approach ? "Re-plan" : "Plan the approach"}
      text={REPLAN_NOTE}
      variant="default"
      loading={replan.isPending}
      onStart={(feedback) => replan.mutate(feedback)}
    />
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
        {touch.connection ? "LinkedIn connection request" : route ? CHANNEL_LABEL[route.channel] : touch.route_ref} to {who}
      </Text>
      {touch.connection && (
        <Badge size="xs" variant="outline" color="gray">
          {touch.connection === "note" ? "note, max 200 characters" : "no note: click Connect on the profile"}
        </Badge>
      )}
      {route && (
        <Text size="xs" c={route.confidence === "guessed" ? "orange" : "dimmed"}>
          {route.address.startsWith("http") ? (
            <Anchor href={route.address} target="_blank" rel="noreferrer" size="xs">
              {route.channel === "form" ? "open the form" : route.address}
            </Anchor>
          ) : (
            route.address
          )}
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
