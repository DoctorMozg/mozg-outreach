import { ActionIcon, Alert, Button, Card, Code, Group, JsonInput, Modal, Select, SimpleGrid, Stack, Switch, Text, Title, Tooltip } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { IconInfoCircle, IconPlus, IconTrash } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactElement, useState } from "react";
import { call, get, type Json, type Project, type Source, type SourceKind } from "../api";
import { ErrorBox } from "../components/common";

// Starting configs per feed; the API validates them against each adapter's model.
const EXAMPLES: Record<string, Json> = {
  greenhouse: { boards: ["company-board-token"] },
  ashby: { boards: ["company"] },
  lever: { companies: ["company"] },
  telegram: { discover: true, channels: [], keywords: [], kind: "job" },
};

export function Sources({ project }: { project: Project }): ReactElement {
  const client = useQueryClient();
  const key = ["sources", project.id];
  const sources = useQuery({ queryKey: key, queryFn: () => get<Source[]>(`/api/projects/${project.id}/sources`) });
  const [adding, add] = useDisclosure();
  const refresh = (): void => void client.invalidateQueries({ queryKey: key });
  const toggle = useMutation({
    mutationFn: (s: Source) => call("PATCH", `/api/sources/${s.id}`, { enabled: !s.enabled }),
    onSuccess: refresh,
  });
  const remove = useMutation({ mutationFn: (s: Source) => call("DELETE", `/api/sources/${s.id}`), onSuccess: refresh });
  const kinds = useQuery({ queryKey: ["source-kinds"], queryFn: () => get<SourceKind[]>("/api/source-kinds"), staleTime: Infinity });
  const hasWeb = sources.data?.some((s) => s.kind === "web_search") ?? true;
  // Web search is one per project, so it is offered only when it was removed.
  const addable = kinds.data?.filter((k) => k.modes.includes(project.mode) && (k.kind !== "web_search" || !hasWeb)) ?? [];
  const seeker = project.mode === "seeker";

  return (
    <Stack>
      <Group justify="space-between">
        <div>
          <Title order={4}>Data sources</Title>
          <Text size="sm" c="dimmed">
            Where a run looks. Web search is always there; agents search and read the web.
          </Text>
        </div>
        {addable.length > 0 && (
          <Button leftSection={<IconPlus size={16} />} onClick={add.open}>
            Add a source
          </Button>
        )}
      </Group>
      {!seeker && (
        <Alert variant="light" icon={<IconInfoCircle size={18} />}>
          Business searches run on web search. Job feeds and Telegram serve seeker projects.
        </Alert>
      )}
      <ErrorBox error={sources.error ?? toggle.error ?? remove.error} />
      <SimpleGrid cols={{ base: 1, md: 2, xl: 3 }}>
        {sources.data?.map((s) => (
          <Card key={s.id} opacity={s.enabled ? 1 : 0.6}>
            <Group justify="space-between" mb="xs" wrap="nowrap">
              <Text fw={650}>{s.kind.replaceAll("_", " ")}</Text>
              <Group gap="xs" wrap="nowrap">
                <Switch checked={s.enabled} onChange={() => toggle.mutate(s)} aria-label="Enabled" />
                <Tooltip label="Remove">
                  <ActionIcon variant="subtle" color="red" onClick={() => remove.mutate(s)} aria-label="Remove">
                    <IconTrash size={16} />
                  </ActionIcon>
                </Tooltip>
              </Group>
            </Group>
            {Object.keys(s.config).length > 0 && (
              <Code block mb="xs">
                {JSON.stringify(s.config, null, 2)}
              </Code>
            )}
            <Text size="xs" c="dimmed">
              {s.cursor ? `Read up to ${s.cursor}` : "Not read yet"}
            </Text>
          </Card>
        ))}
      </SimpleGrid>
      <AddSource project={project} kinds={addable} opened={adding} onClose={add.close} onAdded={refresh} />
    </Stack>
  );
}

interface AddProps {
  project: Project;
  kinds: SourceKind[];
  opened: boolean;
  onClose: () => void;
  onAdded: () => void;
}

function AddSource({ project, kinds: usable, opened, onClose, onAdded }: AddProps): ReactElement {
  const [kind, setKind] = useState<string | null>(null);
  const [config, setConfig] = useState("{}");
  const save = useMutation({
    mutationFn: () => call("POST", `/api/projects/${project.id}/sources`, { kind, config: JSON.parse(config) as Json }),
    onSuccess: () => {
      onAdded();
      onClose();
    },
  });
  const pick = (k: string | null): void => {
    setKind(k);
    setConfig(JSON.stringify((k && EXAMPLES[k]) ?? {}, null, 2));
  };
  return (
    <Modal opened={opened} onClose={onClose} title={<Text fw={650}>Add a source</Text>} size="lg">
      <Stack>
        <Select
          label="Kind"
          placeholder="Choose a feed"
          data={usable.map((k) => k.kind)}
          value={kind}
          onChange={pick}
          description={usable.find((k) => k.kind === kind)?.note}
        />
        <JsonInput label="Config" formatOnBlur autosize minRows={4} maxRows={14} validationError="Not valid JSON" value={config} onChange={setConfig} />
        <ErrorBox error={save.error} />
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={!kind} loading={save.isPending} onClick={() => save.mutate()}>
            Add
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
