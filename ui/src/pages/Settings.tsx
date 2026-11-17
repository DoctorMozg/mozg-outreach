import { Button, Card, Group, NumberInput, Stack, Switch, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactElement, useEffect, useState } from "react";
import { call, get, type UserSettings } from "../api";
import { ErrorBox } from "../components/common";

const DEFAULT_MIN_SCORE = 80;

export function Settings(): ReactElement {
  const client = useQueryClient();
  const saved = useQuery({ queryKey: ["settings"], queryFn: () => get<UserSettings>("/api/settings") });
  const [minScore, setMinScore] = useState<number | null>(null);
  useEffect(() => {
    if (saved.data) setMinScore(saved.data.auto_enrich_min_score);
  }, [saved.data]);
  const save = useMutation({
    mutationFn: (body: UserSettings) => call<UserSettings>("PUT", "/api/settings", body),
    onSuccess: (data) => {
      client.setQueryData(["settings"], data);
      notifications.show({ color: "teal", message: "Settings saved" });
    },
  });
  const on = minScore !== null;
  const changed = saved.data !== undefined && minScore !== saved.data.auto_enrich_min_score;

  return (
    <Stack maw={760} mx="auto">
      <div>
        <Title order={2}>Settings</Title>
        <Text size="sm" c="dimmed">
          These apply to every project.
        </Text>
      </div>
      <ErrorBox error={saved.error ?? save.error} />
      <Card withBorder padding="lg">
        <Stack gap="sm">
          <Switch
            label="Enrich the best leads after each search"
            description="Every lead a search scores at or above the minimum gets a full enrichment run, as if you clicked Enrich. Each one is a paid agent run; leads already enriched or archived are skipped."
            checked={on}
            disabled={!saved.data}
            onChange={(e) => setMinScore(e.currentTarget.checked ? DEFAULT_MIN_SCORE : null)}
          />
          <NumberInput
            label="Minimum score"
            min={0}
            max={100}
            clampBehavior="strict"
            allowDecimal={false}
            maw={200}
            disabled={!on}
            value={minScore ?? DEFAULT_MIN_SCORE}
            onChange={(v) => setMinScore(typeof v === "number" ? v : DEFAULT_MIN_SCORE)}
          />
          <Group justify="flex-end">
            <Button disabled={!changed} loading={save.isPending} onClick={() => save.mutate({ auto_enrich_min_score: minScore })}>
              Save
            </Button>
          </Group>
        </Stack>
      </Card>
    </Stack>
  );
}
