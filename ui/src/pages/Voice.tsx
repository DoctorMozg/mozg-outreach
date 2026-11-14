import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactElement, useEffect, useState } from "react";
import { call, get, type Channel, type Sample, type VoiceProfile } from "../api";
import { ErrorBox, Section } from "../components/common";

const CHANNELS: { channel: Channel; label: string }[] = [
  { channel: "email", label: "Emails" },
  { channel: "dm", label: "Direct messages" },
  { channel: "cover_letter", label: "Cover letters" },
  { channel: "proposal", label: "Proposals" },
];

export function Voice(): ReactElement {
  const [channel, setChannel] = useState<Channel>("email");
  return (
    <div className="stack">
      <h2>Your voice</h2>
      <p className="hint">
        Paste messages you wrote yourself. Every generated letter is rewritten toward the profile built from them, and your own rules win
        over everything the profiler noticed.
      </p>
      <nav className="tabs">
        {CHANNELS.map((c) => (
          <button key={c.channel} className={c.channel === channel ? "tab active" : "tab"} onClick={() => setChannel(c.channel)}>
            {c.label}
          </button>
        ))}
      </nav>
      <Samples channel={channel} />
      <Profile channel={channel} />
    </div>
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
  return (
    <Section title={`Samples (${samples.data?.length ?? 0}, at least 3)`}>
      {samples.data?.map((s) => (
        <div key={s.id} className="sample">
          <pre>{s.text}</pre>
          <button className="link" onClick={() => remove.mutate(s.id)}>
            remove
          </button>
        </div>
      ))}
      <textarea rows={6} value={text} onChange={(e) => setText(e.target.value)} placeholder="Paste one message you wrote" />
      <button disabled={text.trim().length < 20 || add.isPending} onClick={() => add.mutate()}>
        Add sample
      </button>
      <ErrorBox error={add.error ?? remove.error ?? samples.error} />
    </Section>
  );
}

function Profile({ channel }: { channel: Channel }): ReactElement {
  const client = useQueryClient();
  const key = ["voice", channel];
  const profile = useQuery({ queryKey: key, queryFn: () => get<VoiceProfile>(`/api/voice/${channel}/profile`) });
  const stored = (profile.data?.profile?.style_prompts as string[] | undefined) ?? [];
  const [rules, setRules] = useState("");
  useEffect(() => setRules(stored.join("\n")), [profile.data]);
  const build = useMutation({
    mutationFn: () =>
      call<VoiceProfile>("POST", `/api/voice/${channel}/profile`, {
        style_prompts: rules.split("\n").filter((line) => line.trim() !== ""),
      }),
    onSuccess: (built) => client.setQueryData(key, built),
  });
  return (
    <Section
      title="Profile"
      actions={
        <button onClick={() => build.mutate()} disabled={build.isPending}>
          {build.isPending ? "Building..." : profile.data?.profile ? "Rebuild" : "Build"}
        </button>
      }
    >
      <label className="field">
        <span>Your style rules, one per line</span>
        <textarea rows={4} value={rules} onChange={(e) => setRules(e.target.value)} placeholder="Never open with 'I hope this finds you well'." />
      </label>
      {profile.data?.rendered ? <pre className="letter">{profile.data.rendered}</pre> : <p className="hint">No profile yet.</p>}
      <ErrorBox error={build.error ?? profile.error} />
    </Section>
  );
}
