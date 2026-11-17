import {
  ActionIcon,
  Affix,
  Anchor,
  Badge,
  Button,
  Card,
  Checkbox,
  Divider,
  Drawer,
  Group,
  Popover,
  ScrollArea,
  SegmentedControl,
  Select,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Title,
  Tooltip,
  Transition,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconColumns3, IconExternalLink, IconMail, IconRoute, IconSearch, IconSparkles, IconTable } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactElement, useState } from "react";
import { call, enrichHint, get, type Graph, type Item, type Project, type Run } from "../api";
import { ActivityBadge, Empty, EnrichedBadge, ErrorBox, replanDue, Score, StepBadge, Value } from "../components/common";
import { Duplicates } from "../components/Duplicates";
import { GraphView } from "../components/GraphView";
import { href } from "../router";

const DEFAULT_COLUMNS = 3;

export function Report({ project }: { project: Project }): ReactElement {
  const [view, setView] = useState<"leads" | "graph">("leads");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const range = dateWindow(since, until);
  return (
    <Stack>
      <Group justify="space-between">
        <SegmentedControl
          value={view}
          onChange={(v) => setView(v === "graph" ? "graph" : "leads")}
          data={[
            { value: "leads", label: "Leads" },
            { value: "graph", label: "Connections" },
          ]}
        />
        <Group gap="xs">
          <TextInput type="date" size="xs" aria-label="From" leftSection={<Text size="xs">from</Text>} leftSectionWidth={44} value={since} onChange={(e) => setSince(e.currentTarget.value)} />
          <TextInput type="date" size="xs" aria-label="To" leftSection={<Text size="xs">to</Text>} leftSectionWidth={30} value={until} onChange={(e) => setUntil(e.currentTarget.value)} />
        </Group>
      </Group>
      {project.mode === "biz" && <Duplicates project={project} />}
      {view === "leads" ? <Leads project={project} range={range} /> : <Connections project={project} range={range} />}
    </Stack>
  );
}

function Connections({ project, range }: { project: Project; range: Record<string, string> }): ReactElement {
  const graph = useQuery({
    queryKey: ["graph", project.id, range],
    queryFn: () => get<Graph>(`/api/graph?${new URLSearchParams({ project_id: String(project.id), ...range }).toString()}`),
  });
  return (
    <Card>
      <Text size="sm" c="dimmed" mb="sm">
        Leads, the companies behind them and the people found so far. Click a node to open it.
      </Text>
      <ErrorBox error={graph.error} />
      {graph.data && <GraphView graph={graph.data} />}
    </Card>
  );
}

function Leads({ project, range }: { project: Project; range: Record<string, string> }): ReactElement {
  const client = useQueryClient();
  const [sort, setSort] = useState<"score" | "date">("score");
  const key = ["items", project.id, range, sort];
  const items = useQuery({
    queryKey: key,
    queryFn: () => get<Item[]>(`/api/projects/${project.id}/items?${new URLSearchParams({ ...range, sort }).toString()}`),
  });
  const [query, setQuery] = useState("");
  const [onlyEnriched, setOnlyEnriched] = useState(false);
  const [onlyReplan, setOnlyReplan] = useState(false);
  // Ticked rows for the bulk bar; they live only on this page.
  const [ticked, setTicked] = useState<ReadonlySet<number>>(new Set());
  const tick = (id: number): void =>
    setTicked((was) => {
      const next = new Set(was);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  const [kind, setKind] = useState<Item["kind"] | "all">("all");
  const [openId, setOpenId] = useState<number | null>(null);
  const allColumns = [...new Set((items.data ?? []).flatMap((i) => Object.keys(i.fields)))];
  const [picked, setPicked] = useStoredColumns(project.id);
  const columns = (picked ?? allColumns.slice(0, DEFAULT_COLUMNS)).filter((c) => allColumns.includes(c));

  const enrich = useMutation({
    mutationFn: (item: Item) => call<Run>("POST", `/api/items/${item.id}/enrich`),
    onSuccess: (run, item) => {
      void client.invalidateQueries({ queryKey: ["items", project.id] });
      void client.invalidateQueries({ queryKey: ["runs", project.id] });
      notifications.show({
        color: "indigo",
        title: `Enriching ${item.title}`,
        message: <Anchor href={href.run(run.id)}>Follow run {run.id}</Anchor>,
      });
    },
  });
  const enriching = (item: Item): boolean => enrich.isPending && enrich.variables.id === item.id;

  const needle = query.trim().toLowerCase();
  const rows = (items.data ?? []).filter(
    (i) => (!onlyEnriched || i.enriched) && (!onlyReplan || replanDue(i.approach_step)) && (kind === "all" || i.kind === kind) && (!needle || i.title.toLowerCase().includes(needle)),
  );
  // Bulk actions take only the ticked rows on screen, so a filter never hides what they touch.
  const chosen = rows.filter((i) => ticked.has(i.id));
  const seeker = project.mode === "seeker";
  const open = items.data?.find((i) => i.id === openId) ?? null;

  if (items.data?.length === 0) {
    return (
      <Empty icon={<IconTable size={36} />} title="No leads yet">
        Run a search and its scored leads land here.
      </Empty>
    );
  }

  return (
    <Card p={0}>
      <Group justify="space-between" p="md" gap="sm">
        <Group gap="sm">
          <Title order={4}>{items.data ? `${rows.length} of ${items.data.length} leads` : "Leads"}</Title>
          <TextInput
            size="xs"
            placeholder="Search leads"
            leftSection={<IconSearch size={14} />}
            value={query}
            onChange={(e) => setQuery(e.currentTarget.value)}
            w={220}
          />
          {seeker && (
            <SegmentedControl
              size="xs"
              value={kind}
              onChange={(v) => setKind(v as Item["kind"] | "all")}
              data={[
                { value: "all", label: "All" },
                { value: "job", label: "Jobs" },
                { value: "gig", label: "Gigs" },
              ]}
            />
          )}
          <Switch size="sm" label="Enriched only" checked={onlyEnriched} onChange={(e) => setOnlyEnriched(e.currentTarget.checked)} />
          {!seeker && <Switch size="sm" label="Re-plan due" checked={onlyReplan} onChange={(e) => setOnlyReplan(e.currentTarget.checked)} />}
        </Group>
        <Group gap="xs">
          <Select
            size="xs"
            w={170}
            allowDeselect={false}
            value={sort}
            onChange={(v) => setSort(v === "date" ? "date" : "score")}
            data={[
              { value: "score", label: "Best score first" },
              { value: "date", label: "Newest first" },
            ]}
          />
          <ColumnPicker all={allColumns} shown={columns} onChange={setPicked} />
        </Group>
      </Group>
      <ErrorBox error={items.error ?? enrich.error} />
      <ScrollArea type="auto">
        <Table highlightOnHover stickyHeader verticalSpacing="sm" miw={760}>
          <Table.Thead>
            <Table.Tr>
              <Table.Th w={44} />
              <Table.Th w={70}>Score</Table.Th>
              <Table.Th miw={260}>Lead</Table.Th>
              {columns.map((c) => (
                <Table.Th key={c} miw={180}>
                  {c.replaceAll("_", " ")}
                </Table.Th>
              ))}
              <Table.Th w={110}>
                <Tooltip label="Posted on, or the day a run found it">
                  <span>Date</span>
                </Tooltip>
              </Table.Th>
              <Table.Th w={50} />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((item) => (
              <Table.Tr
                key={item.id}
                bg={ticked.has(item.id) ? "var(--mantine-primary-color-light)" : undefined}
                style={{ cursor: "pointer" }}
                onClick={() => setOpenId(item.id)}
              >
                <Table.Td onClick={(e) => e.stopPropagation()}>
                  <Checkbox checked={ticked.has(item.id)} onChange={() => tick(item.id)} aria-label={`Tick ${item.title}`} />
                </Table.Td>
                <Table.Td>
                  <Score value={item.score_total} />
                </Table.Td>
                <Table.Td>
                  <Text fw={550} size="sm">
                    {item.title}
                  </Text>
                  <Group gap={4} mt={2}>
                    <ActivityBadge activity={item.activity} projectId={project.id} />
                    <EnrichedBadge item={item} />
                    <StepBadge step={item.approach_step} />
                    {seeker && (
                      <Badge size="xs" variant="outline" color="gray">
                        {item.kind}
                      </Badge>
                    )}
                    {item.score?.blockers.map((b) => (
                      <Badge key={b} size="xs" color="red" variant="light">
                        {b}
                      </Badge>
                    ))}
                  </Group>
                </Table.Td>
                {columns.map((c) => (
                  <Table.Td key={c}>
                    <Text size="sm" lineClamp={2} component="div">
                      <Value field={item.fields[c]} />
                    </Text>
                  </Table.Td>
                ))}
                <Table.Td c="dimmed" fz="sm" style={{ whiteSpace: "nowrap" }}>
                  {item.dated}
                </Table.Td>
                <Table.Td onClick={(e) => e.stopPropagation()}>
                  {item.kind !== "gig" && (
                    <Tooltip label={enrichHint(item).note ?? "Enrich: find people, contacts and signals"}>
                      <ActionIcon variant="subtle" loading={enriching(item)} onClick={() => enrich.mutate(item)} aria-label={`${enrichHint(item).label} ${item.title}`}>
                        <IconSparkles size={16} />
                      </ActionIcon>
                    </Tooltip>
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </ScrollArea>
      <BulkBar project={project} chosen={chosen} onStarted={() => setTicked(new Set())} />
      <Drawer opened={open !== null} onClose={() => setOpenId(null)} position="right" size="xl" title={<Text fw={650}>{open?.title}</Text>}>
        {open && <LeadPreview item={open} onEnrich={() => enrich.mutate(open)} enriching={enriching(open)} />}
      </Drawer>
    </Card>
  );
}

/** Which field columns show, remembered per project in this browser only. */
function useStoredColumns(projectId: number): [string[] | null, (columns: string[]) => void] {
  const storageKey = `report-columns-${projectId}`;
  const [columns, setColumns] = useState<string[] | null>(() => {
    try {
      const raw = localStorage.getItem(storageKey);
      return raw ? (JSON.parse(raw) as string[]) : null;
    } catch {
      return null; // storage blocked or corrupt: fall back to the default columns
    }
  });
  const store = (next: string[]): void => {
    setColumns(next);
    try {
      localStorage.setItem(storageKey, JSON.stringify(next));
    } catch {
      // A private window may refuse storage; the choice then lasts for this visit only.
    }
  };
  return [columns, store];
}

function ColumnPicker({ all, shown, onChange }: { all: string[]; shown: string[]; onChange: (c: string[]) => void }): ReactElement {
  return (
    <Popover position="bottom-end" shadow="md" width={280}>
      <Popover.Target>
        <Button size="xs" variant="default" leftSection={<IconColumns3 size={14} />}>
          Columns {shown.length}/{all.length}
        </Button>
      </Popover.Target>
      <Popover.Dropdown>
        <ScrollArea.Autosize mah={360}>
          <Checkbox.Group value={shown} onChange={(next) => onChange(all.filter((c) => next.includes(c)))}>
            <Stack gap={6}>
              {all.map((c) => (
                <Checkbox key={c} value={c} label={c.replaceAll("_", " ")} size="xs" />
              ))}
            </Stack>
          </Checkbox.Group>
        </ScrollArea.Autosize>
      </Popover.Dropdown>
    </Popover>
  );
}

/** Names what the letters step writes for the ticked jobs and gigs. */
function lettersFor(kinds: Set<Item["kind"]>): string {
  const names = [kinds.has("job") && "cover letters", kinds.has("gig") && "proposals"].filter(Boolean);
  return `Write ${names.join(" and ")}`;
}

function BulkBar({ project, chosen, onStarted }: { project: Project; chosen: Item[]; onStarted: () => void }): ReactElement {
  // Gigs are answered on their own board, so there is nothing to enrich.
  const enrichable = chosen.filter((i) => i.kind !== "gig");
  // A company's letters come from its approach plan, so companies re-plan instead.
  const writable = chosen.filter((i) => i.kind !== "company");
  const replannable = chosen.filter((i) => i.kind === "company" && i.enriched);
  const bulk = useMutation({
    mutationFn: async ({ action, items }: { action: "enrich" | "letters" | "approach"; items: Item[] }) => {
      // One run per item; the server queues them behind its run limit.
      const results = await Promise.allSettled(items.map((i) => call("POST", `/api/items/${i.id}/${action}`)));
      const failed = results.filter((r) => r.status === "rejected").map((r) => String((r as PromiseRejectedResult).reason));
      if (failed.length) throw new Error(failed.join("\n"));
      return items.length;
    },
    onSuccess: (count) => {
      notifications.show({ color: "indigo", message: `${count} runs started` });
      onStarted();
      window.location.hash = href.project(project.id, "runs");
    },
  });
  return (
    <Affix position={{ bottom: 24, left: 0, right: 0 }} zIndex={150}>
      <Transition mounted={chosen.length > 0} transition="slide-up">
        {(styles) => (
          <Group justify="center" style={styles}>
            <Card shadow="lg" py="sm" px="md" withBorder>
              <Group gap="sm">
                <Text fw={600} size="sm">
                  {chosen.length} ticked
                </Text>
                <Divider orientation="vertical" />
                {enrichable.length > 0 && (
                  <Button
                    size="xs"
                    variant="default"
                    leftSection={<IconSparkles size={14} />}
                    loading={bulk.isPending}
                    onClick={() => bulk.mutate({ action: "enrich", items: enrichable })}
                  >
                    Enrich {enrichable.length}
                  </Button>
                )}
                {replannable.length > 0 && (
                  <Tooltip label="Plans the approach again and writes a letter for each step not done yet">
                    <Button size="xs" leftSection={<IconRoute size={14} />} loading={bulk.isPending} onClick={() => bulk.mutate({ action: "approach", items: replannable })}>
                      Re-plan {replannable.length}
                    </Button>
                  </Tooltip>
                )}
                {writable.length > 0 && (
                  <Button size="xs" leftSection={<IconMail size={14} />} loading={bulk.isPending} onClick={() => bulk.mutate({ action: "letters", items: writable })}>
                    {lettersFor(new Set(writable.map((i) => i.kind)))} for {writable.length}
                  </Button>
                )}
              </Group>
              {bulk.error && (
                <Text size="xs" c="red" mt={4}>
                  {bulk.error.message}
                </Text>
              )}
            </Card>
          </Group>
        )}
      </Transition>
    </Affix>
  );
}

interface PreviewProps {
  item: Item;
  onEnrich: () => void;
  enriching: boolean;
}

function LeadPreview({ item, onEnrich, enriching }: PreviewProps): ReactElement {
  return (
    <Stack>
      <Group justify="space-between">
        <Group gap="sm">
          <Score value={item.score_total} size="lg" />
          <Badge variant="outline" color="gray">
            {item.kind}
          </Badge>
          <ActivityBadge activity={item.activity} projectId={item.project_id} size="md" />
          <EnrichedBadge item={item} size="md" />
          <StepBadge step={item.approach_step} size="md" />
          <Text size="sm" c="dimmed">
            {item.dated}
          </Text>
        </Group>
        <Group gap="xs">
          {item.kind !== "gig" && (
            <Button size="xs" variant="default" leftSection={<IconSparkles size={14} />} loading={enriching} onClick={onEnrich}>
              {enrichHint(item).label}
            </Button>
          )}
          <Button size="xs" variant="light" component="a" href={href.item(item.id)} rightSection={<IconExternalLink size={14} />}>
            Open lead
          </Button>
        </Group>
      </Group>
      {item.score?.blockers.length ? (
        <Group gap={4}>
          {item.score.blockers.map((b) => (
            <Badge key={b} color="red" variant="light">
              {b}
            </Badge>
          ))}
        </Group>
      ) : null}
      {item.score && (
        <>
          <Title order={5}>Why this score</Title>
          <Axes item={item} />
        </>
      )}
      <Title order={5}>Fields</Title>
      <FieldList fields={item.fields} />
    </Stack>
  );
}

export function FieldList({ fields }: { fields: Item["fields"] }): ReactElement {
  return (
    <Table variant="vertical" layout="fixed" withTableBorder>
      <Table.Tbody>
        {Object.entries(fields).map(([name, field]) => (
          <Table.Tr key={name}>
            <Table.Th w={170} style={{ verticalAlign: "top" }}>
              {name.replaceAll("_", " ")}
            </Table.Th>
            <Table.Td>
              <Value field={field} />
              {field.quote && (
                <Text size="xs" c="dimmed" fs="italic" mt={4}>
                  "{field.quote}"
                </Text>
              )}
              {field.source_url && (
                <Anchor href={field.source_url} target="_blank" rel="noreferrer noopener" size="xs" c="dimmed" style={{ wordBreak: "break-all" }}>
                  {field.source_url}
                </Anchor>
              )}
            </Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  );
}

/** Only the ends the user set, so an empty input means no bound. */
function dateWindow(since: string, until: string): Record<string, string> {
  return { ...(since ? { since } : {}), ...(until ? { until } : {}) };
}

export function Axes({ item }: { item: Item }): ReactElement | null {
  if (!item.score) return null;
  return (
    <Table withTableBorder verticalSpacing="xs" fz="sm">
      <Table.Thead>
        <Table.Tr>
          <Table.Th>Axis</Table.Th>
          <Table.Th w={70}>Score</Table.Th>
          <Table.Th w={70}>Weight</Table.Th>
          <Table.Th>Reason</Table.Th>
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {item.score.axes.map((a) => (
          <Table.Tr key={a.axis}>
            <Table.Td fw={500}>{a.axis.replaceAll("_", " ")}</Table.Td>
            <Table.Td>
              <Score value={a.value} size="sm" />
            </Table.Td>
            <Table.Td c="dimmed">x{a.weight}</Table.Td>
            <Table.Td>{a.reason}</Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  );
}
