export interface Role {
  id: number
  name: string
  description: string
  permissions: string[]
  is_system: boolean
}
export interface User {
  id: number
  username: string
  full_name: string
  department: string
  is_active: boolean
  must_change_password: boolean
  role: Role
  created_at: string
}
export interface Material {
  id: number
  code: string
  name: string
  category_id: number | null
  location_id: number | null
  supplier_id: number | null
  mpn: string
  specification: string
  package: string
  footprint: string
  manufacturer: string
  unit: string
  unit_price: string
  safety_stock: string
  target_stock: string
  quantity: string
  reserved_quantity: string
  available_quantity: string
  barcode: string
  lifecycle_status: string
  rohs_status: string
  datasheet_url: string
  tags: string[]
  attributes: Record<string, unknown>
  notes: string
  is_active: boolean
  created_at: string
  updated_at: string
}
export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}
export type CableKind = 'terminal' | 'flat_flex' | 'micro_coax' | 'rf_coax'
export type CableEndStyle =
  'double' | 'single' | 'single_tinned' | 'male_female_pair' | 'unspecified'
export type CableDirection = 'same' | 'reverse' | 'unspecified'
export interface CableActualLocation {
  location_id: number
  code: string
  name: string
  full_path: string
  warehouse: string
  quantity: number | string
}
export interface Cable {
  id: number
  code: string
  name: string
  custom_name: string
  model: string
  cable_kind: CableKind
  end_style: CableEndStyle
  connector_a: string
  connector_b: string
  connector_pitch_mm: string
  direction: CableDirection
  length_cm: string
  pin_count: number
  pin_count_b: number
  pin_layout: string
  quantity: number | string
  reserved_quantity: number | string
  available_quantity: number | string
  unit_price: string
  storage_location: string
  actual_locations: CableActualLocation[]
  actual_location_status: 'complete' | 'partial' | 'unallocated' | 'inconsistent'
  actual_location_quantity: number | string
  notes: string
  updated_at: string
}
export interface CablePage {
  items: Cable[]
  total: number
  page: number
  page_size: number
  summary: {
    quantity: number | string
    available_quantity: number | string
    in_stock_types: number
    pitch_count: number
  }
  facets: {
    connector_pitches: string[]
    lengths: string[]
    pin_counts: number[]
    cable_kinds: CableKind[]
    end_styles: CableEndStyle[]
  }
}
export interface CableImportSourceItem {
  key: string
  identity: string
  item_id: string
  variant: string
  quantity: number
  order_no: string
  source_row: number
  unit_price: string
  product_url: string
}
export interface CableImportRow {
  selected: boolean
  valid: boolean
  confidence: 'high' | 'medium' | 'low'
  source_rows: number[]
  source_line_count: number
  source_items: CableImportSourceItem[]
  name: string
  model: string
  cable_kind: CableKind
  end_style: CableEndStyle
  connector_a: string
  connector_b: string
  connector_pitch_mm: string | null
  direction: CableDirection
  length_cm: string
  pin_count: number
  pin_count_b: number
  pin_layout: string
  quantity: number
  unit_price: string
  storage_location: string
  notes: string
  shop: string
  status: string
  raw_product_name: string
  raw_variant: string
  warnings: string[]
  issues: string[]
  existing_cable_id: number | null
  existing_cable_code: string
  import_action: 'create' | 'increase' | 'skip'
}
export interface CableImportPreview {
  filename: string
  detected_format: string
  rows: CableImportRow[]
  summary: {
    source_rows: number
    recognized_rows: number
    spec_count: number
    merged_rows: number
    quantity: number
    valid_quantity: number
    shops: string[]
    existing_specs: number
    already_imported_specs: number
  }
}
export interface CableImportResult {
  created: number
  increased: number
  skipped: number
  imported_specs: number
  quantity_added: number
  cable_ids: number[]
  idempotent_replay?: boolean
}
export interface ApiError {
  code: string
  message: string
  details: Record<string, unknown>
  request_id: string
}

export interface AgentToolEvent {
  tool: string
  status: 'running' | 'success' | 'error'
  summary: string
  duration_ms: number
  error_code?: string | null
}

export interface AgentUIAction {
  type:
    | 'open_material'
    | 'open_cable'
    | 'focus_location'
    | 'open_project'
    | 'open_product'
    | 'request_approval'
  target_id: number | null
  payload: Record<string, unknown>
}

export interface AgentLocationEntity {
  location_id: number
  code: string
  name: string
  full_path: string
  organizer_id: number | null
  organizer_style: string | null
  parent_id: number | null
  quantity_at_location: string | null
  quantity_is_exact: boolean
}

export interface AgentLocationResultEntity {
  material_id: number
  code: string
  name: string
  mpn: string
  attributes?: Record<string, unknown> | null
  locations: AgentLocationEntity[]
  count: number
  material_quantity: string
  reserved_quantity?: string
  available_quantity?: string
  unit?: string
  lot_quantity_total: string
  unallocated_quantity: string
  distribution_status: 'partial' | 'complete' | 'inconsistent'
}

export interface AgentMaterialEntity {
  id?: number
  material_id?: number
  code: string
  name: string
  mpn: string
  specification?: string
  package?: string
  manufacturer?: string
  unit?: string
  attributes?: Record<string, unknown> | null
  quantity?: string
  reserved_quantity?: string
  available_quantity?: string
  safety_stock?: string
  target_stock?: string
  low_stock?: boolean
  location?: AgentLocationEntity | null
  distribution_status?: 'partial' | 'complete' | 'inconsistent'
  unallocated_quantity?: string
  locations?: AgentLocationEntity[]
}

export interface AgentBomEntity {
  project: { id: number; code: string; name: string; status: string }
  version?: string | null
  items: Array<
    AgentMaterialEntity & {
      required_quantity: string
      reserved_for_project: string
      shortage?: string
      sufficient?: boolean
    }
  >
  quantity_semantics: string
  shortage_count?: number
  sufficient?: boolean
}

export interface ProductRevisionSummary {
  id: number
  product_id?: number
  revision: string
  status: 'draft' | 'released' | 'obsolete'
  is_default: boolean
  notes?: string
  released_at?: string | null
  bom_hash?: string | null
  released_by_id?: number | null
}

export interface ProductSummary {
  id: number
  code: string
  name: string
  description?: string
  lifecycle_status?: 'active' | 'archived'
  revision_count?: number
  default_revision?: ProductRevisionSummary | null
  revisions?: ProductRevisionSummary[]
  released_revisions?: ProductRevisionSummary[]
}

export interface ProductBomItemView {
  id?: number
  material_id: number
  code: string
  name: string
  mpn: string
  unit: string
  quantity_per_unit: string
  available_quantity: string
  safety_stock: string
  notes?: string
}

export type ComponentRelationType =
  'similar_to' | 'electrical_compatible' | 'pin_compatible' | 'same_footprint'
export type EngineeringReviewStatus =
  'candidate' | 'validated' | 'approved' | 'rejected' | 'revoked'

export interface EngineeringEvidenceRef {
  type?: string
  label?: string
  url?: string
  reference?: string
  [key: string]: unknown
}

export interface EvidenceCitation {
  document_id: number
  document_key: string
  document_title: string
  document_revision: string
  document_status: 'current' | 'superseded' | 'withdrawn'
  page: number
  section: string
  anchor_id: number
  excerpt: string
  file_sha256: string
  page_text_sha256: string
  synthetic_fixture: boolean
  layout_blocks?: Array<{
    id: number
    block_type:
      'heading' | 'paragraph' | 'table' | 'pin_description' | 'application_circuit' | 'unparsed'
    reading_order: number
    text: string
    text_sha256: string
    bbox: number[] | null
    location_status: 'available' | 'location_unavailable'
    provenance?: Record<string, unknown> | null
    extractor_version: string
    source_sha256: string
  }>
  ranking?: { score: number; signals: string[]; strategy: string }
}

export interface EngineeringDocumentSummary {
  id: number
  document_key: string
  scope_type: 'material' | 'product_revision'
  material_id: number | null
  product_revision_id: number | null
  title: string
  manufacturer: string
  document_revision: string
  document_date: string
  original_filename: string
  file_sha256: string
  page_count: number
  status: 'current' | 'superseded' | 'withdrawn'
  supersedes_document_id: number | null
  ingest_status: string
  extraction_version?: string
  synthetic_fixture: boolean
}

export interface EngineeringEvidenceEntity {
  material_scope: RelationMaterialSummary[]
  query: string
  include_superseded: boolean
  facts: Array<Record<string, unknown> & { field: string; anchor_id: number }>
  citations: EvidenceCitation[]
  allowed_anchor_ids: number[]
  evidence_coverage: 'supported' | 'insufficient'
  conclusion: string
  automatic_decision: false
  read_only: true
  retrieval?: {
    strategy: string
    vector_status: string
    reranker: string
    layout_extraction_versions: string[]
  }
}

export interface ComponentEvidenceComparisonEntity {
  materials: RelationMaterialSummary[]
  comparisons: Array<{
    field: string
    first: unknown
    second: unknown
    result: 'same' | 'different' | 'unknown'
    first_anchor_ids: number[]
    second_anchor_ids: number[]
  }>
  differences: Array<Record<string, unknown>>
  unknowns: string[]
  citations: EvidenceCitation[]
  conclusion: string
  pin_compatible_supported: false | null
  automatic_decision: false
  read_only: true
}

export interface RelationMaterialSummary {
  id: number
  code: string
  name: string
  mpn: string
  package: string
  manufacturer: string
  is_active?: boolean
  is_deleted?: boolean
}

export interface ComponentRelation {
  id: number
  source_material: RelationMaterialSummary
  target_material: RelationMaterialSummary
  relation_type: ComponentRelationType
  status: 'candidate' | 'validated' | 'rejected' | 'revoked'
  confidence_note: string
  evidence_summary: string
  evidence_refs: EngineeringEvidenceRef[]
  evidence_citations?: EvidenceCitation[]
  rejected_reason: string
  validated_at: string | null
  reviewed_at?: string | null
  revoked_at?: string | null
  revoked_reason?: string
  historically_validated?: boolean
  current_evidence_complete?: boolean
  review_required?: boolean
  currently_usable?: boolean
  unavailable_reasons?: string[]
  global_replacement_approved: false
  pin_compatible_validated: boolean
}

export interface ProductBomAlternate {
  id: number
  product: { id: number; code: string; name: string }
  revision: { id: number; revision: string; status: string }
  product_bom_item_id: number
  primary_material: RelationMaterialSummary
  alternate_material: RelationMaterialSummary
  status: 'candidate' | 'approved' | 'rejected' | 'revoked'
  priority: number
  usage_condition: string
  engineering_note: string
  evidence_refs: EngineeringEvidenceRef[]
  evidence_citations?: EvidenceCitation[]
  source_component_relation_id: number | null
  rejected_reason: string
  approved_at: string | null
  reviewed_at?: string | null
  revoked_at?: string | null
  revoked_reason?: string
  historically_approved?: boolean
  current_evidence_complete?: boolean
  review_required?: boolean
  currently_usable?: boolean
  unavailable_reasons?: string[]
  scope: 'product_revision_bom_position'
  automatic_substitution: false
  approval_statement: string
}

export interface ProductBomEntity {
  product: ProductSummary
  revision: ProductRevisionSummary
  items: ProductBomItemView[]
  count: number
  quantity_semantics: string
}

export interface ProductBomPreviewLine {
  action: 'add' | 'update_quantity' | 'no_change' | 'unresolved'
  role?: string | null
  rail_id?: string | null
  stage_id?: string | null
  requirement_id?: string | null
  selected_material_id?: number | null
  material_code?: string | null
  mpn?: string | null
  draft_quantity?: string | null
  existing_bom_item_id?: number | null
  existing_quantity_per_unit?: string | null
  proposed_quantity_per_unit?: string | null
  reason: string
  reason_code?: string
  selected_candidate_match_status?: 'exact' | 'compatible' | 'partial' | 'mismatch' | null
  selected_candidate_still_valid?: boolean | null
  component_class?: string
  class_source?: string
  class_match?: 'compatible' | 'unknown' | 'mismatch' | null
  rejection_reason?: string | null
  provenance?: Record<string, Record<string, string>>
  material_active?: boolean | null
  requirement_satisfied?: boolean
  source_requirement_id?: string | null
  source_requirement_fingerprint?: string | null
  warnings?: string[]
  evidence_refs: Array<Record<string, unknown>>
}

export interface ProductBomApplyReadinessDryRun {
  dry_run: true
  apply_allowed: false
  requires_explicit_user_confirmation: true
  preconditions: string[]
  planned_mutations: Array<{
    action: string
    material_id?: number | null
    before?: string | null
    after?: string | null
  }>
  blocked_items: Array<Record<string, unknown>>
  stale_preview: boolean
  idempotency_key: string
  preview_fingerprint: string
  target_revision_fingerprint: string
  current_bom_fingerprint: string
  read_only: true
  automatic_write: false
  formal_product_bom_modified: false
}

export interface ProductBomPreviewEntity {
  workflow: 'product_bom_preview'
  target_product: { id: number; code: string; name: string }
  target_revision: { id: number; revision: string; status: string; is_default: boolean }
  source_engineering_draft: {
    status: string
    focus_scope?: string | null
    row_count: number
    completeness: Record<string, unknown>
    incomplete?: boolean
  }
  lines: ProductBomPreviewLine[]
  summary: {
    add_count: number
    update_quantity_count: number
    no_change_count: number
    unresolved_count: number
    complete_for_apply_preview: boolean
    blocking_reasons: string[]
    readiness?: 'ready' | 'blocked'
    readiness_for_apply?: boolean
    ready_for_confirmation_preview?: boolean
    blocking_reason_codes?: string[]
    warnings?: string[]
    selected_count?: number
    valid_selected_count?: number
    selected_line_count?: number
    valid_selected_line_count?: number
    target_revision_status?: string
  }
  unresolved: Array<{
    requirement_id?: string | null
    role?: string | null
    selected_material_id?: number | null
    reason: string
  }>
  preview_fingerprint?: string
  target_revision_fingerprint?: string
  current_bom_fingerprint?: string
  apply_readiness_dry_run?: ProductBomApplyReadinessDryRun
  read_only: true
  automatic_write: false
  formal_product_bom_modified: false
  quantity_semantics: string
}

export interface BuildReadinessItem extends ProductBomItemView {
  required_total: string
  reserved_for_project: string
  additional_reservation_required: string
  projected_free_available_after_build: string
  coverage: string
  shortage: string
  remaining_after_build: string
  below_safety_after_build: boolean
  material_available_for_build: boolean
  material_blocker: 'inactive' | 'deleted' | null
  approved_alternates?: Array<{
    alternate_id: number
    material_id: number
    code: string
    name: string
    mpn: string
    available_quantity: string
    unit: string
    usage_condition: string
    scope: 'product_revision_bom_position'
    informational_only: true
  }>
}

export interface BuildReadinessEntity {
  product: ProductSummary
  revision: ProductRevisionSummary
  project: { id: number; code: string; name: string } | null
  build_quantity: number
  sufficient: boolean
  shortage_count: number
  max_buildable_units: number
  safety_risk_count: number
  material_blocker_count: number
  items: BuildReadinessItem[]
  quantity_semantics: string
  read_only: true
}

export interface BuildPlanItem {
  material_id: number
  code: string
  name: string
  unit: string
  quantity_per_unit: string
  required_total: string
  available_quantity_at_plan: string
  reserved_for_project_at_plan: string
  additional_reservation_required: string
  projected_free_available_after_build: string
  safety_stock_at_plan: string
  below_safety_after_build: boolean
  material_status_at_plan: string
}

export interface BuildPlan {
  id: number
  plan_no: string
  status: 'ready' | 'reservation_pending' | 'reserved' | 'stale' | 'cancelled'
  product_revision_id: number
  project_id: number
  build_quantity: number
  product_bom_hash: string
  snapshot_hash: string
  reservation_proposal_id: number | null
  stale_reason: string
  product: { id: number; code: string; name: string }
  revision: { id: number; revision: string; status: string }
  project: { id: number; code: string; name: string }
  items: BuildPlanItem[]
}

export interface AgentComponentCandidate {
  material_id: number
  code: string
  name: string
  mpn: string
  specification: string
  package: string
  manufacturer: string
  match_reasons: string[]
  hard_constraint_matches: string[]
  soft_preference_matches: string[]
  metadata_confidence: string
  technical_claims_allowed: boolean
  engineering_verification_required: boolean
  validated_relations?: Array<{
    relation_id: number
    relation_type: ComponentRelationType
    status: 'validated'
    language: string
    related_material: RelationMaterialSummary
    evidence_summary: string
    global_replacement_approved: false
  }>
  inventory: AgentMaterialEntity
  locations: AgentLocationResultEntity
}

export interface AgentComponentSearchEntity {
  query: { raw_text: string; replacement_intent: boolean }
  candidates: AgentComponentCandidate[]
  count: number
  candidate_only: boolean
  engineering_caveat: string
  read_only: boolean
}

export interface AgentPowerPeripheralRole {
  role: string
  exact_value: string | null
  value_status: 'datasheet_grounded' | 'select_device_then_verify'
  evidence: string
}

export interface AgentPowerCandidate {
  material_id: number
  code: string
  name: string
  mpn: string
  manufacturer: string
  package: string
  topology: 'buck' | 'ldo'
  compatible: boolean
  provisional: boolean
  incompatibilities: string[]
  inventory: AgentMaterialEntity
  locations: AgentLocationResultEntity
  evidence: {
    material_code: string
    mpn: string
    source_type: string
    source_title: string
    source_url: string
    datasheet_url: string
    verified_facts: Record<string, string>
    provenance_status: string
  }
  peripheral_roles: AgentPowerPeripheralRole[]
  calculations?: Array<{
    calculation_type: string
    formula: string
    source: string
    vin_v: string | null
    vout_v: string | null
    load_current_a: string | null
    status: 'calculated' | 'requires_load_current'
    loss_w: string | null
    ideal_efficiency: string | null
    thermal_note?: string
  }>
  evidence_reconciliation?: Array<Record<string, string>>
}

export interface AgentPowerBranch {
  topology: 'buck' | 'ldo'
  label: string
  supported: boolean
  compatible_candidate_count: number
  candidates: AgentPowerCandidate[]
  tradeoff_summary: string
  peripheral_roles: AgentPowerPeripheralRole[]
  calculations: AgentPowerCandidate['calculations']
  citations: AgentPowerCandidate['evidence'][]
  missing_constraints: string[]
  evidence_reconciliation: Array<Record<string, string>>
}

export interface AgentPowerCandidateReference {
  material_id: number
  code: string
  mpn: string
  package?: string | null
  inventory?: Record<string, unknown>
  locations?: string[]
  location_facts?: Array<Record<string, unknown>>
  selection_status?: AgentPowerBomStatus
  match_status?: 'exact' | 'compatible' | 'partial' | 'mismatch' | null
  match_reasons?: string[]
  match_unknowns?: string[]
  selection_basis?: 'explicit_user' | null
  selection_provenance?: Record<string, unknown>
  inventory_status?: 'in_stock' | 'out_of_stock' | 'unknown' | 'not_checked'
  peripheral_roles?: AgentPowerPeripheralRole[]
  engineering_parameters?: Array<Record<string, unknown>>
  citations?: Array<Record<string, unknown>>
  component_class?: string
  class_source?: string
  class_match?: 'compatible' | 'unknown' | 'mismatch' | null
  rejection_reason?: string | null
  provenance?: Record<string, Record<string, string>>
}

export type AgentPowerBomStatus =
  | 'needs_input'
  | 'needs_selection'
  | 'candidate_found'
  | 'selected'
  | 'no_grounded_candidate'
  | 'not_applicable'
  | 'out_of_stock'
  | 'blocked_by_input'
  | 'complete_draft'

export interface AgentPowerBomConstraint {
  key: string
  operator: 'eq' | 'gte' | 'lte' | 'range' | 'one_of' | 'preferred'
  value: unknown
  unit?: string | null
  hard: boolean
  source_kind: 'datasheet' | 'reference_design' | 'deterministic_calculation' | 'user_input'
  source_anchor?: Record<string, unknown>
  citation?: Record<string, unknown>
}

export interface AgentPowerBomMatchSummary {
  exact_or_compatible_candidates: number
  partial_candidates: number
  mismatch_candidates: number
  selected_candidate_valid?: boolean | null
}

export interface AgentPowerBomCompleteness {
  required_roles: number
  grounded_roles: number
  candidate_covered_roles: number
  selected_roles: number
  unresolved_roles: number
  needs_input_roles: number
  out_of_stock_roles: number
  complete_for_review: boolean
  blocking_reasons: string[]
  requirements_defined?: number
  requirements_grounded?: number
  candidate_covered?: number
  explicitly_selected?: number
  unresolved?: number
  needs_input?: number
  evidence_gap?: number
  complete_for_engineering_review?: boolean
  complete_for_bom_preview_ready_path?: boolean
}

export interface AgentPowerArchitectureStage {
  stage_id: string
  topology: 'buck' | 'ldo' | 'filter'
  input_voltage_v?: string | null
  output_voltage_v?: string | null
  load_current_a?: string | null
  current_basis: 'user_total' | 'user_rail' | 'derived_from_total' | 'not_allocated'
  loss_w?: string | null
  ideal_efficiency?: string | null
  quiescent_current_a?: string | null
  quiescent_input_power_w?: string | null
  thermal_screen?: Record<string, unknown>
  loss_status: 'calculated' | 'unknown_load' | 'requires_efficiency_curve' | 'not_applicable'
  headroom_v?: string | null
  dropout_status: 'not_applicable' | 'verify_at_load' | 'unknown'
  candidate_devices: AgentPowerCandidateReference[]
  selection_status?: AgentPowerBomStatus
  selected_material_id?: number | null
  selection_basis?: 'explicit_user' | null
  engineering_facts?: Array<Record<string, unknown>>
  bom_requirements?: AgentPowerBomRequirement[]
  completeness?: AgentPowerBomCompleteness
  notes: string[]
}

export interface AgentPowerRail {
  rail_id: string
  label: string
  voltage_v?: string | null
  load_current_a?: string | null
  current_basis: 'user_total' | 'user_rail' | 'derived_from_total' | 'not_allocated'
  sensitive_analog: boolean
  stage_ids: string[]
  selection_status?: AgentPowerBomStatus
  completeness?: AgentPowerBomCompleteness
  notes: string[]
}

export interface AgentPowerArchitecture {
  topology: 'direct_buck' | 'buck_ldo' | 'split_rails'
  label: string
  availability: 'candidate_found' | 'conceptual' | 'no_grounded_candidate'
  selected_by_user: boolean
  total_load_current_a?: string | null
  rails: AgentPowerRail[]
  stages: AgentPowerArchitectureStage[]
  summary: string
  constraints: string[]
  read_only: true
}

export interface AgentPowerRailBomStage {
  stage_id: string
  topology: 'buck' | 'ldo' | 'filter'
  input_voltage_v?: string | null
  output_voltage_v?: string | null
  load_current_a?: string | null
  loss_w?: string | null
  loss_status?: string
  headroom_v?: string | null
  dropout_status?: string
  selection_status?: AgentPowerBomStatus
  engineering_facts?: Array<Record<string, unknown>>
  candidate_devices: AgentPowerCandidateReference[]
  bom_requirements?: AgentPowerBomRequirement[]
  selected_material_id?: number | null
  selection_basis?: 'explicit_user' | null
  completeness?: AgentPowerBomCompleteness
}

export interface AgentPowerBomRequirement {
  requirement_id: string
  role: string
  required?: boolean
  required_quantity?: string | null
  exact_value?: string | null
  evidence_status?: 'grounded' | 'not_grounded' | 'unknown'
  status: AgentPowerBomStatus
  selection_status: AgentPowerBomStatus
  inventory_status?: 'in_stock' | 'out_of_stock' | 'unknown' | 'not_checked'
  selected_material_id?: number | null
  selection_basis?: 'explicit_user' | null
  selection_provenance?: Record<string, unknown>
  selection_conflict?: string | null
  constraints?: AgentPowerBomConstraint[]
  match_summary?: AgentPowerBomMatchSummary
  candidates: AgentPowerCandidateReference[]
  peripheral_roles?: Array<Record<string, unknown>>
  citations?: Array<Record<string, unknown>>
  notes?: string[]
  component_class?: string
  expected_component_classes?: string[]
  provenance?: Record<string, Record<string, string>>
  completeness_contribution?: Record<string, unknown>
  evidence_gap?: boolean
}

export interface AgentPowerRailBomRail {
  rail_id: string
  label: string
  voltage_v?: string | null
  load_current_a?: string | null
  current_basis?: string
  sensitive_analog?: boolean
  selection_status?: AgentPowerBomStatus
  stages: AgentPowerRailBomStage[]
  completeness?: AgentPowerBomCompleteness
}

export interface AgentPowerRailBomDraft {
  status: 'needs_selection' | 'needs_confirmation' | 'not_supported' | AgentPowerBomStatus
  selected_topology?: string | null
  rails: AgentPowerRailBomRail[]
  completeness?: AgentPowerBomCompleteness
  manual_review: string[]
  read_only: true
  automatic_write: false
}

export interface AgentPowerLoadCaseCalculation {
  load_current_a: string
  direct_ldo_input_v: string
  output_voltage_v: string
  direct_ldo_loss_w: string
  post_buck_intermediate_voltage_v?: string | null
  post_buck_ldo_loss_w?: string | null
  calculation_type: 'deterministic_server_calculation'
  formula: string
}

export interface AgentPowerDesignEntity {
  workflow: 'power_design'
  topology_first: true
  read_only: true
  request: string
  status: 'supported' | 'needs_constraints' | 'no_grounded_solution'
  requirements: {
    input_voltage_v: string | null
    output_voltage_v: string | null
    load_current_a: string | null
    load_current_min_a: string | null
    load_current_max_a: string | null
    load_current_range_a: [string, string] | null
    load_current_cases_a: string[]
    analog_load_current_a?: string | null
    digital_load_current_a?: string | null
    topology_choice?: string | null
    intermediate_voltage_v?: string | null
    topology_constraints: string[]
    location_requested: boolean
  }
  missing_constraints: string[]
  next_question?: string
  branches: AgentPowerBranch[]
  topologies?: AgentPowerArchitecture[]
  load_case_calculations: AgentPowerLoadCaseCalculation[]
  selected_topology?: string | null
  rail_bom_draft?: AgentPowerRailBomDraft
  completeness?: AgentPowerBomCompleteness
  evidence_reconciliation: Array<Record<string, string>>
}

export interface AgentEngineeringResearchEntity {
  workflow: 'engineering_research'
  read_only: true
  automatic_write: false
  status: 'supported' | 'needs_constraints' | 'no_grounded_solution'
  candidate_status: 'found' | 'not_found' | 'needs_constraints'
  evidence_status: 'sufficient' | 'partial' | 'insufficient' | 'not_checked'
  draft_status: 'reviewable' | 'not_formed'
  focus_scope?: 'primary' | 'peripheral' | 'bom_draft'
  selected_primary_material_id?: number | null
  active_selection_context?: AgentEngineeringSelectionContext | null
  selection_action_result?: AgentEngineeringSelectionActionResult | null
  peripheral_requirements?: AgentEngineeringPeripheralRequirement[]
  engineering_bom_draft?: AgentEngineeringBomDraft
  completeness?: AgentPowerBomCompleteness
  topologies?: AgentPowerArchitecture[]
  rail_bom_draft?: AgentPowerRailBomDraft
  requirements: Record<string, unknown>
  plan: {
    workflow: 'engineering_research'
    task_contract: Record<string, unknown>
    requirements: Record<string, unknown>
    steps: Array<{
      sequence: number
      tool: string
      purpose: string
      status: string
      read_only: true
      write_scope: 'none'
    }>
    round: number
    continuation: boolean
    adaptive?: Record<string, unknown>
    read_only: true
    write_scope: 'none'
  }
  draft: {
    status: 'supported' | 'needs_constraints' | 'no_grounded_solution'
    candidate_status: 'found' | 'not_found' | 'needs_constraints'
    evidence_status: 'sufficient' | 'partial' | 'insufficient' | 'not_checked'
    draft_status: 'reviewable' | 'not_formed'
    conclusion: string
    buck: AgentEngineeringResearchBranch
    ldo: AgentEngineeringResearchBranch
    manual_review: string[]
    unknowns: string[]
    citations: EvidenceCitation[]
    focus_scope?: 'primary' | 'peripheral' | 'bom_draft'
    selected_primary_material_id?: number | null
    peripheral_requirements?: AgentEngineeringPeripheralRequirement[]
    engineering_bom_draft?: AgentEngineeringBomDraft
    completeness?: AgentPowerBomCompleteness
    topologies?: AgentPowerArchitecture[]
    rail_bom_draft?: AgentPowerRailBomDraft
    read_only: true
    automatic_write: false
  }
  citations: EvidenceCitation[]
}

export interface AgentEngineeringPeripheralRequirement {
  requirement_id: string
  role: string
  selected_primary_material_id?: number
  selected_primary_mpn?: string
  value?: string | null
  unit?: string | null
  capacitance_pf?: string | null
  rated_voltage_v?: string | null
  dielectric?: string | null
  tolerance?: string | null
  package?: string | null
  connection?: string | null
  constraint_value?: string | null
  required_quantity?: string | null
  specification_status: string
  evidence_status: string
  evidence_gap?: boolean
  component_class?: string
  expected_component_classes?: string[]
  component_class_gate?: boolean
  provenance?: Record<string, Record<string, string>>
  completeness_contribution?: Record<string, unknown>
  selection_status: string
  material_candidate_ids?: number[]
  matched_material_id?: number | null
  matched_code?: string | null
  matched_mpn?: string | null
  available_quantity?: string | null
  shortage_quantity?: string | null
  location?: Array<{ full_path?: string; code?: string; [key: string]: unknown }>
  location_status?: string | null
  source_anchor?: Partial<EvidenceCitation> & Record<string, unknown>
  source_value?: string | null
  unknowns?: string[]
  required?: boolean
  constraints?: AgentPowerBomConstraint[]
  match_summary?: AgentPowerBomMatchSummary
  candidates?: Array<Record<string, unknown>>
  selected_material_id?: number | null
  selection_basis?: 'explicit_user' | null
  selection_provenance?: Record<string, unknown>
  selection_conflict?: string | null
  selection_resolution?: string | null
}

export interface AgentEngineeringBomDraft {
  status?: string
  focus_scope?: string
  primary?: Record<string, unknown>
  rows?: AgentEngineeringPeripheralRequirement[]
  read_only: true
  automatic_write: false
  formal_product_bom_modified?: false
  approved_substitute?: false
  picking_settled?: false
  active_selection_context?: AgentEngineeringSelectionContext | null
  selection_action_result?: AgentEngineeringSelectionActionResult | null
  completeness?: AgentPowerBomCompleteness
}

export interface AgentEngineeringSelectionContext {
  requirement_id: string
  role?: string | null
  rail_id?: string | null
  stage_id?: string | null
  ordered_candidate_ids: number[]
  valid_candidate_ids: number[]
  selected_material_id?: number | null
  selected_status?: string | null
  selection_basis?: 'explicit_user' | null
  source_request_id?: string | null
  selection_truth_source: 'server'
}

export interface AgentEngineeringSelectionActionResult {
  selection_action: string
  resolution: string
  requirement_id?: string | null
  role?: string | null
  before_material_id?: number | null
  after_material_id?: number | null
  selected_material_code?: string | null
  selected_material_mpn?: string | null
  ordered_candidate_ids: number[]
  valid_candidate_ids: number[]
  selection_truth_source: 'server'
  model_called_for_state: false
  narrative: string
}

export interface AgentEngineeringResearchBranch {
  summary: string
  candidates: AgentEngineeringResearchCandidate[]
}

export interface AgentEngineeringResearchCandidate {
  material_id: number
  code: string
  mpn: string
  package?: string
  inventory: AgentMaterialEntity
  locations: AgentLocationResultEntity
  evidence_coverage: 'supported' | 'insufficient'
  evidence_status: 'sufficient' | 'partial' | 'insufficient' | 'not_checked'
  evidence_gaps: string[]
  evidence_facts?: Array<Record<string, unknown>>
  citations: EvidenceCitation[]
  peripheral_roles: AgentPowerPeripheralRole[]
  component_class?: string
  class_source?: string
  class_match?: 'compatible' | 'unknown' | 'mismatch' | null
  rejection_reason?: string | null
  provenance?: Record<string, Record<string, string>>
  electrical_thermal_judgment?: string
  thermal_analysis?: {
    status: 'not_applicable' | 'unknown' | 'partial' | 'illustrative_first_order'
    loss_w?: string | null
    warning?: string
    points?: Array<{
      package: string
      rtheta_ja_c_per_w: string
      first_order_rise_c: string
      citation_anchor_id?: number
      conditions?: string
    }>
  }
  calculations?: AgentPowerCandidate['calculations']
}

export interface AgentCableLocation {
  location_id: number
  code: string
  name: string
  full_path: string
  quantity: string
}

export interface AgentCableItem {
  material_id: number
  code: string
  name: string
  mpn: string
  manufacturer: string
  specification: string
  unit: string
  quantity: string
  reserved_quantity: string
  available_quantity: string
  cable_kind: CableKind
  end_style: CableEndStyle
  connector_a: string
  connector_b: string
  connector_pitch_mm: string
  pin_count: number
  pin_count_b: number
  pin_layout: string
  direction: CableDirection
  length_cm: string
  locations: AgentCableLocation[]
  location_count: number
  fallback_storage_location: string
  location_truth_source: 'InventoryLot'
  match_reasons: string[]
  technical_claims_allowed: boolean
  match_state?: 'exact_match' | 'near_match'
  differences?: string[]
}

export interface AgentCableSearchEntity {
  query: string
  constraints: Record<string, unknown> & { length_is_soft?: boolean }
  items: AgentCableItem[]
  count: number
  evaluated_count: number
  needs_direction_disambiguation: boolean
  result_state?: 'awaiting_clarification' | 'exact_match' | 'near_match' | 'no_match'
  clarification: string
  automatic_substitution: false
  inventory_source: 'Material + InventoryLot'
}

export interface AgentEntities {
  material_candidates?: {
    items: AgentMaterialEntity[]
    count: number
    exact_match_ids?: number[]
    selected_material_id?: number | null
  }
  project_candidates?: {
    items: Array<{
      id: number
      code: string
      name: string
      status: string
      available_versions?: string[]
    }>
    count: number
    exact_match_ids?: number[]
    selected_project_id?: number | null
    selected_bom_version?: string | null
  }
  product_candidates?: {
    items: ProductSummary[]
    count: number
    exact_match_ids?: number[]
    selected_product_id?: number | null
    selected_product_revision_id?: number | null
  }
  material_detail?: AgentMaterialEntity
  inventory?: AgentMaterialEntity
  locations?: AgentLocationResultEntity
  low_stock?: { items: AgentMaterialEntity[]; count: number }
  project_bom?: AgentBomEntity
  bom_analysis?: AgentBomEntity
  product_bom?: ProductBomEntity
  product_bom_preview?: ProductBomPreviewEntity
  build_readiness?: BuildReadinessEntity
  component_search?: AgentComponentSearchEntity
  power_design?: AgentPowerDesignEntity
  engineering_research?: AgentEngineeringResearchEntity
  cable_search?: AgentCableSearchEntity
  cable_detail?: AgentCableItem
  component_relations?: {
    items: ComponentRelation[]
    count: number
    validated_relation_types: ComponentRelationType[]
    pin_compatible_validated: boolean
    global_replacement_approved: false
    safety_statement: string
    read_only: true
  }
  product_bom_alternates?: {
    items: ProductBomAlternate[]
    count: number
    approved_count: number
    candidate_count: number
    scope_required: boolean
    primary_bom_arithmetic_only: true
    automatic_substitution: false
    safety_statement: string
    read_only: true
  }
  engineering_evidence?: EngineeringEvidenceEntity
  component_evidence_comparison?: ComponentEvidenceComparisonEntity
  relation_policy?: {
    safety_statement: string
    global_replacement_approved: false
    automatic_substitution: false
  }
  proposal?: AgentActionProposal
}

export interface AgentQueryResponse {
  answer: string
  narrative: string
  intent: string | null
  entities: AgentEntities
  grounded_facts: AgentGroundedFact[]
  tool_events: AgentToolEvent[]
  ui_actions: AgentUIAction[]
  proposal_ids: number[]
  telemetry: AgentLLMTelemetry[]
  execution_mode: 'deterministic' | 'llm_assisted'
  model_call_count: number
  request_id: string
  conversation_id: string
}

export interface AgentSuggestionItem {
  type: 'material_location' | 'material_inventory' | 'low_stock' | 'project_bom' | 'material_search'
  text: string
  material_id: number | null
  project_id: number | null
}

export interface AgentSuggestionsResponse {
  items: AgentSuggestionItem[]
  generated_at: string
  source: 'database'
}

export interface AgentGroundedFact {
  kind:
    | 'inventory'
    | 'location'
    | 'bom'
    | 'product_bom'
    | 'build_readiness'
    | 'reconciliation'
    | 'proposal'
    | 'component_relation'
    | 'product_alternate'
    | 'cable'
    | 'engineering_evidence'
    | 'evidence_comparison'
    | 'power_design'
    | 'engineering_research'
  source_tool: string
  entity_id: number | null
  field: string
  value: unknown
  unit: string | null
  label: string
}

export interface AgentLLMTelemetry {
  provider: string
  model: string
  finish_reason: string
  input_tokens: number
  output_tokens: number
  total_tokens: number
  latency_ms: number
  tool_call_count: number
  attempts: number
  retries: number
}

export interface AgentActionProposal {
  id: number
  proposal_no: string
  action_type: string
  status: 'pending' | 'approved' | 'rejected' | 'executed' | 'failed' | 'expired'
  payload: {
    action_type: 'reserve_inventory'
    project_id: number
    items: Array<{ material_id: number; quantity: string }>
    reason: string
    source?: 'manual' | 'build_plan'
    build_plan_id?: number | null
    build_plan_snapshot_hash?: string | null
  }
  display: {
    project_code: string
    project_name: string
    source?: 'manual' | 'build_plan'
    build_plan_id?: number | null
    build_plan_no?: string
    product_code?: string
    product_name?: string
    product_revision?: string
    product_bom_hash?: string
    build_quantity?: number | null
    items: Array<{
      material_id: number
      code: string
      name: string
      mpn: string
      required_total?: string | null
      reserved_for_project_at_plan?: string | null
      additional_reservation_required?: string | null
    }>
  }
  reason: string
  created_by_id: number
  approved_by_id: number | null
  request_id: string
  client_operation_id: string
  payload_hash: string
  execution_result: Record<string, unknown> | null
  error_message: string
  expires_at: string | null
  decided_at: string | null
  executed_at: string | null
  created_at: string
  updated_at: string
}
