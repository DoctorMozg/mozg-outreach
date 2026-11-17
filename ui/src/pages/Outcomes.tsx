import { Alert, Badge, Stack, Table, Text } from "@mantine/core";
import { IconChartBar } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import type { ReactElement } from "react";
import { get, type OutcomeRow, type Outcomes as OutcomesData, type Project } from "../api";
import { Empty, ErrorBox } from "../components/common";

const GROUPS: { group: OutcomeRow["group"]; title: string }[] = [
  { group: "angle", title: "Angle" },
  { group: "purpose", title: "Touch" },
];

function percent(share: number): string {
  return `${Math.round(share * 100)}%`;
}

/** Reply rates per angle and touch, with intervals wide enough to stop over-reading small counts. */
export function Outcomes({ project }: { project: Project }): ReactElement {
  const outcomes = useQuery({ queryKey: ["outcomes", project.id], queryFn: () => get<OutcomesData>(`/api/projects/${project.id}/outcomes`) });
  const data = outcomes.data;
  if (!data) return <ErrorBox error={outcomes.error} />;
  if (data.sent === 0) {
    return (
      <Empty icon={<IconChartBar size={28} />} title="Nothing sent yet">
        Letters you send from Gmail Drafts show up here with their replies.
      </Empty>
    );
  }
  return (
    <Stack>
      <Alert variant="light" color="gray">
        {data.sent} letters sent from Gmail. The range is where the true reply rate likely sits ({percent(data.level)} likely); while ranges overlap, no angle has shown it is better.
      </Alert>
      {GROUPS.map(({ group, title }) => (
        <Table key={group} striped withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{title}</Table.Th>
              <Table.Th>Sent</Table.Th>
              <Table.Th>Replied</Table.Th>
              <Table.Th>Reply rate</Table.Th>
              <Table.Th>Likely range</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {data.rows
              .filter((r) => r.group === group)
              .map((r) => (
                <Table.Tr key={r.name}>
                  <Table.Td>
                    <Badge variant="light" color="gray">
                      {r.name.replace("_", " ")}
                    </Badge>
                  </Table.Td>
                  <Table.Td>{r.rate.sent}</Table.Td>
                  <Table.Td>{r.rate.replied}</Table.Td>
                  <Table.Td>{percent(r.rate.mean)}</Table.Td>
                  <Table.Td>
                    <Text size="sm" c="dimmed">
                      {percent(r.rate.low)} to {percent(r.rate.high)}
                    </Text>
                  </Table.Td>
                </Table.Tr>
              ))}
          </Table.Tbody>
        </Table>
      ))}
    </Stack>
  );
}
