import { Button, type ButtonProps, Group, Popover, Stack, Text, Textarea } from "@mantine/core";
import { IconRoute } from "@tabler/icons-react";
import { type ReactElement, type ReactNode, useState } from "react";

export interface NoteText {
  prompt: string;
  description: string;
  footer: string;
}

export const REPLAN_NOTE: NoteText = {
  prompt: "What should change?",
  description: "Optional. The planner follows it within its limits, e.g. lead with the COO, skip LinkedIn, drop the cost angle.",
  footer: "Letters for steps not done yet are rewritten.",
};

interface NoteButtonProps {
  label: string;
  text: NoteText;
  loading: boolean;
  onStart: (note: string | null) => void;
  icon?: ReactNode;
  variant?: ButtonProps["variant"];
}

/** Opens an optional note box first, so the user can steer the agent before it runs. */
export function NoteButton({ label, text, loading, onStart, icon = <IconRoute size={14} />, variant }: NoteButtonProps): ReactElement {
  const [opened, setOpened] = useState(false);
  const [note, setNote] = useState("");
  const start = (): void => {
    setOpened(false);
    onStart(note.trim() || null);
    setNote("");
  };
  return (
    <Popover opened={opened} onChange={setOpened} width={360} position="bottom-end" shadow="md" trapFocus>
      <Popover.Target>
        <Button size="xs" variant={variant} leftSection={icon} loading={loading} onClick={() => setOpened((o) => !o)}>
          {label}
        </Button>
      </Popover.Target>
      <Popover.Dropdown>
        <Stack gap="xs">
          <Textarea
            label={text.prompt}
            description={text.description}
            autosize
            minRows={3}
            maxRows={8}
            maxLength={2000}
            value={note}
            onChange={(e) => setNote(e.currentTarget.value)}
          />
          <Group justify="space-between" wrap="nowrap">
            <Text size="xs" c="dimmed">
              {text.footer}
            </Text>
            <Button size="xs" leftSection={icon} onClick={start}>
              {label}
            </Button>
          </Group>
        </Stack>
      </Popover.Dropdown>
    </Popover>
  );
}
