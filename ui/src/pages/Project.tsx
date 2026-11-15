import { ActionIcon, Alert, Badge, Button, Group, Loader, Menu, Modal, Stack, Tabs, Text, Title } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import {
  IconAlertCircle,
  IconCircleCheck,
  IconCircleDashed,
  IconDots,
  IconFileText,
  IconHistory,
  IconListDetails,
  IconPlugConnected,
  IconTable,
  IconTrash,
} from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ReactElement, ReactNode } from "react";
import { call, type FieldSpecVersion, get, type Project as ProjectRow, type Run, type SearchReadiness, type Source } from "../api";
import { ErrorBox, ModeBadge } from "../components/common";
import { href } from "../router";
import { Brief } from "./Brief";
import { Fields } from "./Fields";
import { Report } from "./Report";
import { Runs } from "./Runs";
import { Sources } from "./Sources";

const OPEN_RUN = new Set<Run["status"]>(["pending", "running", "awaiting_gate"]);

export function Project({ id, tab }: { id: number; tab: string }): ReactElement {
  const project = useQuery({ queryKey: ["project", id], queryFn: () => get<ProjectRow>(`/api/projects/${id}`) });
  const readiness = useQuery({ queryKey: ["search", id], queryFn: () => get<SearchReadiness>(`/api/projects/${id}/search`) });
  const fields = useQuery({ queryKey: ["fields", id], queryFn: () => get<FieldSpecVersion[]>(`/api/projects/${id}/fields`) });
  const sources = useQuery({ queryKey: ["sources", id], queryFn: () => get<Source[]>(`/api/projects/${id}/sources`) });
  const runs = useQuery({ queryKey: ["runs", id], queryFn: () => get<Run[]>(`/api/projects/${id}/runs`), refetchInterval: 5000 });
  const p = project.data;
  if (!p) return <ErrorBox error={project.error} />;

  const biz = p.mode === "biz";
  const accepted = fields.data?.some((v) => v.accepted_at) ?? false;
  const enabledSources = sources.data?.filter((s) => s.enabled).length ?? 0;
  const openRuns = runs.data?.filter((r) => OPEN_RUN.has(r.status)).length ?? 0;
  const blocker = readiness.data?.blocker;
  // The first unfinished step is where the blocker is fixed; Runs shows the blocker itself.
  const fixAt = !p.brief ? "brief" : "fields";

  return (
    <Stack maw={1400} mx="auto">
      <Group justify="space-between" wrap="nowrap" align="flex-start">
        <div>
          <Group gap="sm">
            <Title order={2}>{p.name}</Title>
            <ModeBadge mode={p.mode} />
          </Group>
          <Text size="sm" c="dimmed">
            {biz ? "Client companies for your services" : "Jobs and gigs for one person"} · created {new Date(p.created_at).toLocaleDateString()}
          </Text>
        </div>
        <ProjectMenu project={p} />
      </Group>

      {blocker && tab !== fixAt && tab !== "runs" && (
        <Alert variant="light" color="yellow" icon={<IconAlertCircle size={18} />} title="The search can't start yet">
          <Group justify="space-between">
            <Text size="sm">{capitalize(blocker)}.</Text>
            <Button size="xs" variant="light" color="yellow" component="a" href={href.project(id, fixAt)}>
              Go to {fixAt === "brief" ? "the brief" : "Fields"}
            </Button>
          </Group>
        </Alert>
      )}

      <Tabs value={tab} onChange={(next) => next && (window.location.hash = href.project(id, next))} keepMounted={false}>
        <Tabs.List>
          <Tabs.Tab value="brief" leftSection={<StepIcon done={Boolean(p.brief)} icon={<IconFileText size={16} />} />}>
            Brief
          </Tabs.Tab>
          <Tabs.Tab
            value="fields"
            leftSection={<StepIcon done={accepted} optional={!biz} icon={<IconListDetails size={16} />} />}
            rightSection={!biz && !accepted ? <Text size="xs" c="dimmed">optional</Text> : null}
          >
            Fields
          </Tabs.Tab>
          <Tabs.Tab
            value="sources"
            leftSection={<IconPlugConnected size={16} />}
            rightSection={<Badge size="xs" variant="light" color="gray">{enabledSources}</Badge>}
          >
            Sources
          </Tabs.Tab>
          <Tabs.Tab
            value="runs"
            leftSection={openRuns ? <Loader size={14} /> : <IconHistory size={16} />}
            rightSection={runs.data?.length ? <Badge size="xs" variant="light" color="gray">{runs.data.length}</Badge> : null}
          >
            Runs
          </Tabs.Tab>
          <Tabs.Tab value="report" leftSection={<IconTable size={16} />}>
            Report
          </Tabs.Tab>
        </Tabs.List>
      </Tabs>

      {tab === "brief" && <Brief project={p} />}
      {tab === "fields" && <Fields project={p} />}
      {tab === "sources" && <Sources project={p} />}
      {tab === "runs" && <Runs project={p} blocker={blocker ?? null} />}
      {tab === "report" && (
        <div className="legacy">
          <Report project={p} />
        </div>
      )}
    </Stack>
  );
}

function capitalize(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** A setup step's state: done, still needed, or optional. */
function StepIcon({ done, optional = false, icon }: { done: boolean; optional?: boolean; icon: ReactNode }): ReactElement {
  if (done) return <IconCircleCheck size={16} color="var(--mantine-color-teal-filled)" />;
  if (optional) return <>{icon}</>;
  return <IconCircleDashed size={16} color="var(--mantine-color-yellow-filled)" />;
}

function ProjectMenu({ project }: { project: ProjectRow }): ReactElement {
  const client = useQueryClient();
  const [confirming, confirm] = useDisclosure();
  const remove = useMutation({
    mutationFn: () => call("DELETE", `/api/projects/${project.id}`),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["projects"] });
      window.location.hash = href.projects();
    },
  });
  return (
    <>
      <Menu position="bottom-end" shadow="md">
        <Menu.Target>
          <ActionIcon variant="default" size="lg" aria-label="Project actions">
            <IconDots size={18} />
          </ActionIcon>
        </Menu.Target>
        <Menu.Dropdown>
          <Menu.Item color="red" leftSection={<IconTrash size={16} />} onClick={confirm.open}>
            Delete project
          </Menu.Item>
        </Menu.Dropdown>
      </Menu>
      <Modal opened={confirming} onClose={confirm.close} title={<Text fw={650}>Delete "{project.name}"?</Text>}>
        <Stack>
          <Text size="sm">
            This deletes its runs, report and drafts. People and companies stay in the registry. It can't be undone.
          </Text>
          <ErrorBox error={remove.error} />
          <Group justify="flex-end">
            <Button variant="default" onClick={confirm.close}>
              Cancel
            </Button>
            <Button color="red" loading={remove.isPending} onClick={() => remove.mutate()}>
              Delete project
            </Button>
          </Group>
        </Stack>
      </Modal>
    </>
  );
}
