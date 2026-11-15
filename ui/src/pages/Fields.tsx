import { ActionIcon, Alert, Badge, Button, Card, Checkbox, Group, Select, Stack, Table, Text, Textarea, TextInput, Title, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconCheck, IconDeviceFloppy, IconInfoCircle, IconPlus, IconSparkles, IconTrash } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactElement, useEffect, useState } from "react";
import { call, type FieldDefinition, type FieldSpecVersion, get, type Project, type Run } from "../api";
import { Empty, ErrorBox } from "../components/common";
import { href } from "../router";

const TYPES: FieldDefinition["type"][] = ["text", "number", "boolean", "url", "email", "date", "list"];
const BLANK: FieldDefinition = { id: "", label: "", type: "text", description: "", required: false, example: null };

export function Fields({ project }: { project: Project }): ReactElement {
  const client = useQueryClient();
  const key = ["fields", project.id];
  const versions = useQuery({ queryKey: key, queryFn: () => get<FieldSpecVersion[]>(`/api/projects/${project.id}/fields`) });
  const latest = versions.data?.at(-1);
  const accepted = versions.data?.filter((v) => v.accepted_at).at(-1);
  const [rows, setRows] = useState<FieldDefinition[]>([]);
  useEffect(() => setRows(latest?.spec.fields ?? []), [latest]);

  const refresh = (): void => {
    void client.invalidateQueries({ queryKey: key });
    void client.invalidateQueries({ queryKey: ["search", project.id] });
  };
  const propose = useMutation({
    mutationFn: () => call<Run>("POST", `/api/projects/${project.id}/fields/propose`),
    onSuccess: (run) => (window.location.hash = href.run(run.id)),
  });
  const save = useMutation({
    mutationFn: () => call<FieldSpecVersion>("POST", `/api/projects/${project.id}/fields`, { fields: rows }),
    onSuccess: (saved) => {
      refresh();
      notifications.show({ color: "teal", message: `Saved as v${saved.version}. Accept it to use it in runs.` });
    },
  });
  const accept = useMutation({
    mutationFn: (version: number) => call("POST", `/api/projects/${project.id}/fields/${version}/accept`),
    onSuccess: () => {
      refresh();
      notifications.show({ color: "teal", message: "Fields accepted. New runs use them." });
    },
  });
  const edit = (n: number, patch: Partial<FieldDefinition>): void => setRows(rows.map((r, i) => (i === n ? { ...r, ...patch } : r)));
  const dirty = JSON.stringify(rows) !== JSON.stringify(latest?.spec.fields ?? []);
  const pending = latest && !latest.accepted_at;

  return (
    <Card>
      <Group justify="space-between" mb="xs">
        <Group gap="sm">
          <Title order={4}>Report fields</Title>
          {accepted && <Badge color="teal" variant="light">runs use v{accepted.version}</Badge>}
          {pending && <Badge color="yellow" variant="light">v{latest.version} not accepted</Badge>}
        </Group>
        <Group gap="xs">
          <Button variant="default" leftSection={<IconSparkles size={16} />} onClick={() => propose.mutate()} loading={propose.isPending} disabled={!project.brief}>
            Propose from brief
          </Button>
          <Button variant="default" leftSection={<IconDeviceFloppy size={16} />} onClick={() => save.mutate()} disabled={!dirty} loading={save.isPending}>
            Save as v{(latest?.version ?? 0) + 1}
          </Button>
          {pending && !dirty && (
            <Button leftSection={<IconCheck size={16} />} onClick={() => accept.mutate(latest.version)} loading={accept.isPending}>
              Accept v{latest.version}
            </Button>
          )}
        </Group>
      </Group>
      <Text size="sm" c="dimmed" mb="md">
        What a run researches for every lead. An accepted field keeps its type; add a new id to change one.
      </Text>
      <Stack>
        {pending && !dirty && project.mode === "biz" && (
          <Alert color="yellow" variant="light" icon={<IconInfoCircle size={18} />}>
            {accepted
              ? `Runs keep using v${accepted.version} until you accept v${latest.version}.`
              : "A business search can't start until you accept a version."}
          </Alert>
        )}
        <ErrorBox error={save.error ?? accept.error ?? propose.error ?? versions.error} />
        {rows.length === 0 ? (
          <Empty icon={<IconSparkles size={32} />} title="No fields yet">
            Propose them from the brief, or add them by hand.
          </Empty>
        ) : (
          <Table.ScrollContainer minWidth={900}>
            <Table verticalSpacing="xs">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th w={170}>Id</Table.Th>
                  <Table.Th w={200}>Label</Table.Th>
                  <Table.Th w={120}>Type</Table.Th>
                  <Table.Th>What to collect</Table.Th>
                  <Table.Th w={60} ta="center">
                    Req.
                  </Table.Th>
                  <Table.Th w={44} />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {rows.map((r, n) => (
                  <Table.Tr key={n}>
                    <Table.Td>
                      <TextInput size="xs" ff="monospace" value={r.id} onChange={(e) => edit(n, { id: e.currentTarget.value })} />
                    </Table.Td>
                    <Table.Td>
                      <TextInput size="xs" value={r.label} onChange={(e) => edit(n, { label: e.currentTarget.value })} />
                    </Table.Td>
                    <Table.Td>
                      <Select size="xs" data={TYPES} value={r.type} allowDeselect={false} onChange={(t) => t && edit(n, { type: t as FieldDefinition["type"] })} />
                    </Table.Td>
                    <Table.Td>
                      <Textarea size="xs" autosize minRows={1} maxRows={4} value={r.description} onChange={(e) => edit(n, { description: e.currentTarget.value })} />
                    </Table.Td>
                    <Table.Td>
                      <Group justify="center">
                        <Checkbox checked={r.required} onChange={(e) => edit(n, { required: e.currentTarget.checked })} aria-label="Required" />
                      </Group>
                    </Table.Td>
                    <Table.Td>
                      <Tooltip label="Remove">
                        <ActionIcon variant="subtle" color="red" onClick={() => setRows(rows.filter((_, i) => i !== n))} aria-label="Remove">
                          <IconTrash size={16} />
                        </ActionIcon>
                      </Tooltip>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        )}
        <Group>
          <Button variant="light" size="xs" leftSection={<IconPlus size={14} />} onClick={() => setRows([...rows, BLANK])}>
            Add field
          </Button>
        </Group>
      </Stack>
    </Card>
  );
}
