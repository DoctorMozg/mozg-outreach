// Types mirror src/mozg_outreach/api/schemas.py; keep them in step when the API changes.

export type Mode = "biz" | "seeker";
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
  enriched: boolean;
  /** Null for a lead never enriched, or enriched before timestamps were kept. */
  enriched_at: string | null;
  /** Posted on, or the day a run first found the lead (YYYY-MM-DD). */
  dated: string;
  updated_at: string;
  activity: Activity | null;
}

/** What the user last did with a lead, logged here or on the same lead in another project. */
export interface Activity {
  /** The newest logged kind apart from notes; null when only notes were logged. */
  status: string | null;
  status_at: string | null;
  note: string;
  project_id: number;
  item_id: number;
}

/** Label and cost note for a lead's enrich action; running it again repeats paid searches. */
export function enrichHint(item: Item): { label: string; note: string | null } {
  if (!item.enriched) return { label: "Enrich", note: null };
  const when = item.enriched_at ? ` on ${new Date(item.enriched_at).toLocaleDateString()}` : "";
  return { label: "Enrich again", note: `Already enriched${when}; running it again repeats the paid searches.` };
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
  leads: { item_id: number; project_id: number; title: string; role: string; activity: Activity | null }[];
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

/** Sends a file as the raw request body, with its own content type. */
export async function upload<T>(path: string, file: File): Promise<T> {
  const response = await fetch(path, { method: "POST", headers: { "Content-Type": file.type || "application/octet-stream" }, body: file });
  if (!response.ok) {
    const payload: { detail?: unknown } = await response.json().catch(() => ({}));
    throw new ApiError(response.status, detailText(payload.detail ?? response.statusText));
  }
  return (await response.json()) as T;
}

export interface CvSummary {
  language: string;
  headline: string;
  seniority: string;
  titles: string[];
  domains: string[];
  core_skills: string[];
}

export interface Cv {
  id: number;
  name: string;
  filename: string;
  /** Null until the profiler has read it. */
  summary: CvSummary | null;
  created_at: string;
}

export interface CvUpload {
  cv: Cv;
  concerns: string[];
  cost_usd: number;
}

/** The CV to send for one job or gig, stored on the lead as `details.cv_choice`. */
export interface CvChoice {
  cv_id: number;
  picked_by: "ratings" | "only" | "user";
  listing_language: string | null;
  ranked: { cv_id: number; name: string; total: number | null; in_listing_language: boolean; reason: string | null }[];
  concerns: string[];
}

export interface BriefDraft {
  brief: Json;
  concerns: string[];
  field_version: number | null;
  cost_usd: number;
}

export interface SearchReadiness {
  pipeline: string;
  /** Why the search can't start yet; null when it can. */
  blocker: string | null;
}
