import { Alert, Anchor, Badge, Button, Card, Fieldset, Grid, Group, Select, Stack, Text, Textarea, TextInput, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconDeviceFloppy, IconSparkles } from "@tabler/icons-react";
import { type UseQueryResult, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactElement, useEffect, useState } from "react";
import { type BriefDraft, call, get, type Json, type Project } from "../api";
import { Concerns, ErrorBox } from "../components/common";
import { SchemaForm } from "../components/SchemaForm";
import { href } from "../router";

type Schemas = { brief: Json; options: Json };
const TONES = [
  { value: "warm", label: "Warm" },
  { value: "neutral", label: "Neutral" },
  { value: "formal", label: "Formal" },
];

export function useSchemas(mode: string): UseQueryResult<Schemas> {
  return useQuery({ queryKey: ["schemas", mode], queryFn: () => get<Schemas>(`/api/modes/${mode}/schemas`), staleTime: Infinity });
}

export function Brief({ project }: { project: Project }): ReactElement {
  const client = useQueryClient();
  const schemas = useSchemas(project.mode);
  const [brief, setBrief] = useState<Json>(project.brief ?? {});
  const [sender, setSender] = useState({ name: project.sender_name ?? "", tone: project.sender_tone });
  useEffect(() => setBrief(project.brief ?? {}), [project.brief]);
  const dirty =
    JSON.stringify(brief) !== JSON.stringify(project.brief ?? {}) ||
    sender.name !== (project.sender_name ?? "") ||
    sender.tone !== project.sender_tone;
  const save = useMutation({
    mutationFn: () =>
      call<Project>("PATCH", `/api/projects/${project.id}`, {
        brief,
        sender_name: sender.name || null,
        sender_tone: sender.tone,
      }),
    onSuccess: (saved) => {
      client.setQueryData(["project", project.id], saved);
      void client.invalidateQueries({ queryKey: ["search", project.id] });
      void client.invalidateQueries({ queryKey: ["projects"] });
      notifications.show({ color: "teal", message: "Brief saved" });
    },
  });

  return (
    <Grid gap="lg">
      <Grid.Col span={{ base: 12, lg: 8 }}>
        <Card>
          <Group justify="space-between" mb="md">
            <Group gap="sm">
              <Title order={4}>Brief</Title>
              {dirty && (
                <Badge color="yellow" variant="light">
                  unsaved changes
                </Badge>
              )}
            </Group>
            <Button leftSection={<IconDeviceFloppy size={16} />} onClick={() => save.mutate()} loading={save.isPending} disabled={!dirty}>
              Save
            </Button>
          </Group>
          <Stack>
            <ErrorBox error={save.error ?? schemas.error} />
            <Fieldset legend={<Text fw={600} size="sm">Signature</Text>}>
              <Group grow align="flex-start">
                <TextInput
                  label="Your name"
                  description="Signs every letter"
                  value={sender.name}
                  onChange={(e) => setSender({ ...sender, name: e.currentTarget.value })}
                />
                <Select
                  label="Tone"
                  description="Picks the salutation and sign-off"
                  data={TONES}
                  value={sender.tone}
                  allowDeselect={false}
                  onChange={(tone) => tone && setSender({ ...sender, tone })}
                />
              </Group>
            </Fieldset>
            {schemas.data && <SchemaForm schema={schemas.data.brief} value={brief} onChange={setBrief} />}
          </Stack>
        </Card>
      </Grid.Col>
      <Grid.Col span={{ base: 12, lg: 4 }}>
        <div style={{ position: "sticky", top: 72 }}>
          <WriteWithAi project={project} brief={brief} onDraft={setBrief} />
        </div>
      </Grid.Col>
    </Grid>
  );
}

/** Drafts the brief from the company site (biz) or the CV (seeker) into the form, unsaved. */
function WriteWithAi({ project, brief, onDraft }: { project: Project; brief: Json; onDraft: (brief: Json) => void }): ReactElement {
  const client = useQueryClient();
  const biz = project.mode === "biz";
  const [source, setSource] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const site = source ?? "";
  const cv = source ?? (typeof brief.cv_text === "string" ? brief.cv_text : "");
  const write = useMutation({
    mutationFn: () =>
      call<BriefDraft>("POST", `/api/projects/${project.id}/brief/generate`, biz ? { site_url: site, note } : { cv_text: cv, note }),
    onSuccess: (draft) => {
      onDraft(draft.brief);
      void client.invalidateQueries({ queryKey: ["fields", project.id] });
      notifications.show({ color: "indigo", title: "Draft ready", message: "Check the brief, then press Save." });
    },
  });
  const draft = write.data;
  return (
    <Card>
      <Group gap="xs" mb={4}>
        <IconSparkles size={20} color="var(--mantine-color-indigo-filled)" />
        <Title order={4}>Write with AI</Title>
      </Group>
      <Text size="sm" c="dimmed" mb="md">
        {biz
          ? "Give your company's website. The AI reads it and fills the brief, and drafts report fields too."
          : "Paste your CV. The AI fills the brief from it, and drafts report fields too."}
      </Text>
      <Stack gap="sm">
        {biz ? (
          <TextInput label="Your company's website" placeholder="https://" value={site} onChange={(e) => setSource(e.currentTarget.value)} />
        ) : (
          <Textarea label="Your CV, as text" autosize minRows={6} maxRows={14} value={cv} onChange={(e) => setSource(e.currentTarget.value)} />
        )}
        <Textarea
          label="Note"
          description="Optional"
          autosize
          minRows={2}
          placeholder={biz ? "Goal, target regions, companies to avoid" : "Regions, salary or rate, work you won't take"}
          value={note}
          onChange={(e) => setNote(e.currentTarget.value)}
        />
        <Button leftSection={<IconSparkles size={16} />} variant="light" loading={write.isPending} disabled={!(biz ? site : cv)} onClick={() => write.mutate()}>
          Write the brief
        </Button>
        {write.isPending && (
          <Text size="xs" c="dimmed" ta="center">
            Takes about a minute and costs roughly $0.40 of usage.
          </Text>
        )}
        <ErrorBox error={write.error} />
        {draft && (
          <Alert color="indigo" variant="light" title={`Draft in the form ($${draft.cost_usd.toFixed(2)})`}>
            <Text size="sm">
              Not saved yet: check it, then press Save.
              {draft.field_version !== null && (
                <>
                  {" "}
                  Report fields were drafted too. <Anchor href={href.project(project.id, "fields")}>Review and accept them</Anchor>.
                </>
              )}
            </Text>
          </Alert>
        )}
        {draft && <Concerns items={draft.concerns} />}
      </Stack>
    </Card>
  );
}
