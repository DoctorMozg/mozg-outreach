// Types mirror src/mozg_outreach/api/schemas.py; keep them in step when the API changes.

export type Mode = "biz" | "job" | "freelance";
export type RunStatus = "pending" | "running" | "awaiting_gate" | "complete" | "failed" | "cancelled";
export type Channel = "email" | "dm" | "cover_letter" | "proposal";
export type Json = Record<string, unknown>;

export interface Project {
  id: number;
  name: string;
  mode: Mode;
  sender_name: string | null;
  sender_tone: string;
  brief: Json | null;
  created_at: string;
}

export interface FieldDefinition {
  id: string;
  label: string;
  type: "text" | "number" | "boolean" | "url" | "email" | "date" | "list";
  description: string;
  required: boolean;
  example: string | null;
}

export interface FieldSpecVersion {
  version: number;
  spec: { fields: FieldDefinition[] };
  accepted_at: string | null;
  created_at: string;
}

export interface Source {
  id: number;
  project_id: number;
  kind: string;
  config: Json;
  enabled: boolean;
  cursor: string | null;
}

export interface SourceKind {
  kind: string;
  modes: Mode[];
  note: string;
}

export interface Run {
  id: number;
  project_id: number;
  pipeline: string;
  status: RunStatus;
  phase: string | null;
  pending_gate: { kind: string; payload: Json } | null;
  state: Json & { concerns?: string[] };
  cost_usd: number;
  error: string | null;
  started_at: string;
  finished_at: string | null;
}

export interface RunEvent {
  seq: number;
  kind: string;
  payload: Json;
  created_at: string;
}

export interface FieldValue {
  value: unknown;
  source_url: string | null;
  quote: string | null;
  confidence: "verified" | "unverified" | "guessed";
}

export interface ScoreAxis {
  axis: string;
  value: number | null;
  weight: number;
  reason: string;
}

export interface Item {
  id: number;
  project_id: number;
  kind: "company" | "job" | "gig";
  title: string;
  fields: Record<string, FieldValue>;
  score_total: number | null;
  score: { total: number | null; axes: ScoreAxis[]; blockers: string[] } | null;
  selected: boolean;
  /** Posted on, or the day a run first found the lead (YYYY-MM-DD). */
  dated: string;
  updated_at: string;
}

export interface DraftReview {
  address?: string;
  address_confidence?: string;
  hook_used?: string;
  angle_used?: string;
  reader_calibration?: string;
  naturalized?: boolean;
  before_naturalize?: string;
  changes?: string[];
  problems?: string[];
  review_notes?: string[];
  edited_by_user?: boolean;
  based_on_version?: number;
}

export interface Draft {
  id: number;
  item_id: number;
  channel: string;
  recipient: string;
  version: number;
  subject: string | null;
  body: string;
  review: DraftReview;
  created_at: string;
}

export interface Interaction {
  id: number;
  kind: string;
  note: string;
  happened_at: string;
}

export interface LinkedEntity {
  entity_id: number;
  kind: "person" | "company";
  name: string;
  role: string;
}

export interface ItemDetail extends Item {
  raw: Json;
  details: Json;
  drafts: Draft[];
  interactions: Interaction[];
  entities: LinkedEntity[];
}

export interface Sample {
  id: number;
  channel: Channel;
  text: string;
  held_out: boolean;
  created_at: string;
}

export interface VoiceProfile {
  channel: Channel;
  profile: Json | null;
  rendered: string | null;
}

export interface Entity {
  id: number;
  kind: "person" | "company";
  name: string;
  updated_at: string;
}

export interface EntityDetail extends Entity {
  profile: Record<string, FieldValue>;
  identifiers: { kind: string; value: string }[];
  sightings: { source_kind: string; source_url: string; quote: string; project_id: number | null; seen_at: string }[];
  relations: {
    type: string;
    direction: "out" | "in";
    other_id: number;
    other_name: string;
    detail: string | null;
    source_url: string | null;
    quote: string | null;
  }[];
  leads: { item_id: number; project_id: number; title: string; role: string }[];
}

export interface Graph {
  nodes: { id: string; kind: "person" | "company" | "lead"; label: string }[];
  edges: { source: string; target: string; label: string }[];
}

export interface MergeSuggestion {
  id: number;
  reason: string;
  status: string;
  a: Entity;
  b: Entity;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

function detailText(detail: unknown): string {
  if (typeof detail === "string") return detail;
  // FastAPI request validation returns a list of {loc, msg}.
  if (Array.isArray(detail)) {
    return detail
      .map((d: { loc?: unknown[]; msg?: string }) => `${(d.loc ?? []).join(".")}: ${d.msg ?? ""}`)
      .join("; ");
  }
  return JSON.stringify(detail);
}

export async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const payload: { detail?: unknown } = await response.json().catch(() => ({}));
    throw new ApiError(response.status, detailText(payload.detail ?? response.statusText));
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

export const get = <T,>(path: string) => call<T>("GET", path);
