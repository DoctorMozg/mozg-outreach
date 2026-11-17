import { Accordion, Anchor, Badge, Button, Card, Code, Grid, Group, Select, Stack, Text, TextInput, Timeline, Title, Tooltip } from "@mantine/core";
import { IconArrowLeft, IconDownload, IconForms, IconMail, IconSparkles } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactElement, useState } from "react";
import { call, type Cv, type CvChoice, enrichHint, get, type ItemDetail, type Run } from "../api";
import { ActivityBadge, DATED_KINDS, EnrichedBadge, ErrorBox, INTERACTION_LABELS, type InteractionKind, interactionLabel, Score, StepBadge } from "../components/common";
import { href } from "../router";
import { Axes, FieldList } from "./Report";
import { Approach } from "./item/Approach";
import { Drafts, WRITE_LABEL } from "./item/Drafts";
import { People } from "./item/People";
import { CheckGmail, useGmail } from "./item/Gmail";

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
        <EnrichedBadge item={i} size="lg" />
        <StepBadge step={i.approach_step} size="lg" />
        <Text size="sm" c="dimmed">
          {i.dated}
        </Text>
      </Group>
      <Grid gap="lg">
        <Grid.Col span={{ base: 12, lg: 7 }}>
          <Stack>
            {i.kind === "company" && i.enriched && <Approach item={i} onChange={refresh} onOpenDraft={openDraft} />}
            {i.kind === "company" && <People item={i} onChange={refresh} />}
            <Drafts item={i} onSaved={refresh} tab={draftTab} onTab={setDraftTab} />
            <History item={i} onSaved={refresh} />
          </Stack>
        </Grid.Col>
        <Grid.Col span={{ base: 12, lg: 5 }}>
          <Stack>
            <Actions item={i} />
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

function Actions({ item }: { item: ItemDetail }): ReactElement {
  const start = useMutation({
    mutationFn: (action: "enrich" | "letters" | "apply") => call<Run>("POST", `/api/items/${item.id}/${action}`),
    onSuccess: (run) => (window.location.hash = href.run(run.id)),
  });
  const enrich = enrichHint(item);
  return (
    <Card>
      <Group justify="flex-end">
        <Group gap="xs">
          {item.kind !== "gig" && (
            <Button
              variant="default"
              leftSection={<IconSparkles size={16} />}
              loading={start.isPending && start.variables === "enrich"}
              onClick={() => start.mutate("enrich")}
            >
              {enrich.label}
            </Button>
          )}
          {item.kind !== "company" && (
            <Button
              leftSection={<IconMail size={16} />}
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
                  loading={start.isPending && start.variables === "apply"}
                onClick={() => start.mutate("apply")}
              >
                Fill application
              </Button>
            </Tooltip>
          )}
        </Group>
      </Group>
      {enrich.note && item.kind !== "gig" && (
        <Text size="sm" c="dimmed" mt="xs">
          {enrich.note}
        </Text>
      )}
      <ErrorBox error={start.error} />
    </Card>
  );
}

/** One tab per recipient and channel; each holds that thread's versions. */
function History({ item, onSaved }: { item: ItemDetail; onSaved: () => void }): ReactElement {
  const gmail = useGmail();
  const [kind, setKind] = useState<InteractionKind>("sent");
  const [note, setNote] = useState("");
  const [until, setUntil] = useState("");
  const dated = DATED_KINDS[kind];
  const log = useMutation({
    mutationFn: () => call("POST", `/api/items/${item.id}/interactions`, { kind, note, until: dated && until ? until : null }),
    onSuccess: () => {
      setNote("");
      setUntil("");
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
              {h.until && (
                <Text size="sm">
                  {DATED_KINDS[h.kind as InteractionKind] ?? "Until"} {h.until}
                </Text>
              )}
              <Text size="xs" c="dimmed">
                {new Date(h.happened_at).toLocaleString()}
              </Text>
            </Timeline.Item>
          ))}
        </Timeline>
      )}
      <Group gap="xs" align="flex-end">
        <Select w={170} allowDeselect={false} value={kind} onChange={(v) => v && setKind(v as InteractionKind)} data={Object.entries(INTERACTION_LABELS).map(([value, label]) => ({ value, label }))} />
        {dated && <TextInput type="date" w={190} aria-label={dated} leftSection={<Text size="xs">{dated}</Text>} leftSectionWidth={90} value={until} onChange={(e) => setUntil(e.currentTarget.value)} />}
        <TextInput flex={1} placeholder="Note" value={note} onChange={(e) => setNote(e.currentTarget.value)} />
        <Button variant="light" onClick={() => log.mutate()} loading={log.isPending}>
          Log
        </Button>
      </Group>
      <ErrorBox error={log.error} />
    </Card>
  );
}
