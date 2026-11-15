import { Alert, Anchor, Badge, Button, Card, FileInput, Group, List, Select, SimpleGrid, Stack, TagsInput, Text, TextInput, Title } from "@mantine/core";
import { IconDownload, IconFileCv, IconTrash, IconUpload, IconWand } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactElement, useState } from "react";
import { call, type Cv, type CvSummary, type CvUpload, get, upload } from "../api";
import { Empty, ErrorBox } from "../components/common";

const SENIORITY = ["intern", "junior", "mid", "senior", "staff", "principal", "executive"];

export function Cvs(): ReactElement {
  const cvs = useQuery({ queryKey: ["cvs"], queryFn: () => get<Cv[]>("/api/cvs") });
  return (
    <Stack maw={1400} mx="auto">
      <div>
        <Title order={2}>Your CVs</Title>
        <Text size="sm" c="dimmed" maw={760}>
          Keep a CV per kind of role or language. Before a cover letter or proposal is written, each CV is rated against the posting and the
          best one is picked; the letter claims only what that CV says. Correct a summary if it misreads the CV, since the pick relies on it.
        </Text>
      </div>
      <UploadCard count={cvs.data?.length ?? 0} />
      <ErrorBox error={cvs.error} />
      {cvs.data?.length === 0 ? (
        <Empty icon={<IconFileCv size={32} />} title="No CVs yet">
          Upload a PDF exported from your editor. Without one, letters use the CV text in the project brief.
        </Empty>
      ) : (
        <SimpleGrid cols={{ base: 1, lg: 2 }}>
          {cvs.data?.map((cv) => (
            <CvCard key={cv.id} cv={cv} />
          ))}
        </SimpleGrid>
      )}
    </Stack>
  );
}

function UploadCard({ count }: { count: number }): ReactElement {
  const client = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const send = useMutation({
    mutationFn: () => upload<CvUpload>(`/api/cvs?${new URLSearchParams({ name: name.trim(), filename: file!.name }).toString()}`, file!),
    onSuccess: () => {
      setFile(null);
      setName("");
      void client.invalidateQueries({ queryKey: ["cvs"] });
    },
  });
  return (
    <Card>
      <Group align="flex-end" gap="sm">
        <FileInput w={300} label="PDF, up to 2.5 MB" placeholder="Choose a CV" accept="application/pdf" value={file} onChange={setFile} clearable />
        <TextInput w={260} label="Name" placeholder="Backend, English" value={name} onChange={(e) => setName(e.currentTarget.value)} />
        <Button leftSection={<IconUpload size={16} />} disabled={!file || !name.trim() || count >= 8} loading={send.isPending} onClick={() => send.mutate()}>
          Upload and profile
        </Button>
      </Group>
      <Text size="xs" c="dimmed" mt="xs">
        Profiling is one AI call. At most 8 CVs.
      </Text>
      {send.data && send.data.concerns.length > 0 && (
        <Alert color="yellow" variant="light" mt="sm" title={`Uploaded ${send.data.cv.name}`}>
          <List size="sm">
            {send.data.concerns.map((c, n) => (
              <List.Item key={n}>{c}</List.Item>
            ))}
          </List>
        </Alert>
      )}
      <ErrorBox error={send.error} />
    </Card>
  );
}

function CvCard({ cv }: { cv: Cv }): ReactElement {
  const client = useQueryClient();
  const refresh = (): void => void client.invalidateQueries({ queryKey: ["cvs"] });
  const [editing, setEditing] = useState(false);
  const [armed, setArmed] = useState(false);
  const profile = useMutation({ mutationFn: () => call<Cv>("POST", `/api/cvs/${cv.id}/profile`), onSuccess: refresh });
  const remove = useMutation({ mutationFn: () => call("DELETE", `/api/cvs/${cv.id}`), onSuccess: refresh });
  const s = cv.summary;
  return (
    <Card>
      <Group justify="space-between" mb="xs" wrap="nowrap">
        <Group gap="xs">
          <Title order={4}>{cv.name}</Title>
          {s && (
            <Badge variant="light" color="indigo">
              {s.language}
            </Badge>
          )}
        </Group>
        <Group gap="xs" wrap="nowrap">
          <Button size="xs" variant="default" component="a" href={`/api/cvs/${cv.id}/pdf`} leftSection={<IconDownload size={14} />}>
            {cv.filename}
          </Button>
          <Button
            size="xs"
            variant={armed ? "filled" : "subtle"}
            color="red"
            leftSection={<IconTrash size={14} />}
            loading={remove.isPending}
            onClick={() => (armed ? remove.mutate() : setArmed(true))}
            onBlur={() => setArmed(false)}
          >
            {armed ? "Delete for good" : "Delete"}
          </Button>
        </Group>
      </Group>
      {editing && s ? (
        <SummaryForm cv={cv} summary={s} onDone={() => setEditing(false)} />
      ) : s ? (
        <Stack gap={6}>
          <Text fw={550}>
            {s.headline} <Text span c="dimmed">· {s.seniority}</Text>
          </Text>
          <Tags label="Titles" values={s.titles} />
          <Tags label="Domains" values={s.domains} />
          <Tags label="Core skills" values={s.core_skills} />
          <Group>
            <Anchor component="button" size="sm" onClick={() => setEditing(true)}>
              Correct the summary
            </Anchor>
          </Group>
        </Stack>
      ) : (
        <Group>
          <Text size="sm" c="dimmed">
            Not profiled yet, so it can't be picked.
          </Text>
          <Button size="xs" variant="light" leftSection={<IconWand size={14} />} loading={profile.isPending} onClick={() => profile.mutate()}>
            Profile it
          </Button>
        </Group>
      )}
      <ErrorBox error={profile.error ?? remove.error} />
    </Card>
  );
}

function Tags({ label, values }: { label: string; values: string[] }): ReactElement | null {
  if (values.length === 0) return null;
  return (
    <Group gap={4}>
      <Text size="xs" c="dimmed" w={80}>
        {label}
      </Text>
      {values.map((v) => (
        <Badge key={v} size="sm" variant="outline" color="gray">
          {v}
        </Badge>
      ))}
    </Group>
  );
}

function SummaryForm({ cv, summary, onDone }: { cv: Cv; summary: CvSummary; onDone: () => void }): ReactElement {
  const client = useQueryClient();
  const [name, setName] = useState(cv.name);
  const [draft, setDraft] = useState(summary);
  const save = useMutation({
    mutationFn: () => call<Cv>("PATCH", `/api/cvs/${cv.id}`, { name, summary: draft }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["cvs"] });
      onDone();
    },
  });
  const set = (patch: Partial<CvSummary>): void => setDraft({ ...draft, ...patch });
  return (
    <Stack gap="xs">
      <Group grow>
        <TextInput label="Name" value={name} onChange={(e) => setName(e.currentTarget.value)} />
        <TextInput label="Language (ISO code)" maxLength={2} value={draft.language} onChange={(e) => set({ language: e.currentTarget.value.toLowerCase() })} />
        <Select label="Seniority" allowDeselect={false} data={SENIORITY} value={draft.seniority} onChange={(v) => v && set({ seniority: v })} />
      </Group>
      <TextInput label="Headline" value={draft.headline} onChange={(e) => set({ headline: e.currentTarget.value })} />
      <TagsInput label="Titles" value={draft.titles} onChange={(titles) => set({ titles })} />
      <TagsInput label="Domains" value={draft.domains} onChange={(domains) => set({ domains })} />
      <TagsInput label="Core skills" value={draft.core_skills} onChange={(core_skills) => set({ core_skills })} />
      <Group>
        <Button size="xs" onClick={() => save.mutate()} loading={save.isPending} disabled={!name.trim() || draft.language.length !== 2 || draft.titles.length === 0}>
          Save
        </Button>
        <Button size="xs" variant="default" onClick={onDone}>
          Cancel
        </Button>
      </Group>
      <ErrorBox error={save.error} />
    </Stack>
  );
}
