import { Alert, Button, Group, List, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconCopy } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactElement, useState } from "react";
import { call, type DuplicateGroup, get, type Project } from "../api";
import { ErrorBox } from "./common";

/** Companies saved twice in this project, with a two-click merge into the lead holding the most work. */
export function Duplicates({ project }: { project: Project }): ReactElement | null {
  const client = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const groups = useQuery({
    queryKey: ["duplicates", project.id],
    queryFn: () => get<DuplicateGroup[]>(`/api/projects/${project.id}/duplicates`),
  });
  const merge = useMutation({
    mutationFn: () => call<DuplicateGroup[]>("POST", `/api/projects/${project.id}/duplicates/merge`),
    onSuccess: (merged) => {
      setConfirming(false);
      notifications.show({ color: "teal", message: `Merged ${merged.length} companies saved twice.` });
      void client.invalidateQueries({ queryKey: ["duplicates", project.id] });
      void client.invalidateQueries({ queryKey: ["items", project.id] });
    },
  });
  const found = groups.data ?? [];
  if (found.length === 0) return null;
  return (
    <Alert variant="light" color="yellow" icon={<IconCopy size={18} />} title={`${found.length} companies are saved twice`}>
      <List size="sm" spacing={2}>
        {found.map((g) => (
          <List.Item key={g.keep.id}>
            Keep <b>{g.keep.title}</b> (#{g.keep.id}), fold in {g.drop.map((d) => `#${d.id}`).join(", ")}
          </List.Item>
        ))}
      </List>
      <Text size="xs" c="dimmed" mt="xs">
        The kept lead is the one with sent mail, history or a plan. History, Gmail threads and people move to it. Drafts of the other lead's plan are deleted unless they are in Gmail.
      </Text>
      <ErrorBox error={merge.error} />
      <Group mt="sm" gap="xs">
        {confirming ? (
          <>
            <Button size="xs" color="yellow" loading={merge.isPending} onClick={() => merge.mutate()}>
              Merge {found.length} now
            </Button>
            <Button size="xs" variant="default" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
          </>
        ) : (
          <Button size="xs" variant="light" color="yellow" onClick={() => setConfirming(true)}>
            Merge duplicates
          </Button>
        )}
      </Group>
    </Alert>
  );
}
