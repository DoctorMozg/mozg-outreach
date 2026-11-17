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
  enriched: boolean;
  /** Null for a lead never enriched, or enriched before timestamps were kept. */
  enriched_at: string | null;
  /** Archived leads leave the report and later searches skip them. */
  archived_at: string | null;
  /** Posted on, or the day a run first found the lead (YYYY-MM-DD). */
  dated: string;
  updated_at: string;
  activity: Activity | null;
  /** Where a company's approach sequence stands; null without a plan. */
  approach_step: ApproachStep | null;
}

/** The first approach touch not done yet, or why the sequence stopped (approach_models.py ApproachStep). */
export interface ApproachStep {
  /** Null when every touch is done or the sequence stopped. */
  step: number | null;
  total: number;
  done: number;
  channel: TouchChannel | null;
  reader: string | null;
  /** YYYY-MM-DD: the first touch's date plus this touch's day offset; null before the first touch. */
  due: string | null;
  state: "active" | "paused" | "stopped" | "done";
  /** Why it stopped or paused, with the date. */
  reason: string | null;
  /** YYYY-MM-DD: when a paused sequence goes on. */
  resume_on: string | null;
  /** YYYY-MM-DD: when a stopped-for-now or finished sequence may be re-planned. */
  recycle_on: string | null;
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
  // A company's enrichment also plans the approach and drafts its letters; a job's drafts its cover letter.
  const extra = item.kind === "company" ? ", replans the approach and drafts new letter versions" : item.kind === "job" ? " and drafts a new cover letter version" : "";
  const first = { company: "Enrichment researches the company, plans the approach and drafts the letters.", job: "Enrichment finds who to send it to and drafts the cover letter.", gig: null }[item.kind];
  if (!item.enriched) return { label: "Enrich", note: first };
  const when = item.enriched_at ? ` on ${new Date(item.enriched_at).toLocaleDateString()}` : "";
  return { label: "Enrich again", note: `Already enriched${when}; running it again repeats the paid searches${extra}.` };
}

export interface DuplicateLead {
  id: number;
  title: string;
}

/** One company saved as several leads; a merge keeps `keep`. */
export interface DuplicateGroup {
  keep: DuplicateLead;
  drop: DuplicateLead[];
}

/** A Beta(1, 1) posterior on a reply rate. */
export interface Rate {
  sent: number;
  replied: number;
  mean: number;
  low: number;
  high: number;
}

export interface OutcomeRow {
  group: "angle" | "purpose";
  name: string;
  rate: Rate;
}

export interface Outcomes {
  rows: OutcomeRow[];
  level: number;
  sent: number;
}

export interface CandidateReview {
  variant: string;
  subject: string | null;
  text: string;
  problems: string[];
  /** Judge points over both orders; null when it wasn't judged. */
  score: number | null;
  chosen: boolean;
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
  /** Every draft the judge compared, the chosen one included; empty for a single draft. */
  candidates?: CandidateReview[];
  edited_by_user?: boolean;
  based_on_version?: number;
}

export interface Draft {
  id: number;
  item_id: number;
  channel: string;
  recipient: string;
  /** The approach step this letter carries; null outside a plan. */
  step: number | null;
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
  /** YYYY-MM-DD: an out-of-office's return, or when a "not now" said to come back. */
  until: string | null;
}

/** An email letter put into the user's Gmail Drafts, and what the mailbox shows became of it. */
export interface MailThread {
  id: number;
  item_id: number;
  recipient: string;
  step: number | null;
  draft_id: number;
  address: string;
  pushed_at: string;
  sent_at: string | null;
  replied_at: string | null;
  bounced_at: string | null;
  auto_replied_at: string | null;
  /** What the user sent, as Gmail has it. */
  sent_body: string | null;
  /** Share of the draft's words changed before sending, 0 to 1. */
  edited: number | null;
  /** Only on a push answer: e.g. a jump in the day's volume. */
  warnings?: string[];
}

/** The Gmail account drafts go to; null when Gmail isn't set up. */
export interface MailStatus {
  address: string | null;
}

export interface MailCheck {
  checked: number;
  logged: string[];
}

export interface LinkedEntity {
  entity_id: number;
  kind: "person" | "company";
  name: string;
  role: string;
}

/** One person from the enrichment dossier's contacts. */
export interface KeyPerson {
  name: string;
  title: string;
  linkedin: string | null;
  relevance: string;
  source_url: string;
  confidence: "high" | "medium" | "low";
}

export interface ItemDetail extends Item {
  raw: Json;
  details: Json;
  drafts: Draft[];
  mail: MailThread[];
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

/** Settings shared by every project; a null minimum turns auto-enrichment off. */
export interface UserSettings {
  auto_enrich_min_score: number | null;
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

export type TouchChannel = "email" | "linkedin" | "phone" | "form";

export interface FilledField {
  label: string;
  source: "profile" | "cv" | "letter" | "answerer" | "page";
  value: string | null;
  /** Set when the AI chose the answer without a stated fact behind it. */
  reasoning: string | null;
}

/** What `seeker.apply` filled on a job's form (apply/models.py FillReport). */
export interface FillReport {
  ats: string;
  url: string;
  filled: FilledField[];
  for_user: { label: string; required: boolean; reason: string }[];
  concerns: string[];
}

export interface PlanRoute {
  ref: string;
  channel: TouchChannel;
  address: string;
  confidence: "published" | "guessed" | "linkedin" | "shared_inbox";
  source_url: string | null;
}

export interface PlanContact {
  ref: string;
  name: string | null;
  title: string;
  relevance: string;
  routes: PlanRoute[];
}

export interface PlannedTouch {
  step: number;
  day: number;
  contact_ref: string;
  route_ref: string;
  addressed_to: string | null;
  purpose: "open" | "follow_up" | "breakup";
  angle: string;
  hook_ref: string | null;
  ask: string;
  talking_points: string;
  /** A LinkedIn connection request; a note is at most 200 characters, "no_note" has no draft. */
  connection?: "note" | "no_note" | null;
}

/** Who to contact, how and when, stored on an enriched company as `details.approach`. */
export interface StoredApproach {
  plan: {
    summary: string;
    first_contact_ref: string;
    rationale: string;
    touches: PlannedTouch[];
    avoid: string[];
    concerns: string[];
  };
  contacts: PlanContact[];
  hooks: { ref: string; hook: { signal: string; source: string; source_url: string | null } }[];
  planned_on: string;
  /** Touches the user marked done, with the day. */
  done?: { step: number; on: string; interaction_id: number | null }[];
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
