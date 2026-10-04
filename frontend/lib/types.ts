/** Mirrors the FastAPI response models. */

import type { Role } from "./roles";

export type RuleStatus = "in_force" | "not_yet_effective" | "pending" | "failed";
/** The challenge's five reportable results, plus the internal `does_not_apply`.
 *
 *  `does_not_apply` never reaches lookups.json - it exists so this view can say
 *  that a rule was considered and definitely does not cover an address, which
 *  the submission format has no word for. */
export type LookupResult =
  | "applies"
  | "unknown"
  | "superseded"
  | "not_yet_effective"
  | "pending"
  | "does_not_apply";
export type JurisdictionStatus = "resolved" | "needs_review" | "not_checked";

export interface RuleRecord {
  team_rule_id: string;
  jurisdiction: string;
  level: "state" | "city";
  category: string;
  status: RuleStatus;
  title: string;
  requirement: string;
  key_value: string | null;
  coverage_conditions: string | Record<string, unknown> | null;
  exemptions: string | null;
  overrides: string[];
  interaction: string | null;
  effective_date: string | null;
  citation: string;
  source_doc_id: string | null;
  source_url: string;
  quoted_span: string;
  confidence: number | null;
  conflict_flag: boolean;
  conflict_note: string | null;
}

export interface RuleStats {
  total: number;
  by_category: Record<string, number>;
  by_status: Record<string, number>;
  by_level: Record<string, number>;
  by_jurisdiction: Record<string, number>;
  documents_total: number;
  documents_with_text: number;
  documents_extracted: number;
  flagged_conflicts: number;
}

export interface DocumentSummary {
  doc_id: string;
  jurisdictions: string;
  url: string;
  source_type: string | null;
  capture: string | null;
  status: string | null;
  has_text: boolean;
  rule_count: number;
  /** What the model said about this document on the last extraction - and for a
   *  document with no rules, why it has none. */
  document_note: string | null;
}

export interface AddressRecord {
  address_id: string;
  street_address: string;
  postal_city: string;
  state: string;
  zip: string | null;
  year_built: number | null;
  units: number | null;
  use_description: string | null;
  legal_city: string | null;
  legal_state: string | null;
  county: string | null;
  jurisdiction_status: JurisdictionStatus;
  zip_discrepancy: boolean;
  postal_city_differs: boolean;
  /** The geocoder's own point, or null. Null for an unresolved address and
   *  also for one a human resolved by override — so a map always places fewer
   *  rows than the table lists, and has to say so. */
  latitude: number | null;
  longitude: number | null;
}

export interface AddressStats {
  total: number;
  by_state: Record<string, number>;
  by_postal_city: Record<string, number>;
  /** Verified legal cities only — what rules actually attach to. */
  by_legal_city: Record<string, number>;
  resolved: number;
  unresolved: number;
  by_method: Record<string, number>;
  city_corrections: number;
  missing_year_built: number;
  missing_units: number;
  with_coordinates: number;
}

/** One check the evaluator ran, as it reported it.
 *
 *  `check` is the dimension - geography, time, coverage, exemption or an
 *  interaction - and `detail` is the sentence the explanation was built from.
 *  Together they are the audit trail: an advocate can see which fact decided
 *  an answer, and a provider can see which exemption beat a rule. */
export interface RuleCheck {
  check: string;
  value: "true" | "false" | "unknown";
  reason: string;
  detail: string;
  condition_id: string | null;
  source_span: string | null;
  field: string | null;
  fact_value: string | number | boolean | null;
  fact_status: string | null;
}

export interface RuleOutcome {
  team_rule_id: string;
  result: LookupResult;
  explanation: string;
  conflict_flag: boolean;
  unresolved_fields: string[];
  in_jurisdiction: boolean;
  category: string | null;
  jurisdiction: string | null;
  level: string | null;
  status: RuleStatus | null;
  title: string | null;
  key_value: string | null;
  citation: string | null;
  source_url: string | null;
  quoted_span: string | null;
  /** Which rule governs this obligation instead, when `result` is superseded. */
  superseded_by: string | null;
  /** The obligation this rule speaks to. Two rules sharing one are rivals. */
  issue_key: string | null;
  checks: RuleCheck[];
}

export interface LookupResponse {
  address_id: string;
  as_of: string;
  legal_city: string | null;
  legal_state: string | null;
  resolution_method: string | null;
  outcomes: RuleOutcome[];
  applies_count: number;
  unknown_count: number;
}

export interface ChangeTest {
  test_id: string;
  title: string;
  type: string;
  rule_ids: string[];
  expected_behavior: string;
}

export interface CanonicalMatch {
  canonical_id: string;
  selector: string;
  matched_rule_ids: string[];
  matched: boolean;
  note: string | null;
  sources: {
    team_rule_id: string;
    source_doc_id: string | null;
    source_url: string;
    citation: string;
    quoted_span: string;
    retrieved_at: string | null;
  }[];
}

/** complete: answered. partial: the affected set stands, but something the case
 *  also asks for is missing. blocked: no answer — its empty sets mean "not
 *  computed", never "nothing moved". */
export type ChangeStatus = "complete" | "partial" | "blocked";

export interface ChangeTestResult {
  test_id: string;
  title: string;
  type: string;
  as_of: string;
  affected_address_ids: string[];
  conflict_flag_address_ids: string[];
  notes: string;
  expected_behavior: string;
  canonical_matches: CanonicalMatch[];
  detail: Record<string, unknown>;
  rules_resolved: boolean;
  // Optional because Vercel and Fly deploy independently; read them through
  // `lib/changes.ts`, which treats an API that predates them as complete.
  status?: ChangeStatus;
  blocked_reason?: string | null;
  warnings?: string[];
}

export interface ChangeStats {
  tests_defined: number;
  tests_run: number;
  tests_answered?: number;
  blocked?: string[];
  partial?: string[];
  total_affected: number;
  total_conflicts: number;
  by_test: Record<
    string,
    { affected: number; conflicts: number; as_of: string; status?: ChangeStatus }
  >;
}

/** What the rule adapter made of Module A's prose, and what still needs a human.
 *  A rule with untranslated text answers `unknown` for every address it could
 *  reach, which is why the queue is ordered by what it is costing. */
export interface CompilationSummary {
  rules: number;
  needs_review: number;
  with_unmapped_text: number;
  relations: number;
  relations_approved: number;
}

/** A queue entry is either a rule needing review or an unapproved relation
 *  between two of them, which is why both halves are optional. */
export interface CompilationQueueItem {
  team_rule_id?: string;
  jurisdiction?: string | null;
  issue_key?: string | null;
  review_state?: string | null;
  unmapped_count?: number;
  unmapped_text?: { text?: string; where?: string }[];
  relation?: {
    left_rule_id: string;
    right_rule_id: string;
    issue_key: string;
    review_state: string;
  };
}

export interface RuleCompilation {
  summary: CompilationSummary;
  queue: CompilationQueueItem[];
}

export interface ZipAssessment {
  status: string;
  input_zip: string;
  matched_zips: string[];
  expected_endpoints: number;
  accepted_endpoints: number;
  reason: string;
}

export interface ZipReviewCase {
  address_id: string;
  street_address: string;
  postal_city: string;
  state: string;
  legal_city: string | null;
  assessment: ZipAssessment;
  review: { decision: string; confirmed_zip: string | null; source_url: string } | null;
}

export interface Health {
  status: string;
  database: boolean;
  database_error: string | null;
  extraction_available: boolean;
  default_as_of: string;
  /** Whether the API requires a WorkOS token. False only outside production
   *  with no client id configured - see backend/app/core/auth.py. */
  auth_required: boolean;
}

/** `/api/meta`: the vocabularies the API is built on, so the frontend renders
 *  filters and scope from the server's own lists rather than a second copy of
 *  them that can drift. Public, like `/health` — it names no row. */
export interface Meta {
  app: string;
  default_as_of: string;
  extraction_model: string;
  categories: string[];
  levels: string[];
  statuses: string[];
  results: string[];
  roles: { role: Role; label: string }[];
  disclaimer: string;
}

/* ----------------------------------------------------------------- accounts */

export interface Account {
  workos_user_id: string;
  role: Role;
  role_label: string;
  email: string;
  name: string | null;
  picture_url: string | null;
  created_at: string;
  last_seen_at: string | null;
}

/** A building somebody has saved for themselves. Private to its owner: there
 *  is no field here that could name another account. */
export interface Place {
  id: number;
  address_id: string;
  label: string | null;
  note: string | null;
  street_address: string;
  postal_city: string;
  state: string;
  zip: string | null;
  year_built: number | null;
  units: number | null;
  legal_city: string | null;
  legal_state: string | null;
  jurisdiction_status: JurisdictionStatus;
  zip_discrepancy: boolean;
  postal_city_differs: boolean;
  latitude: number | null;
  longitude: number | null;
  contract_count: number;
  created_at: string;
}

/** A tenancy agreement somebody uploaded for a building of their own.
 *
 *  The rent and the term are self-reported and nothing verifies them. They are
 *  here so a figure can be read beside the rule that governs it — this app
 *  never computes an entitlement from them. */
export interface Contract {
  id: number;
  place_id: number;
  unit_label: string | null;
  starts_on: string | null;
  ends_on: string | null;
  monthly_rent_cents: number | null;
  note: string | null;
  filename: string;
  content_type: string;
  kind: string;
  byte_size: number;
  sha256: string;
  page_count: number | null;
  created_at: string;
}

export interface ContractTypes {
  max_bytes: number;
  accept: string[];
  names: string[];
}
