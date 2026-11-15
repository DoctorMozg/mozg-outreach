import { ActionIcon, Badge, Button, Card, Grid, Group, Stack, Tabs, Text, Textarea, Title, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconPlus, IconTrash, IconWand } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactElement, useEffect, useState } from "react";
import { call, get, type Channel, type Sample, type VoiceProfile } from "../api";
import { Empty, ErrorBox } from "../components/common";

const CHANNELS: { channel: Channel; label: string }[] = [
  { channel: "email", label: "Emails" },
  { channel: "dm", label: "Direct messages" },
  { channel: "cover_letter", label: "Cover letters" },
  { channel: "proposal", label: "Proposals" },
];
const MIN_SAMPLES = 3;

export function Voice(): ReactElement {
  const [channel, setChannel] = useState<Channel>("email");
  return (
    <Stack maw={1400} mx="auto">
      <div>
        <Title order={2}>Your voice</Title>
        <Text size="sm" c="dimmed" maw={760}>
          Paste messages you wrote yourself. Every generated letter is rewritten toward the profile built from them, and your own rules win
          over everything the profiler noticed.
        </Text>
      </div>
      <Tabs value={channel} onChange={(v) => v && setChannel(v as Channel)} keepMounted={false}>
        <Tabs.List>
          {CHANNELS.map((c) => (
            <Tabs.Tab key={c.channel} value={c.channel}>
              {c.label}
            </Tabs.Tab>
          ))}
        </Tabs.List>
      </Tabs>
      <Grid gap="lg">
        <Grid.Col span={{ base: 12, lg: 7 }}>
          <Samples channel={channel} />
        </Grid.Col>
        <Grid.Col span={{ base: 12, lg: 5 }}>
          <Profile channel={channel} />
        </Grid.Col>
      </Grid>
    </Stack>
  );
}

function Samples({ channel }: { channel: Channel }): ReactElement {
  const client = useQueryClient();
  const key = ["samples", channel];
  const samples = useQuery({ queryKey: key, queryFn: () => get<Sample[]>(`/api/voice/${channel}/samples`) });
  const [text, setText] = useState("");
  const refresh = (): void => void client.invalidateQueries({ queryKey: key });
  const add = useMutation({
    mutationFn: () => call("POST", `/api/voice/${channel}/samples`, { text }),
    onSuccess: () => {
      setText("");
      refresh();
    },
  });
  const remove = useMutation({ mutationFn: (id: number) => call("DELETE", `/api/voice/samples/${id}`), onSuccess: refresh });
  const count = samples.data?.length ?? 0;
  return (
    <Card>
      <Group gap="sm" mb="md">
        <Title order={4}>Samples</Title>
        <Badge variant="light" color={count >= MIN_SAMPLES ? "teal" : "yellow"}>
          {count} of at least {MIN_SAMPLES}
        </Badge>
      </Group>
      <Stack>
        {samples.data?.map((s) => (
          <Card key={s.id} withBorder p="sm" bg="var(--mantine-color-body)">
            <Group justify="space-between" align="flex-start" wrap="nowrap">
              <Text size="sm" style={{ whiteSpace: "pre-wrap" }} lineClamp={8}>
                {s.text}
              </Text>
              <Group gap={4} wrap="nowrap">
                {s.held_out && (
                  <Tooltip label="Kept aside to check the profile, not used to build it">
                    <Badge size="xs" variant="outline" color="gray">
                      held out
                    </Badge>
                  </Tooltip>
                )}
                <Tooltip label="Remove">
                  <ActionIcon variant="subtle" color="red" onClick={() => remove.mutate(s.id)} aria-label="Remove sample">
                    <IconTrash size={16} />
                  </ActionIcon>
                </Tooltip>
              </Group>
            </Group>
          </Card>
        ))}
        <Textarea autosize minRows={4} maxRows={14} value={text} onChange={(e) => setText(e.currentTarget.value)} placeholder="Paste one message you wrote" />
        <Group>
          <Button variant="light" leftSection={<IconPlus size={16} />} disabled={text.trim().length < 20} loading={add.isPending} onClick={() => add.mutate()}>
            Add sample
          </Button>
        </Group>
        <ErrorBox error={add.error ?? remove.error ?? samples.error} />
      </Stack>
    </Card>
  );
}

function Profile({ channel }: { channel: Channel }): ReactElement {
  const client = useQueryClient();
  const key = ["voice", channel];
  const profile = useQuery({ queryKey: key, queryFn: () => get<VoiceProfile>(`/api/voice/${channel}/profile`) });
  const [rules, setRules] = useState("");
  useEffect(() => {
    const stored = profile.data?.profile?.style_prompts;
    setRules(Array.isArray(stored) ? stored.join("\n") : "");
  }, [profile.data]);
  const build = useMutation({
    mutationFn: () =>
      call<VoiceProfile>("POST", `/api/voice/${channel}/profile`, {
        style_prompts: rules.split("\n").filter((line) => line.trim() !== ""),
      }),
    onSuccess: (built) => {
      client.setQueryData(key, built);
      notifications.show({ color: "teal", message: "Voice profile built" });
    },
  });
  return (
    <Card style={{ position: "sticky", top: 72 }}>
      <Group justify="space-between" mb="md">
        <Title order={4}>Profile</Title>
        <Button leftSection={<IconWand size={16} />} loading={build.isPending} onClick={() => build.mutate()}>
          {profile.data?.profile ? "Rebuild" : "Build"}
        </Button>
      </Group>
      <Stack>
        <Textarea
          label="Your style rules"
          description="One per line. These win over anything the profiler noticed."
          autosize
          minRows={3}
          value={rules}
          onChange={(e) => setRules(e.currentTarget.value)}
          placeholder="Never open with 'I hope this finds you well'."
        />
        {profile.data?.rendered ? (
          <Card withBorder p="sm" bg="var(--mantine-color-body)">
            <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
              {profile.data.rendered}
            </Text>
          </Card>
        ) : (
          <Empty icon={<IconWand size={32} />} title="No profile yet">
            Add samples, then build it.
          </Empty>
        )}
        <ErrorBox error={build.error ?? profile.error} />
      </Stack>
    </Card>
  );
}
