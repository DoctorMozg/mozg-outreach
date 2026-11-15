import {
  ActionIcon,
  AppShell,
  Badge,
  Burger,
  Button,
  Group,
  NavLink,
  ScrollArea,
  Text,
  Tooltip,
  useComputedColorScheme,
  useMantineColorScheme,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { IconAddressBook, IconFeather, IconFileCv, IconMoon, IconPlus, IconRadar2, IconSun } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import type { ReactElement, ReactNode } from "react";
import { get, type Project } from "../api";
import { NewProjectModal } from "../pages/Projects";
import { href, type Route } from "../router";
import { ModeBadge } from "./common";

export function Shell({ route, children }: { route: Route; children: ReactNode }): ReactElement {
  const [navOpen, nav] = useDisclosure();
  const [creating, create] = useDisclosure();
  const projects = useQuery({ queryKey: ["projects"], queryFn: () => get<Project[]>("/api/projects") });
  const projectId = route.page === "project" ? route.id : null;

  return (
    <AppShell header={{ height: 56 }} navbar={{ width: 270, breakpoint: "sm", collapsed: { mobile: !navOpen } }} padding="lg">
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between" wrap="nowrap">
          <Group gap="sm" wrap="nowrap">
            <Burger opened={navOpen} onClick={nav.toggle} hiddenFrom="sm" size="sm" aria-label="Menu" />
            <a href={href.projects()} className="brand">
              <IconRadar2 size={24} color="var(--mantine-primary-color-filled)" />
              <Text fw={700} size="lg">
                mozg outreach
              </Text>
            </a>
          </Group>
          <Group gap="sm" wrap="nowrap">
            <Tooltip label="Every letter stays a draft. Nothing is ever sent.">
              <Badge variant="light" color="gray" visibleFrom="xs">
                drafts only
              </Badge>
            </Tooltip>
            <SchemeToggle />
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="sm">
        <AppShell.Section>
          <Group justify="space-between" px="xs" mb={4}>
            <Text size="xs" fw={700} c="dimmed" tt="uppercase">
              Projects
            </Text>
            <Tooltip label="New project">
              <ActionIcon variant="subtle" size="sm" onClick={create.open} aria-label="New project">
                <IconPlus size={16} />
              </ActionIcon>
            </Tooltip>
          </Group>
        </AppShell.Section>
        <AppShell.Section grow component={ScrollArea}>
          {projects.data?.map((p) => (
            <NavLink
              key={p.id}
              href={href.project(p.id)}
              label={p.name}
              active={p.id === projectId}
              rightSection={<ModeBadge mode={p.mode} />}
              onClick={nav.close}
            />
          ))}
          {projects.data?.length === 0 && (
            <Button variant="light" fullWidth leftSection={<IconPlus size={16} />} onClick={create.open}>
              New project
            </Button>
          )}
        </AppShell.Section>
        <AppShell.Section pt="sm" style={{ borderTop: "1px solid var(--mantine-color-default-border)" }}>
          <NavLink
            href={href.registry()}
            label="People and companies"
            leftSection={<IconAddressBook size={18} />}
            active={route.page === "registry" || route.page === "entity"}
            onClick={nav.close}
          />
          <NavLink href={href.cvs()} label="Your CVs" leftSection={<IconFileCv size={18} />} active={route.page === "cvs"} onClick={nav.close} />
          <NavLink href={href.voice()} label="Your voice" leftSection={<IconFeather size={18} />} active={route.page === "voice"} onClick={nav.close} />
        </AppShell.Section>
      </AppShell.Navbar>

      <AppShell.Main>{children}</AppShell.Main>
      <NewProjectModal opened={creating} onClose={create.close} />
    </AppShell>
  );
}

function SchemeToggle(): ReactElement {
  const { setColorScheme } = useMantineColorScheme();
  const scheme = useComputedColorScheme("light");
  const dark = scheme === "dark";
  return (
    <Tooltip label={dark ? "Light theme" : "Dark theme"}>
      <ActionIcon variant="default" size="lg" onClick={() => setColorScheme(dark ? "light" : "dark")} aria-label="Toggle color scheme">
        {dark ? <IconSun size={18} /> : <IconMoon size={18} />}
      </ActionIcon>
    </Tooltip>
  );
}
