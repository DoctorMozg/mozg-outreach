import { Anchor, Button, Card, Group, Stack, Text, TextInput, Title } from "@mantine/core";
import { IconBrandLinkedin } from "@tabler/icons-react";
import { useMutation } from "@tanstack/react-query";
import { type ReactElement, useState } from "react";
import { call, type ItemDetail, type KeyPerson } from "../../api";
import { ErrorBox } from "../../components/common";

/** The dossier's key people, with LinkedIn: open the profile, or search and paste the one you find. */
export function People({ item, onChange }: { item: ItemDetail; onChange: () => void }): ReactElement | null {
  const dossier = (item.details as { dossier?: { contacts?: { key_people: KeyPerson[] } | null } }).dossier;
  const people = dossier?.contacts?.key_people ?? [];
  if (people.length === 0) return null;
  return (
    <Card>
      <Title order={4} mb="sm">
        Key people
      </Title>
      <Stack gap="sm">
        {people.map((p) => (
          <Person key={p.name} item={item} person={p} onChange={onChange} />
        ))}
      </Stack>
    </Card>
  );
}

function Person({ item, person, onChange }: { item: ItemDetail; person: KeyPerson; onChange: () => void }): ReactElement {
  const [editing, setEditing] = useState(false);
  const [url, setUrl] = useState("");
  const save = useMutation({
    mutationFn: () => call("PUT", `/api/items/${item.id}/people/linkedin`, { name: person.name, url }),
    onSuccess: () => {
      setEditing(false);
      setUrl("");
      onChange();
    },
  });
  // LinkedIn's own people search, opened in the user's browser with their own login.
  const search = `https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(`${person.name} ${item.title}`)}`;
  return (
    <Stack gap={4}>
      <Group gap="xs" wrap="wrap">
        <Text size="sm" fw={500}>
          {person.name}
        </Text>
        <Text size="sm" c="dimmed">
          {person.title}
        </Text>
        {person.linkedin && (
          <Anchor href={person.linkedin} target="_blank" rel="noreferrer" size="sm">
            <Group gap={4}>
              <IconBrandLinkedin size={14} />
              profile
            </Group>
          </Anchor>
        )}
        {(!person.linkedin || editing) && (
          <Anchor href={search} target="_blank" rel="noreferrer" size="sm">
            search LinkedIn
          </Anchor>
        )}
        {person.linkedin && !editing && (
          <Button size="compact-xs" variant="subtle" color="gray" onClick={() => setEditing(true)}>
            Change
          </Button>
        )}
      </Group>
      {(!person.linkedin || editing) && (
        <Group gap="xs" wrap="nowrap">
          <TextInput
            size="xs"
            flex={1}
            placeholder="Paste the profile link you found"
            value={url}
            onChange={(e) => setUrl(e.currentTarget.value)}
          />
          <Button size="xs" variant="light" disabled={!url.trim()} loading={save.isPending} onClick={() => save.mutate()}>
            Save
          </Button>
        </Group>
      )}
      <ErrorBox error={save.error} />
    </Stack>
  );
}
