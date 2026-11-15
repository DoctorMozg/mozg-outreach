import { Button, Card, Group, Modal, Radio, SimpleGrid, Stack, Text, TextInput, Title } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { IconBriefcase, IconBuildingSkyscraper, IconFolderPlus, IconPlus } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactElement, useState } from "react";
import { call, get, type Mode, type Project } from "../api";
import { Empty, ErrorBox, ModeBadge } from "../components/common";
import { href } from "../router";

const MODES: { mode: Mode; label: string; description: string; icon: ReactElement }[] = [
  {
    mode: "biz",
    label: "Business",
    description: "Find client companies for your services and draft outreach letters.",
    icon: <IconBuildingSkyscraper size={20} />,
  },
  {
    mode: "seeker",
    label: "Seeker",
    description: "Find salaried jobs and freelance gigs for one person and draft cover letters or proposals.",
    icon: <IconBriefcase size={20} />,
  },
];

export function Projects(): ReactElement {
  const projects = useQuery({ queryKey: ["projects"], queryFn: () => get<Project[]>("/api/projects") });
  const [creating, create] = useDisclosure();
  return (
    <Stack maw={1100} mx="auto">
      <Group justify="space-between">
        <div>
          <Title order={2}>Projects</Title>
          <Text c="dimmed" size="sm">
            A project holds one brief, its sources, its runs and its report.
          </Text>
        </div>
        <Button leftSection={<IconPlus size={16} />} onClick={create.open}>
          New project
        </Button>
      </Group>
      <ErrorBox error={projects.error} />
      {projects.data?.length === 0 && (
        <Empty icon={<IconFolderPlus size={36} />} title="No projects yet">
          Create one for client outreach or for your own job search.
        </Empty>
      )}
      <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }}>
        {projects.data?.map((p) => (
          <Card key={p.id} component="a" href={href.project(p.id)} style={{ textDecoration: "none" }} className="hover-card">
            <Group justify="space-between" mb="xs" wrap="nowrap">
              <Text fw={650} lineClamp={1}>
                {p.name}
              </Text>
              <ModeBadge mode={p.mode} />
            </Group>
            <Text size="sm" c="dimmed">
              {p.brief ? "Brief written" : "No brief yet"} · created {new Date(p.created_at).toLocaleDateString()}
            </Text>
          </Card>
        ))}
      </SimpleGrid>
      <NewProjectModal opened={creating} onClose={create.close} />
    </Stack>
  );
}

export function NewProjectModal({ opened, onClose }: { opened: boolean; onClose: () => void }): ReactElement {
  const client = useQueryClient();
  const [name, setName] = useState("");
  const [mode, setMode] = useState<Mode>("biz");
  const [sender, setSender] = useState("");
  const create = useMutation({
    mutationFn: () => call<Project>("POST", "/api/projects", { name, mode, sender_name: sender || null }),
    onSuccess: (project) => {
      void client.invalidateQueries({ queryKey: ["projects"] });
      setName("");
      onClose();
      window.location.hash = href.project(project.id);
    },
  });
  return (
    <Modal opened={opened} onClose={onClose} title={<Text fw={650}>New project</Text>} size="lg">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        <Stack>
          <TextInput label="Name" placeholder="UAE law firms" required value={name} onChange={(e) => setName(e.currentTarget.value)} data-autofocus />
          <Radio.Group label="What is it for?" value={mode} onChange={(value) => setMode(value as Mode)}>
            <SimpleGrid cols={{ base: 1, sm: 2 }} mt="xs">
              {MODES.map((m) => (
                <Radio.Card key={m.mode} value={m.mode} p="md" radius="md">
                  <Group wrap="nowrap" align="flex-start">
                    <Radio.Indicator />
                    <div>
                      <Group gap={6}>
                        {m.icon}
                        <Text fw={600}>{m.label}</Text>
                      </Group>
                      <Text size="sm" c="dimmed" mt={4}>
                        {m.description}
                      </Text>
                    </div>
                  </Group>
                </Radio.Card>
              ))}
            </SimpleGrid>
          </Radio.Group>
          <TextInput
            label="Your name"
            description="Signs every letter. You can change it later on the brief."
            value={sender}
            onChange={(e) => setSender(e.currentTarget.value)}
          />
          <ErrorBox error={create.error} />
          <Group justify="flex-end">
            <Button variant="default" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={!name.trim()} loading={create.isPending}>
              Create
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
