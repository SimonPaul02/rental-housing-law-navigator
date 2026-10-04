/** Mirrors the FastAPI response models. */

import type { Role } from "./roles";

export type RuleStatus = "in_force" | "not_yet_effective" | "pending" | "failed";
export type LookupResult = "applies" | "does_not_apply" | "unknown";

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
}

export interface AddressStats {
  total: number;
  by_state: Record<string, number>;
  by_postal_city: Record<string, number>;
  resolved: number;
  unresolved: number;
  by_method: Record<string, number>;
  city_corrections: number;
  missing_year_built: number;
  missing_units: number;
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
}

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
  postal_city_differs: boolean;
  created_at: string;
}
