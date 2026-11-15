import { Accordion, Alert, Button, Card, Group, Stack, Table, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconAlertCircle, IconHistory, IconPlayerPlay } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactElement, useState } from "react";
import { call, get, type Json, type Project, type Run } from "../api";
import { Empty, ErrorBox, pipelineLabel, Status } from "../components/common";
import { SchemaForm } from "../components/SchemaForm";
import { href } from "../router";
import { useSchemas } from "./Brief";

export function Runs({ project, blocker }: { project: Project; blocker: string | null }): ReactElement {
  const client = useQueryClient();
  const schemas = useSchemas(project.mode);
  const [options, setOptions] = useState<Json>({});
  const runs = useQuery({
    queryKey: ["runs", project.id],
    queryFn: () => get<Run[]>(`/api/projects/${project.id}/runs`),
    refetchInterval: 5000,
  });
  const start = useMutation({
    mutationFn: () => call<Run>("POST", `/api/projects/${project.id}/runs`, { options }),
    onSuccess: (run) => {
      void client.invalidateQueries({ queryKey: ["runs", project.id] });
      notifications.show({ color: "indigo", message: `Search run ${run.id} started` });
      window.location.hash = href.run(run.id);
    },
  });

  return (
    <Stack>
      <Card>
        <Group justify="space-between" mb="sm">
          <div>
            <Title order={4}>Start a search</Title>
            <Text size="sm" c="dimmed">
              The run plans a strategy and waits for your approval before it searches anything.
            </Text>
          </div>
          <Button leftSection={<IconPlayerPlay size={16} />} disabled={Boolean(blocker)} loading={start.isPending} onClick={() => start.mutate()}>
            Start
          </Button>
        </Group>
        {blocker && (
          <Alert variant="light" color="yellow" icon={<IconAlertCircle size={18} />} mb="sm">
            {blocker.charAt(0).toUpperCase() + blocker.slice(1)}.
          </Alert>
        )}
        <ErrorBox error={start.error} />
        {schemas.data && (
          <Accordion variant="contained" mt="sm">
            <Accordion.Item value="options">
              <Accordion.Control>Search options</Accordion.Control>
              <Accordion.Panel>
                <SchemaForm schema={schemas.data.options} value={options} onChange={setOptions} />
              </Accordion.Panel>
            </Accordion.Item>
          </Accordion>
        )}
      </Card>

      <Card>
        <Title order={4} mb="md">
          Runs
        </Title>
        {runs.data?.length === 0 ? (
          <Empty icon={<IconHistory size={32} />} title="No runs yet" />
        ) : (
          <Table.ScrollContainer minWidth={640}>
            <Table highlightOnHover verticalSpacing="sm">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>#</Table.Th>
                  <Table.Th>What</Table.Th>
                  <Table.Th>Status</Table.Th>
                  <Table.Th>Phase</Table.Th>
                  <Table.Th ta="right">Cost</Table.Th>
                  <Table.Th>Started</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {runs.data?.map((r) => (
                  <Table.Tr key={r.id} style={{ cursor: "pointer" }} onClick={() => (window.location.hash = href.run(r.id))}>
                    <Table.Td c="dimmed">{r.id}</Table.Td>
                    <Table.Td fw={500}>{pipelineLabel(r.pipeline)}</Table.Td>
                    <Table.Td>
                      <Status status={r.status} />
                    </Table.Td>
                    <Table.Td c="dimmed">{r.phase?.replaceAll("_", " ")}</Table.Td>
                    <Table.Td ta="right" style={{ fontVariantNumeric: "tabular-nums" }}>
                      ${r.cost_usd.toFixed(2)}
                    </Table.Td>
                    <Table.Td c="dimmed">{new Date(r.started_at).toLocaleString()}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        )}
        <ErrorBox error={runs.error} />
      </Card>
    </Stack>
  );
}
