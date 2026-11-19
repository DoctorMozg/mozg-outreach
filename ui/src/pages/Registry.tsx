import { Anchor, Badge, Button, Card, Group, SegmentedControl, Stack, Table, Text, TextInput, Title } from "@mantine/core";
import { IconBuilding, IconSearch, IconUser, IconWorldSearch } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactElement, useState } from "react";
import { call, type Entity, type EntityDetail, get, type Graph, type MergeSuggestion, type Run } from "../api";
import { ActivityBadge, Empty, ErrorBox, Value } from "../components/common";
import { GraphView } from "../components/GraphView";
import { NoteButton, type NoteText } from "../components/NoteButton";
import { href } from "../router";

function KindBadge({ kind }: { kind: Entity["kind"] }): ReactElement {
  return (
    <Badge size="sm" variant="light" color={kind === "person" ? "teal" : "indigo"} leftSection={kind === "person" ? <IconUser size={12} /> : <IconBuilding size={12} />}>
      {kind}
    </Badge>
  );
}

export function Registry(): ReactElement {
  const [view, setView] = useState<"list" | "graph">("list");
  const graph = view === "graph";
  return (
    // The graph takes the whole main panel: the viewport minus the header and the shell padding.
    <Stack maw={graph ? undefined : 1200} mx="auto" h={graph ? "calc(100dvh - var(--app-shell-header-offset, 0rem) - 2 * var(--app-shell-padding))" : undefined}>
      <Group justify="space-between" align="flex-end">
        <div>
          <Title order={2}>People and companies</Title>
          <Text size="sm" c="dimmed">
            {graph
              ? "Every person and company with the leads they belong to, the most recently updated first when there are too many to draw. Click a node to open it."
              : "One registry for every project. Each profile lists every place it was seen."}
          </Text>
        </div>
        <SegmentedControl
          value={view}
          onChange={(v) => setView(v === "graph" ? "graph" : "list")}
          data={[
            { value: "list", label: "List" },
            { value: "graph", label: "Graph" },
          ]}
        />
      </Group>
      {graph ? (
        <RegistryGraph />
      ) : (
        <>
          <Merges />
          <EntityList />
        </>
      )}
    </Stack>
  );
}

function RegistryGraph(): ReactElement {
  const graph = useQuery({ queryKey: ["graph", "registry"], queryFn: () => get<Graph>("/api/graph") });
  return (
    <>
      <ErrorBox error={graph.error} />
      {graph.data && <GraphView graph={graph.data} fill />}
    </>
  );
}

function EntityList(): ReactElement {
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const [seenSince, setSeenSince] = useState("");
  const entities = useQuery({
    queryKey: ["entities", q, kind, seenSince],
    queryFn: () =>
      get<Entity[]>(
        `/api/entities?${new URLSearchParams({ q, ...(kind ? { kind } : {}), ...(seenSince ? { seen_since: seenSince } : {}) }).toString()}`,
      ),
  });
  return (
    <Card>
      <Group mb="md" gap="sm">
        <TextInput placeholder="Search names" leftSection={<IconSearch size={14} />} value={q} onChange={(e) => setQ(e.currentTarget.value)} w={260} />
        <SegmentedControl
          value={kind}
          onChange={setKind}
          data={[
            { value: "", label: "All" },
            { value: "company", label: "Companies" },
            { value: "person", label: "People" },
          ]}
        />
        <TextInput
          type="date"
          aria-label="Seen since"
          leftSection={<Text size="xs">seen since</Text>}
          leftSectionWidth={78}
          value={seenSince}
          onChange={(e) => setSeenSince(e.currentTarget.value)}
        />
      </Group>
      <ErrorBox error={entities.error} />
      {entities.data?.length === 0 ? (
        <Empty icon={<IconUser size={32} />} title="Nobody here yet">
          Enrich a lead and the people and companies it finds land here.
        </Empty>
      ) : (
        <Table highlightOnHover verticalSpacing="xs">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Name</Table.Th>
              <Table.Th w={130}>Kind</Table.Th>
              <Table.Th w={180}>Updated</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {entities.data?.map((e) => (
              <Table.Tr key={e.id} style={{ cursor: "pointer" }} onClick={() => (window.location.hash = href.entity(e.id))}>
                <Table.Td fw={500}>{e.name}</Table.Td>
                <Table.Td>
                  <KindBadge kind={e.kind} />
                </Table.Td>
                <Table.Td c="dimmed" fz="sm">
                  {new Date(e.updated_at).toLocaleDateString()}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Card>
  );
}

function Merges(): ReactElement | null {
  const client = useQueryClient();
  const suggestions = useQuery({ queryKey: ["merges"], queryFn: () => get<MergeSuggestion[]>("/api/merge-suggestions") });
  const decide = useMutation({
    mutationFn: ({ id, accept }: { id: number; accept: boolean }) => call("POST", `/api/merge-suggestions/${id}/${accept ? "accept" : "reject"}`),
    onSuccess: () => client.invalidateQueries(),
  });
  if (!suggestions.data?.length) return null;
  return (
    <Card style={{ borderColor: "var(--mantine-color-yellow-filled)" }}>
      <Title order={4} mb={4}>
        Possible duplicates
      </Title>
      <Text size="sm" c="dimmed" mb="md">
        Exact identifiers merge on their own; these only look alike. Merging keeps the first and moves everything onto it.
      </Text>
      <Stack gap="xs">
        {suggestions.data.map((s) => (
          <Group key={s.id} justify="space-between" wrap="nowrap">
            <Text size="sm">
              <Anchor href={href.entity(s.a.id)}>{s.a.name}</Anchor> and <Anchor href={href.entity(s.b.id)}>{s.b.name}</Anchor>{" "}
              <Text span c="dimmed" size="sm">
                {s.reason}
              </Text>
            </Text>
            <Group gap="xs" wrap="nowrap">
              <Button size="xs" loading={decide.isPending && decide.variables.id === s.id && decide.variables.accept} onClick={() => decide.mutate({ id: s.id, accept: true })}>
                Merge
              </Button>
              <Button
                size="xs"
                variant="default"
                loading={decide.isPending && decide.variables.id === s.id && !decide.variables.accept}
                onClick={() => decide.mutate({ id: s.id, accept: false })}
              >
                Different
              </Button>
            </Group>
          </Group>
        ))}
      </Stack>
      <ErrorBox error={decide.error} />
    </Card>
  );
}

const RESEARCH_NOTE: NoteText = {
  prompt: "Anything to look out for?",
  description: "Optional, e.g. the LinkedIn is someone else's, they moved to Acme, look for the CTO.",
  footer: "Wrong matches are listed on the profile and stay off it unless you restore them.",
  submit: "Start search",
};

const KIND_LABELS: Record<string, string> = { linkedin: "LinkedIn", domain: "website", email: "email" };

export function EntityView({ id }: { id: number }): ReactElement {
  const client = useQueryClient();
  const entity = useQuery({
    queryKey: ["entity", id],
    queryFn: () => get<EntityDetail>(`/api/entities/${id}`),
    // While a search runs elsewhere, the profile picks up its result when it lands.
    refetchInterval: (query) => (query.state.data?.research_run ? 5000 : false),
  });
  const [depth, setDepth] = useState("1");
  const graph = useQuery({ queryKey: ["graph", "entity", id, depth], queryFn: () => get<Graph>(`/api/graph?entity_id=${id}&depth=${depth}`) });
  const research = useMutation({
    mutationFn: (note: string | null) => call<Run>("POST", `/api/entities/${id}/research`, { note }),
    onSuccess: (run) => (window.location.hash = href.run(run.id)),
  });
  const restore = useMutation({
    mutationFn: (rejectedId: number) => call("DELETE", `/api/entities/${id}/rejected/${rejectedId}`),
    onSuccess: () => {
      for (const queryKey of [["entity", id], ["graph"]]) void client.invalidateQueries({ queryKey });
    },
  });
  const e = entity.data;
  if (!e) return <ErrorBox error={entity.error} />;
  const profileRows = [
    ...e.identifiers.map((i) => ({ key: `id-${i.kind}-${i.value}`, name: i.kind, value: <Text size="sm">{i.value}</Text> })),
    ...Object.entries(e.profile).map(([name, field]) => ({ key: name, name, value: <Value field={field} /> })),
    ...e.rejected.map((r) => ({
      key: `rejected-${r.kind}-${r.value}`,
      name: `removed ${KIND_LABELS[r.kind] ?? r.kind}`,
      value: (
        <Group gap="xs" wrap="nowrap" justify="space-between" align="flex-start">
          <Text size="sm">
            <Text span td="line-through" c="dimmed">
              {r.value}
            </Text>{" "}
            <Text span fs="italic">
              "{r.quote}"
            </Text>{" "}
            <Anchor href={r.source_url} target="_blank" rel="noreferrer noopener" size="sm">
              source
            </Anchor>
          </Text>
          <Button size="compact-xs" variant="subtle" loading={restore.isPending && restore.variables === r.id} onClick={() => restore.mutate(r.id)}>
            Restore
          </Button>
        </Group>
      ),
    })),
  ];
  return (
    <Stack maw={1200} mx="auto">
      <Anchor href={href.registry()} size="sm">
        People and companies
      </Anchor>
      <Group justify="space-between">
        <Group gap="sm">
          <Title order={2}>{e.name}</Title>
          <KindBadge kind={e.kind} />
        </Group>
        <Group gap="sm">
          {e.research_run !== null && (
            <Anchor href={href.run(e.research_run)} size="sm">
              Search running, open run {e.research_run}
            </Anchor>
          )}
          <NoteButton
            label="Search again"
            text={RESEARCH_NOTE}
            icon={<IconWorldSearch size={14} />}
            loading={research.isPending}
            disabled={e.research_run !== null}
            onStart={(note) => research.mutateAsync(note)}
          />
        </Group>
      </Group>
      <ErrorBox error={research.error ?? restore.error} />
      {profileRows.length > 0 && (
        <Card>
          <Title order={4} mb="sm">
            Profile
          </Title>
          <Table variant="vertical" layout="fixed" withTableBorder>
            <Table.Tbody>
              {profileRows.map((row) => (
                <Table.Tr key={row.key}>
                  <Table.Th w={180}>{row.name.replaceAll("_", " ")}</Table.Th>
                  <Table.Td>{row.value}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Card>
      )}
      <Card>
        <Group justify="space-between" mb="sm">
          <Title order={4}>Connections</Title>
          <Group gap="xs">
            <Text size="sm" c="dimmed">
              Graph depth
            </Text>
            <SegmentedControl size="xs" value={depth} onChange={setDepth} data={["1", "2", "3"]} />
          </Group>
        </Group>
        <Stack gap={6} mb="md">
          {e.relations.map((r, n) => (
            <Text key={n} size="sm">
              {r.direction === "out" && <Text span c="dimmed">{r.type} </Text>}
              <Anchor href={href.entity(r.other_id)}>{r.other_name}</Anchor>
              {r.direction === "in" && <Text span c="dimmed"> ({r.type})</Text>}
              {r.detail && (
                <Text span c="dimmed">
                  {" "}
                  {r.detail}
                </Text>
              )}
              {r.source_url && (
                <Anchor href={r.source_url} target="_blank" rel="noreferrer noopener" size="xs" ml={6}>
                  source
                </Anchor>
              )}
            </Text>
          ))}
          {e.leads.map((l) => (
            <Text key={`lead-${l.item_id}`} size="sm">
              <Badge size="xs" variant="light" color="orange" mr={6}>
                lead
              </Badge>
              <Anchor href={href.item(l.item_id)}>{l.title}</Anchor>{" "}
              <Text span c="dimmed">
                {l.role}
              </Text>{" "}
              <ActivityBadge activity={l.activity} projectId={l.project_id} />
            </Text>
          ))}
        </Stack>
        <ErrorBox error={graph.error} />
        {graph.data && <GraphView graph={graph.data} />}
      </Card>
      <Card>
        <Title order={4} mb="sm">
          Seen in {e.sightings.length} {e.sightings.length === 1 ? "place" : "places"}
        </Title>
        <Stack gap="sm">
          {e.sightings.map((s, n) => (
            <div key={n}>
              <Group gap={6} wrap="nowrap">
                <Badge size="xs" variant="outline" color="gray">
                  {s.source_kind.replaceAll("_", " ")}
                </Badge>
                <Anchor href={s.source_url} target="_blank" rel="noreferrer noopener" size="sm" style={{ wordBreak: "break-all" }}>
                  {s.source_url}
                </Anchor>
              </Group>
              {s.quote && (
                <Text size="xs" c="dimmed" fs="italic" mt={2}>
                  "{s.quote}"
                </Text>
              )}
            </div>
          ))}
        </Stack>
      </Card>
    </Stack>
  );
}
