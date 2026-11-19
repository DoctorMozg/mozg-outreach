import { Button, type ButtonProps, Group, Popover, Stack, Text, Textarea } from "@mantine/core";
import { type ReactElement, type ReactNode, useState } from "react";

export interface NoteText {
  prompt: string;
  description: string;
  footer: string;
  /** The button inside the box that starts the run. */
  submit: string;
}

export const REPLAN_NOTE: NoteText = {
  prompt: "What should change?",
  description: "Optional. The planner follows it within its limits, e.g. lead with the COO, skip LinkedIn, drop the cost angle.",
  footer: "Letters for steps not done yet are rewritten.",
  submit: "Start planning",
};

interface NoteButtonProps {
  label: string;
  text: NoteText;
  icon: ReactNode;
  loading: boolean;
  /** Settles once the run has started; a rejection keeps the note so the user can retry. */
  onStart: (note: string | null) => Promise<unknown>;
  disabled?: boolean;
  variant?: ButtonProps["variant"];
}

/** Opens an optional note box first, so the user can steer the agent before it runs. */
export function NoteButton({ label, text, icon, loading, onStart, disabled, variant }: NoteButtonProps): ReactElement {
  const [opened, setOpened] = useState(false);
  const [note, setNote] = useState("");
  const cancel = (): void => {
    setOpened(false);
    setNote("");
  };
  const start = async (): Promise<void> => {
    if (loading) return;
    try {
      await onStart(note.trim() || null);
      cancel();
    } catch {
      // The caller shows the error; the box stays open with the note for another try.
    }
  };
  return (
    <Popover opened={opened} onChange={(open) => (open ? setOpened(true) : cancel())} closeOnClickOutside={false} width={360} position="bottom-end" shadow="md" trapFocus>
      <Popover.Target>
        <Button
          size="xs"
          variant={variant}
          leftSection={icon}
          loading={loading}
          disabled={disabled}
          aria-haspopup="dialog"
          aria-expanded={opened}
          onClick={() => (opened ? cancel() : setOpened(true))}
        >
          {label}
        </Button>
      </Popover.Target>
      <Popover.Dropdown role="dialog" aria-label={label}>
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
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                e.preventDefault();
                void start();
              }
            }}
          />
          <Text size="xs" c="dimmed">
            {text.footer}
          </Text>
          <Group justify="flex-end" gap="xs">
            <Button size="xs" variant="subtle" color="gray" onClick={cancel}>
              Cancel
            </Button>
            <Button size="xs" leftSection={icon} loading={loading} onClick={() => void start()}>
              {text.submit}
            </Button>
          </Group>
        </Stack>
      </Popover.Dropdown>
    </Popover>
  );
}
