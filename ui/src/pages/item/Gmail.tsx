import { Badge, Button, Group, Stack, Text, Tooltip } from "@mantine/core";
import { IconBrandGmail, IconRefresh } from "@tabler/icons-react";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery } from "@tanstack/react-query";
import { type ReactElement, useState } from "react";
import { call, type Draft, get, type MailCheck, type MailStatus, type MailThread } from "../../api";
import { ErrorBox, interactionLabel } from "../../components/common";

export function useGmail(): string | null {
  const status = useQuery({ queryKey: ["mail"], queryFn: () => get<MailStatus>("/api/mail"), staleTime: Infinity });
  return status.data?.address ?? null;
}

export function GmailDraft({ draft, guessed, thread, onSaved }: { draft: Draft; guessed: boolean; thread: MailThread | undefined; onSaved: () => void }): ReactElement | null {
  const gmail = useGmail();
  // A guessed address may bounce, so the server takes it only with an explicit confirm.
  const [confirming, setConfirming] = useState(false);
  const push = useMutation({
    mutationFn: () => call<MailThread>("POST", `/api/drafts/${draft.id}/gmail${guessed ? "?confirm=true" : ""}`),
    onSuccess: (pushed) => {
      setConfirming(false);
      for (const warning of pushed.warnings ?? []) notifications.show({ color: "yellow", message: warning, autoClose: false });
      onSaved();
    },
  });
  if (!gmail) return null;
  if (thread?.sent_at) {
    const outcome = thread.bounced_at ? "bounced" : thread.replied_at ? "replied" : null;
    return (
      <Group gap={4}>
        <Badge variant="light" color="blue" leftSection={<IconBrandGmail size={12} />}>
          Sent {new Date(thread.sent_at).toLocaleDateString()}
        </Badge>
        {thread.edited !== null && (
          <Tooltip label="Share of the draft's words you changed before sending. Small numbers mean the drafts are close to how you write.">
            <Badge variant="light" color={thread.edited > 0.3 ? "orange" : "gray"}>
              {Math.round(thread.edited * 100)}% edited
            </Badge>
          </Tooltip>
        )}
        {outcome && (
          <Badge variant="light" color={outcome === "bounced" ? "red" : "teal"}>
            {interactionLabel(outcome)}
          </Badge>
        )}
      </Group>
    );
  }
  if (thread?.draft_id === draft.id) {
    return (
      <Tooltip label={`Send it from ${gmail}; the lead is marked sent within five minutes.`} multiline maw={300}>
        <Badge variant="light" color="gray" leftSection={<IconBrandGmail size={12} />}>
          v{draft.version} in Gmail Drafts
        </Badge>
      </Tooltip>
    );
  }
  const tip = [
    thread ? "Replaces the letter's earlier Gmail draft; edits you made to it in Gmail are lost." : `Saves it as a draft in ${gmail}; you send it from Gmail.`,
    !draft.subject && "It goes in as a reply in the thread of your last email to them, so send that one first.",
    draft.channel === "cover_letter" && "The CV picked for this job goes in as a PDF attachment.",
    guessed && "The address is a guess, so check it before sending.",
  ]
    .filter(Boolean)
    .join(" ");
  if (confirming) {
    return (
      <Stack gap={4} align="flex-end">
        <Text size="xs" c="orange" ta="right" maw={320}>
          The address is a guess and may bounce, which hurts your mailbox's reputation. At most three guesses a day go to Gmail.
        </Text>
        <Group gap={4}>
          <Button size="xs" variant="subtle" color="gray" onClick={() => setConfirming(false)}>
            Cancel
          </Button>
          <Button size="xs" color="orange" leftSection={<IconBrandGmail size={14} />} loading={push.isPending} onClick={() => push.mutate()}>
            I checked it, put it in
          </Button>
        </Group>
        <ErrorBox error={push.error} />
      </Stack>
    );
  }
  return (
    <Stack gap={4} align="flex-end">
      <Tooltip label={tip} multiline maw={320}>
        <Button size="xs" variant="light" leftSection={<IconBrandGmail size={14} />} loading={push.isPending} onClick={() => (guessed ? setConfirming(true) : push.mutate())}>
          {thread ? `Put v${draft.version} in Gmail instead` : "Put in Gmail drafts"}
        </Button>
      </Tooltip>
      <ErrorBox error={push.error} />
    </Stack>
  );
}

export function CheckGmail({ onSaved }: { onSaved: () => void }): ReactElement | null {
  const gmail = useGmail();
  const check = useMutation({ mutationFn: () => call<MailCheck>("POST", "/api/mail/check"), onSuccess: onSaved });
  if (!gmail) return null;
  const found = check.data;
  return (
    <Group gap="xs" mb="md">
      <Button size="xs" variant="default" leftSection={<IconRefresh size={14} />} loading={check.isPending} onClick={() => check.mutate()}>
        Check Gmail now
      </Button>
      {found && (
        <Text size="xs" c="dimmed">
          {found.logged.length ? `Logged: ${found.logged.length}` : `Nothing new in ${found.checked} watched letter${found.checked === 1 ? "" : "s"}`}
        </Text>
      )}
      <ErrorBox error={check.error} />
    </Group>
  );
}
