import { downloadFile, request, requestFormData } from "./client.ts";
import type { WorkflowStatus } from "../projectContracts.ts";
import type { ReportImportJob } from "./reportImportClient.ts";

export type ReportSectionType = "form" | "blocks" | "generated" | "appendix_a" | "appendix_b";
export type ReportEditPolicy = "editable" | "overrideable" | "readonly";
export type ReportCompletionStatus = "not_started" | "in_progress" | "complete" | "completed";
export type ReportBlockType =
  | "paragraph"
  | "bullet_list"
  | "numbered_list"
  | "key_value_table"
  | "data_table"
  | "figure"
  | "reference"
  | "generated";

export type ReportIssue = {
  relation_id?: string | null;
  entity_path?: string | null;
  entity_type?: string | null;
  entity_uuid?: string | null;
  field?: string | null;
  code: string;
  message: string;
  severity: "error" | "warning" | "info";
  target?: string | null;
  navigation_target?: string | null;
  details?: Record<string, unknown>;
};

export type AppendixBIssue = {
  severity: "error" | "warning";
  code: string;
  message: string;
  category_code?: string | null;
  item_uuid?: string | null;
  field?: string | null;
  details: Record<string, unknown>;
  navigation_target: string;
};

export type AppendixBUsage = {
  usage_uuid: string;
  usage_kind: "member" | "covered_onsite" | "personnel_role" | "exam_proof" | "image_slot";
  related_member_uuid?: string | null;
  related_item_uuid?: string | null;
  slot_key: string;
  sort_order: number;
  member_name?: string | null;
  qualification_passed_at?: string | null;
};

export type AppendixBEvidenceItem = {
  item_uuid: string;
  project_id: number;
  category_code: string;
  parent_item_uuid?: string | null;
  item_kind: "record" | "image";
  subtype: string;
  title: string;
  starts_on?: string | null;
  ends_on?: string | null;
  organization_uuid?: string | null;
  location: string;
  sort_order: number;
  metadata: Record<string, unknown>;
  file_path?: string | null;
  original_name?: string | null;
  mime_type?: string | null;
  caption: string;
  alt_text: string;
  pixel_width?: number | null;
  pixel_height?: number | null;
  display_width_in?: number | null;
  display_height_in?: number | null;
  sha256?: string | null;
  revision: number;
  usages: AppendixBUsage[];
  file_url?: string | null;
};

export type AppendixBCategory = {
  code: `B-${number}`;
  category_code: string;
  title: string;
  order: number;
  category_uuid: string;
  is_not_applicable: boolean;
  not_applicable_reason: string;
  warning_acknowledged_at?: string | null;
  revision: number;
  items: AppendixBEvidenceItem[];
  warnings: AppendixBIssue[];
  errors: AppendixBIssue[];
  completion: "empty" | "complete" | "not_applicable";
};

export type AppendixBWorkspace = {
  schema_version: string;
  project_uuid: string;
  project_revision: number;
  categories: AppendixBCategory[];
  members: ReportMember[];
  organizations: ReportOrganization[];
  warnings: AppendixBIssue[];
  errors: AppendixBIssue[];
  completion: {
    category_total: number;
    completed: number;
    warning_count: number;
    error_count: number;
  };
};

export type AppendixBRecordInput = {
  subtype: string;
  title: string;
  starts_on?: string | null;
  ends_on?: string | null;
  organization_uuid?: string | null;
  location: string;
  sort_order: number;
  metadata: Record<string, unknown>;
  member_uuids: string[];
  related_item_uuids: string[];
};

export type AppendixBValidation = {
  project_uuid: string;
  project_revision: number;
  valid: boolean;
  errors: AppendixBIssue[];
  warnings: AppendixBIssue[];
  issues: AppendixBIssue[];
};

export type ReportOverview = {
  project_uuid: string;
  workflow_status: WorkflowStatus;
  template_package_id: string;
  template_edition: string;
  template_revision: string;
  field_matrix_version: string;
  section_count: number;
  completed_section_count: number;
  object_count: number;
  unbound_assessment_row_count: number;
  error_count: number;
  warning_count: number;
};

export type ReportTemplateRuleHint = {
  rule_id: string;
  category: string;
  sanitized_summary: string;
  approval_status: "pending" | "approved" | "rejected";
  runtime_behavior: string;
};

export type ReportTemplateRuleHints = {
  package_id: string;
  rules: ReportTemplateRuleHint[];
};

export type ReportSection = {
  section_uuid: string;
  section_key: string;
  parent_section_uuid?: string | null;
  title: string;
  level: number;
  sort_order: number;
  section_type: ReportSectionType;
  edit_policy: ReportEditPolicy;
  completion_status: ReportCompletionStatus;
  allowed_block_types: ReportBlockType[];
  form_key?: string | null;
  revision: number;
};

export type ParagraphBlockPayload = { text: string };
export type ListBlockPayload = { items: string[] };
export type KeyValueBlockPayload = { rows: Array<{ key: string; value: string }> };
export type DataTableBlockPayload = {
  schema_version: string;
  columns: Array<{ key: string; label: string }>;
  rows: Array<Record<string, string>>;
};
export type FigureBlockPayload = { figure_uuid: string; caption?: string | null };
export type ReferenceBlockPayload = { target_uuid: string; label?: string | null };
export type GeneratedBlockPayload = { status: "current" | "stale" | "not_generated"; source_summary?: string | null };

export type ReportBlockPayload =
  | ParagraphBlockPayload
  | ListBlockPayload
  | KeyValueBlockPayload
  | DataTableBlockPayload
  | FigureBlockPayload
  | ReferenceBlockPayload
  | GeneratedBlockPayload;

export type ReportBlock = {
  block_uuid: string;
  block_key: string;
  section_uuid: string;
  block_type: ReportBlockType | string;
  source_kind: "manual" | "imported" | "derived" | "template_constant";
  edit_policy: ReportEditPolicy;
  baseline_kind?: "template_default" | null;
  payload: ReportBlockPayload;
  sort_order: number;
  revision: number;
  generation_status?: "current" | "stale" | "not_generated" | null;
};

export type ReportSectionDetail = {
  section: ReportSection;
  blocks: ReportBlock[];
  issues: ReportIssue[];
};

export type ReportMetadata = {
  project_uuid: string;
  report_number: string;
  default_export_version: string;
  classification_level?: string;
  confidentiality_level?: string;
  compiler_member_uuid?: string | null;
  reviewer_member_uuid?: string | null;
  approver_member_uuid?: string | null;
  controlled_extension?: Record<string, string | number | boolean | null>;
  system_name?: string | null;
  assessed_organization_uuid?: string | null;
  assessed_organization_name?: string;
  client_organization_uuid?: string | null;
  client_organization_name?: string;
  effective_client_organization_uuid?: string | null;
  effective_client_organization_name?: string;
  revision: number;
  updated_at?: string | null;
};

export type ReportNumberAvailability = {
  report_number: string;
  available: boolean;
  duplicate_project_count: number;
  empty: boolean;
};

export type OrganizationType = "assessed" | "client" | "vendor" | "other";

export type ReportOrganization = {
  organization_uuid: string;
  organization_type: OrganizationType;
  name: string;
  address: string;
  postal_code: string;
  contact_name: string;
  contact_title: string;
  contact_department: string;
  office_phone: string;
  mobile_phone: string;
  email: string;
  active: boolean;
  sort_order: number;
  revision: number;
};

export type ReportOrganizationInput = Omit<ReportOrganization, "organization_uuid" | "revision">;

export type ReportMember = {
  member_uuid: string;
  organization_uuid?: string | null;
  name: string;
  team_role: "member" | "leader";
  is_leader: boolean;
  qualification_passed_at?: string | null;
  title: string;
  department: string;
  certificate_no: string;
  office_phone: string;
  mobile_phone: string;
  email: string;
  active: boolean;
  sort_order: number;
  revision: number;
};

export type ReportMemberInput = Omit<ReportMember, "member_uuid" | "revision">;

export type ReportOnsiteRecord = {
  entry_date: string;
  exit_date: string;
  member_uuids: string[];
};

export type ReportTravelRecord = {
  local_project: boolean;
  start_date?: string | null;
  end_date?: string | null;
  member_uuids: string[];
};

export type ReportPhaseDates = {
  phase_dates_uuid: string;
  preparation_start?: string | null;
  preparation_end?: string | null;
  plan_start?: string | null;
  plan_end?: string | null;
  onsite_start?: string | null;
  onsite_end?: string | null;
  report_start?: string | null;
  report_end?: string | null;
  travel_records: ReportTravelRecord[];
  onsite_records: ReportOnsiteRecord[];
  plan_review_date?: string | null;
  report_review_date?: string | null;
  approval_date?: string | null;
  assessment_start?: string | null;
  assessment_end?: string | null;
  compiled_date?: string | null;
  local_travel_not_applicable: boolean;
  revision: number;
};

export type ReportPhaseDatesInput = Pick<
  ReportPhaseDates,
  | "preparation_start"
  | "preparation_end"
  | "plan_start"
  | "plan_end"
  | "onsite_start"
  | "onsite_end"
  | "report_start"
  | "report_end"
  | "travel_records"
  | "onsite_records"
  | "plan_review_date"
  | "report_review_date"
  | "approval_date"
>;

export type ReportDistribution = {
  distribution_uuid: string;
  regulator_copies: number;
  client_copies: number;
  assessment_copies: number;
  total_copies: number;
  revision: number;
};

export type ReportSystemProfile = {
  profile_uuid: string;
  system_name: string;
  system_summary: string;
  critical_infrastructure_status: "recognized" | "not_recognized" | "";
  critical_infrastructure_department: string;
  level_filing_status: "filed" | "not_filed" | "";
  filing_s: string;
  filing_a: string;
  filing_g: string;
  filing_certificate_no: string;
  filing_system_same: boolean | null;
  filing_system_name: string;
  filing_difference: string;
  level_assessment_status: "assessed" | "assessing" | "not_assessed" | "";
  level_assessment_organization: string;
  level_assessment_date?: string | null;
  level_assessment_conclusion: string;
  cloud_dependency: "yes" | "no" | "";
  cloud_platform_name: string;
  cloud_assessment_status: "assessed" | "assessing" | "not_assessed" | "";
  cloud_assessment_organization: string;
  cloud_assessment_date?: string | null;
  cloud_assessment_conclusion: string;
  crypto_plan_status: "passed" | "not_passed" | "none" | "";
  crypto_plan_passed_at?: string | null;
  crypto_plan_assessment_mode: "self" | "commissioned" | "";
  crypto_plan_assessment_organization: string;
  operation_status: "running" | "not_running" | "";
  operation_started_at?: string | null;
  construction_stage: string;
  service_scope: "national" | "cross_province" | "province" | "cross_city" | "local" | "other" | "";
  service_scope_count?: number | null;
  service_scope_other: string;
  no_crypto_products: boolean;
  selected_algorithms: string[];
  other_algorithms: string[];
  application_catalog: string[];
  revision: number;
};

export type ReportSystemProfileInput = Omit<ReportSystemProfile, "profile_uuid" | "revision">;

type RawReportSystemProfile = Omit<
  ReportSystemProfile,
  "filing_s" | "filing_a" | "filing_g" | "level_assessment_date"
> & {
  filing_s?: string;
  filing_a?: string;
  filing_g?: string;
  level_filing_s?: string;
  level_filing_a?: string;
  level_filing_g?: string;
  level_assessment_date?: string | null;
  level_assessment_period?: string | null;
};

export type CryptoProduct = {
  product_uuid: string;
  name: string;
  model: string;
  manufacturer: string;
  certificate_no: string;
  quantity_text: string;
  normalized_quantity: number;
  use_mode: "exclusive" | "shared";
  classification: "certified" | "uncertified_domestic" | "foreign";
  sort_order: number;
  revision: number;
};

export type CryptoProductInput = Omit<CryptoProduct, "product_uuid" | "normalized_quantity" | "revision">;

export type CryptoProductCollection = {
  items: CryptoProduct[];
  summary: {
    total: number;
    exclusive: number;
    shared: number;
    certified: number;
    uncertified_domestic: number;
    foreign: number;
  };
};

export type ReportStandard = {
  standard_uuid: string;
  kind: "template_constant" | "manual";
  code: string;
  name: string;
  source_ref: string;
  sort_order: number;
  revision: number;
};

export type ReportStandardInput = Pick<ReportStandard, "code" | "name" | "source_ref" | "sort_order">;

export type SpecialIndicator = {
  indicator_uuid: string;
  manual_standard_uuid: string;
  indicator_code: string;
  indicator_name: string;
  description: string;
  sort_order: number;
  revision: number;
};

export type SpecialIndicatorInput = Omit<SpecialIndicator, "indicator_uuid" | "revision">;

export type ReportJsonObject = Record<string, string | number | boolean | null>;

export type AssessmentMethod = "访谈" | "文档审查" | "现场检查" | "配置检查" | "工具测试";

export type AssessmentObject = {
  object_uuid: string;
  object_type: string;
  name_snapshot: string;
  source_section_code?: string | null;
  source_row_id?: number | null;
  properties: ReportJsonObject;
  subsystem_name?: string | null;
  methods: AssessmentMethod[];
  remark: string;
  subsystem_revision?: number | null;
  active: boolean;
  reference_count: number;
  revision: number;
};

export type AssessmentObjectInput = Pick<
  AssessmentObject,
  "object_type" | "name_snapshot" | "source_section_code" | "source_row_id" | "properties" | "active"
>;

type RawAssessmentObject = Omit<AssessmentObject, "properties"> & {
  properties?: ReportJsonObject;
  properties_json?: string;
};

export type BindingMatch = {
  object_uuid: string;
  name_snapshot: string;
  object_type: string;
};

export type BindingPreviewItem = {
  source_row_id: number;
  section_code: string;
  object_name: string;
  subsystem: string;
  matches: BindingMatch[];
};

export type BindingPreview = Record<"exact" | "candidate" | "ambiguous" | "unmatched", BindingPreviewItem[]>;
export type BindingChoice = { source_row_id: number; object_uuid: string };

export type ObjectSubsystem = {
  binding_uuid: string;
  object_uuid: string;
  subsystem_name: string;
  methods: AssessmentMethod[];
  remark: string;
  revision: number;
};

export type ObjectSubsystemInput = Omit<ObjectSubsystem, "binding_uuid" | "revision"> & { expected_revision?: number };

export type ObjectRelationType = "contains" | "connects" | "depends_on" | "protects" | "uses" | "other";

export type ObjectRelation = {
  relation_uuid: string;
  source_object_uuid: string;
  target_object_uuid: string;
  relation_type: ObjectRelationType;
  properties: ReportJsonObject;
  active: boolean;
  revision: number;
};

type RawObjectRelation = Omit<ObjectRelation, "properties"> & {
  properties?: ReportJsonObject;
  properties_json?: string;
};

export type ObjectRelationInput = Omit<ObjectRelation, "relation_uuid" | "revision">;
export type CorrectionKind = "confidentiality" | "integrity";

export type CorrectionRelation = {
  correction_uuid: string;
  a2_object_uuid: string;
  a4_object_uuid: string;
  correction_kind: CorrectionKind;
  a2_metric_code: string;
  a4_metric_code: string;
  original_references: {
    a2_row_id: number;
    a4_row_id: number;
  };
  revision: number;
};

export type CorrectionRelationInput = Omit<CorrectionRelation, "correction_uuid" | "revision">;

export type TransmissionRelationKind = "confidentiality" | "integrity";

export type AppendixTransmissionRelation = {
  correction_uuid: string;
  kind: TransmissionRelationKind;
  a2_object_uuid: string;
  a4_object_uuid: string;
  revision: number;
};

export type AppendixTransmissionObject = {
  object_uuid: string;
  object_name: string;
  subsystem: string;
  available_kinds: TransmissionRelationKind[];
  relations: AppendixTransmissionRelation[];
};

export type AppendixTransmissionRelations = {
  project_revision: number;
  shared_subsystems: string[];
  a2_objects: AppendixTransmissionObject[];
  a4_objects: AppendixTransmissionObject[];
};

export type AppendixTransmissionRelationWrite = {
  kind: TransmissionRelationKind;
  a4_object_uuid: string;
  a2_object_uuid: string | null;
  expected_correction_uuid: string | null;
  expected_revision: number | null;
};

export type DuplicateObjectGroup = {
  object_type: string;
  normalized_name: string;
  objects: AssessmentObject[];
  requires_confirmation: boolean;
};

export type ReportValidation = {
  errors: number;
  warnings: number;
  info: number;
  issues: ReportIssue[];
};

export type DerivedIssue = {
  code: string;
  message: string;
  field?: string | null;
  section_code?: string | null;
  indicator?: string | null;
  object_uuid?: string | null;
  entity_uuid?: string | null;
  block_key?: string | null;
  details?: Record<string, unknown>;
};

export type GenerationImpact = {
  project_revision: number;
  current_run_uuid?: string | null;
  rule_set_id: string;
  rule_set_hash: string;
  current_input_hash: string;
  last_input_hash?: string | null;
  has_changes: boolean;
  affected_blocks: string[];
  overrides_requiring_review: string[];
  can_generate: boolean;
  issues: DerivedIssue[];
};

export type DerivedGenerationRun = {
  run_uuid: string;
  status: "current" | "needs_input" | "failed";
  rule_set_id: string;
  rule_set_hash: string;
  input_hash: string;
  projection?: Record<string, unknown> | null;
  issues: DerivedIssue[];
  state_revision: number;
  project_revision: number;
  started_at: string;
  finished_at: string;
};

export type ThreatCatalogItem = {
  id: string;
  layer: string;
  description: string;
};

export type DerivedRisk = {
  risk_uuid: string;
  finding_uuid: string;
  indicator_code: string;
  indicator_name: string;
  layer_code: string;
  final_indicator_result: "部分符合" | "不符合";
  problem_description: string;
  problem_items: Array<Record<string, unknown>>;
  risk_level?: "high" | "medium" | "low" | null;
  analysis_baseline: Record<string, unknown>;
  analysis_override?: { text?: string } | null;
  override_reason: string;
  confirmation_status: "needs_input" | "unconfirmed" | "confirmed";
  source_hash: string;
  threat_ids: string[];
  revision: number;
};

export type DerivedRiskCollection = {
  project_revision: number;
  threat_catalog: ThreatCatalogItem[];
  items: DerivedRisk[];
};

export type DerivedBlock = {
  block_uuid: string;
  block_key: string;
  edit_policy: ReportEditPolicy;
  block_revision: number;
  revision_uuid: string;
  revision: number;
  baseline: Record<string, unknown>;
  override?: Record<string, unknown> | null;
  effective: Record<string, unknown>;
  override_reason: string;
  generation_status: "not_generated" | "current" | "stale" | "failed";
  confirmation_status: "unconfirmed" | "confirmed" | "review_required";
  rule_set_id: string;
  rule_id: string;
  source_hash: string;
};

export type ConsistencyResult = {
  check_uuid: string;
  run_uuid?: string | null;
  status: "valid" | "invalid" | "needs_input";
  issues: DerivedIssue[];
  context_hash?: string | null;
  state_revision: number;
  project_revision: number;
  checked_at: string;
};

export type DerivedReview = {
  project_revision: number;
  current_run_uuid?: string | null;
  current_input_hash?: string | null;
  blocks: DerivedBlock[];
  latest_consistency?: ConsistencyResult | null;
};

export type ReportExportMode = "draft" | "final";
export type ReportExportIssue = {
  severity: "error" | "warning" | "info";
  code: string;
  message: string;
  field?: string | null;
  section_code?: string | null;
  indicator?: string | null;
  object_uuid?: string | null;
  object_name?: string | null;
  block_id?: string | null;
  details?: Record<string, unknown>;
};

export type ReportExportValidation = {
  project_uuid: string;
  project_revision: number;
  mode: ReportExportMode;
  errors: number;
  warnings: number;
  issues: ReportExportIssue[];
  valid: boolean;
};

export type ReportExportJobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";
export type ReportExportJob = {
  job_uuid: string;
  project_id: number;
  mode: ReportExportMode;
  version: string;
  status: ReportExportJobStatus;
  project_revision: number;
  template_package_id: string;
  template_asset_set_hash: string;
  template_docx_hash: string;
  r2_context_hash?: string | null;
  r3_context_hash?: string | null;
  assembly_context_hash?: string | null;
  snapshot_uuid?: string | null;
  docx_hash?: string | null;
  page_count?: number | null;
  word_refresh_status: "not_started" | "skipped" | "succeeded" | "failed";
  issues: ReportExportIssue[];
  error_code?: string | null;
  error_message?: string | null;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  download_available: boolean;
  roundtrip_capable?: boolean;
};

export type ReportExportIssueCollection = {
  job_uuid: string;
  status: ReportExportJobStatus;
  errors: ReportExportIssue[];
  warnings: ReportExportIssue[];
  info: ReportExportIssue[];
};

const reportRoot = (projectUuid: string) => `/api/projects/${encodeURIComponent(projectUuid)}/report`;

const revisionHeaders = (revision: number) => ({ "If-Match": String(revision) });
const revisionDeleteUrl = (url: string, revision: number) => `${url}?expected_revision=${encodeURIComponent(String(revision))}`;

export function getReportOverview(projectUuid: string): Promise<ReportOverview> {
  return request<ReportOverview>(`${reportRoot(projectUuid)}/overview`);
}

export function getReportTemplateRuleHints(packageId: string): Promise<ReportTemplateRuleHints> {
  return request<ReportTemplateRuleHints>(`/api/report-templates/${encodeURIComponent(packageId)}/rule-hints`);
}

export function getReportSections(projectUuid: string): Promise<ReportSection[]> {
  return request<ReportSection[]>(`${reportRoot(projectUuid)}/sections`);
}

export function getReportSection(projectUuid: string, sectionUuid: string): Promise<ReportSectionDetail> {
  return request<ReportSectionDetail>(`${reportRoot(projectUuid)}/sections/${encodeURIComponent(sectionUuid)}`);
}

export function getReportMetadata(projectUuid: string): Promise<ReportMetadata> {
  return request<ReportMetadata>(`${reportRoot(projectUuid)}/metadata`);
}

export function getReportNumberAvailability(projectUuid: string, reportNumber: string): Promise<ReportNumberAvailability> {
  return request<ReportNumberAvailability>(
    `${reportRoot(projectUuid)}/metadata/report-number-availability?report_number=${encodeURIComponent(reportNumber)}`
  );
}

export function updateReportMetadata(
  projectUuid: string,
  revision: number,
  payload: Partial<Pick<
    ReportMetadata,
    | "report_number"
    | "default_export_version"
    | "classification_level"
    | "confidentiality_level"
    | "compiler_member_uuid"
    | "reviewer_member_uuid"
    | "approver_member_uuid"
    | "controlled_extension"
  >>
): Promise<ReportMetadata> {
  return request<ReportMetadata>(`${reportRoot(projectUuid)}/metadata`, {
    method: "PUT",
    headers: revisionHeaders(revision),
    body: JSON.stringify({ ...payload, expected_revision: revision })
  });
}

export function listReportOrganizations(projectUuid: string): Promise<ReportOrganization[]> {
  return request<ReportOrganization[]>(`${reportRoot(projectUuid)}/organizations`);
}

export function createReportOrganization(projectUuid: string, payload: ReportOrganizationInput): Promise<ReportOrganization> {
  return request<ReportOrganization>(`${reportRoot(projectUuid)}/organizations`, {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function updateReportOrganization(projectUuid: string, organization: ReportOrganization): Promise<ReportOrganization> {
  const payload: ReportOrganizationInput = {
    organization_type: organization.organization_type,
    name: organization.name,
    address: organization.address,
    postal_code: organization.postal_code,
    contact_name: organization.contact_name,
    contact_title: organization.contact_title,
    contact_department: organization.contact_department,
    office_phone: organization.office_phone,
    mobile_phone: organization.mobile_phone,
    email: organization.email,
    active: organization.active,
    sort_order: organization.sort_order
  };
  return request<ReportOrganization>(`${reportRoot(projectUuid)}/organizations/${encodeURIComponent(organization.organization_uuid)}`, {
    method: "PUT",
    headers: revisionHeaders(organization.revision),
    body: JSON.stringify({ ...payload, expected_revision: organization.revision })
  });
}

export function deleteReportOrganization(projectUuid: string, organization: Pick<ReportOrganization, "organization_uuid" | "revision">): Promise<ReportOrganization> {
  const url = `${reportRoot(projectUuid)}/organizations/${encodeURIComponent(organization.organization_uuid)}`;
  return request<ReportOrganization>(revisionDeleteUrl(url, organization.revision), { method: "DELETE", headers: revisionHeaders(organization.revision) });
}

export function listReportMembers(projectUuid: string): Promise<ReportMember[]> {
  return request<ReportMember[]>(`${reportRoot(projectUuid)}/members`);
}

export function createReportMember(projectUuid: string, payload: ReportMemberInput): Promise<ReportMember> {
  return request<ReportMember>(`${reportRoot(projectUuid)}/members`, { method: "POST", body: JSON.stringify(payload) });
}

export function updateReportMember(projectUuid: string, member: ReportMember): Promise<ReportMember> {
  const payload: ReportMemberInput = {
    organization_uuid: member.organization_uuid ?? null,
    name: member.name,
    team_role: member.team_role,
    is_leader: member.team_role === "leader",
    qualification_passed_at: member.qualification_passed_at ?? null,
    title: member.title,
    department: member.department,
    certificate_no: member.certificate_no,
    office_phone: member.office_phone,
    mobile_phone: member.mobile_phone,
    email: member.email,
    active: member.active,
    sort_order: member.sort_order
  };
  return request<ReportMember>(`${reportRoot(projectUuid)}/members/${encodeURIComponent(member.member_uuid)}`, {
    method: "PUT",
    headers: revisionHeaders(member.revision),
    body: JSON.stringify({ ...payload, expected_revision: member.revision })
  });
}

export function deleteReportMember(projectUuid: string, member: Pick<ReportMember, "member_uuid" | "revision">): Promise<ReportMember> {
  const url = `${reportRoot(projectUuid)}/members/${encodeURIComponent(member.member_uuid)}`;
  return request<ReportMember>(revisionDeleteUrl(url, member.revision), { method: "DELETE", headers: revisionHeaders(member.revision) });
}

export function getReportPhaseDates(projectUuid: string): Promise<ReportPhaseDates> {
  return request<ReportPhaseDates>(`${reportRoot(projectUuid)}/phase-dates`);
}

export function updateReportPhaseDates(projectUuid: string, revision: number, payload: ReportPhaseDatesInput): Promise<ReportPhaseDates> {
  return request<ReportPhaseDates>(`${reportRoot(projectUuid)}/phase-dates`, {
    method: "PUT",
    headers: revisionHeaders(revision),
    body: JSON.stringify({ ...payload, expected_revision: revision })
  });
}

export function getReportDistribution(projectUuid: string): Promise<ReportDistribution> {
  return request<ReportDistribution>(`${reportRoot(projectUuid)}/distribution`);
}

export function updateReportDistribution(
  projectUuid: string,
  revision: number,
  payload: Pick<ReportDistribution, "regulator_copies" | "client_copies" | "assessment_copies">
): Promise<ReportDistribution> {
  return request<ReportDistribution>(`${reportRoot(projectUuid)}/distribution`, {
    method: "PUT",
    headers: revisionHeaders(revision),
    body: JSON.stringify({ ...payload, expected_revision: revision })
  });
}

export function getReportSystemProfile(projectUuid: string): Promise<ReportSystemProfile> {
  return request<RawReportSystemProfile>(`${reportRoot(projectUuid)}/system-profile`).then((profile) => ({
    ...profile,
    filing_s: profile.filing_s ?? profile.level_filing_s ?? "",
    filing_a: profile.filing_a ?? profile.level_filing_a ?? "",
    filing_g: profile.filing_g ?? profile.level_filing_g ?? "",
    level_assessment_date: profile.level_assessment_date ?? profile.level_assessment_period ?? null
  }));
}

export function updateReportSystemProfile(
  projectUuid: string,
  revision: number,
  payload: ReportSystemProfileInput
): Promise<ReportSystemProfile> {
  return request<ReportSystemProfile>(`${reportRoot(projectUuid)}/system-profile`, {
    method: "PUT",
    headers: revisionHeaders(revision),
    body: JSON.stringify({ ...payload, expected_revision: revision })
  });
}

export function listCryptoProducts(projectUuid: string): Promise<CryptoProductCollection> {
  return request<CryptoProductCollection>(`${reportRoot(projectUuid)}/crypto-products`);
}

export function createCryptoProduct(projectUuid: string, payload: CryptoProductInput): Promise<CryptoProduct> {
  return request<CryptoProduct>(`${reportRoot(projectUuid)}/crypto-products`, { method: "POST", body: JSON.stringify(payload) });
}

export function updateCryptoProduct(projectUuid: string, product: CryptoProduct): Promise<CryptoProduct> {
  const payload: CryptoProductInput = {
    name: product.name,
    model: product.model,
    manufacturer: product.manufacturer,
    certificate_no: product.certificate_no,
    quantity_text: product.quantity_text,
    use_mode: product.use_mode,
    classification: product.classification,
    sort_order: product.sort_order
  };
  return request<CryptoProduct>(`${reportRoot(projectUuid)}/crypto-products/${encodeURIComponent(product.product_uuid)}`, {
    method: "PUT",
    headers: revisionHeaders(product.revision),
    body: JSON.stringify({ ...payload, expected_revision: product.revision })
  });
}

export function deleteCryptoProduct(projectUuid: string, product: Pick<CryptoProduct, "product_uuid" | "revision">): Promise<CryptoProduct> {
  const url = `${reportRoot(projectUuid)}/crypto-products/${encodeURIComponent(product.product_uuid)}`;
  return request<CryptoProduct>(revisionDeleteUrl(url, product.revision), { method: "DELETE", headers: revisionHeaders(product.revision) });
}

export function listReportStandards(projectUuid: string): Promise<ReportStandard[]> {
  return request<ReportStandard[]>(`${reportRoot(projectUuid)}/standards`);
}

export function createReportStandard(projectUuid: string, payload: ReportStandardInput): Promise<ReportStandard> {
  return request<ReportStandard>(`${reportRoot(projectUuid)}/standards`, { method: "POST", body: JSON.stringify(payload) });
}

export function updateReportStandard(projectUuid: string, standard: ReportStandard): Promise<ReportStandard> {
  const payload: ReportStandardInput = {
    code: standard.code,
    name: standard.name,
    source_ref: standard.source_ref,
    sort_order: standard.sort_order
  };
  return request<ReportStandard>(`${reportRoot(projectUuid)}/standards/${encodeURIComponent(standard.standard_uuid)}`, {
    method: "PUT",
    headers: revisionHeaders(standard.revision),
    body: JSON.stringify({ ...payload, expected_revision: standard.revision })
  });
}

export function deleteReportStandard(projectUuid: string, standard: Pick<ReportStandard, "standard_uuid" | "revision">): Promise<ReportStandard> {
  const url = `${reportRoot(projectUuid)}/standards/${encodeURIComponent(standard.standard_uuid)}`;
  return request<ReportStandard>(revisionDeleteUrl(url, standard.revision), { method: "DELETE", headers: revisionHeaders(standard.revision) });
}

export function listSpecialIndicators(projectUuid: string): Promise<SpecialIndicator[]> {
  return request<SpecialIndicator[]>(`${reportRoot(projectUuid)}/special-indicators`);
}

export function createSpecialIndicator(projectUuid: string, payload: SpecialIndicatorInput): Promise<SpecialIndicator> {
  return request<SpecialIndicator>(`${reportRoot(projectUuid)}/special-indicators`, { method: "POST", body: JSON.stringify(payload) });
}

export function updateSpecialIndicator(projectUuid: string, indicator: SpecialIndicator): Promise<SpecialIndicator> {
  const payload: SpecialIndicatorInput = {
    manual_standard_uuid: indicator.manual_standard_uuid,
    indicator_code: indicator.indicator_code,
    indicator_name: indicator.indicator_name,
    description: indicator.description,
    sort_order: indicator.sort_order
  };
  return request<SpecialIndicator>(`${reportRoot(projectUuid)}/special-indicators/${encodeURIComponent(indicator.indicator_uuid)}`, {
    method: "PUT",
    headers: revisionHeaders(indicator.revision),
    body: JSON.stringify({ ...payload, expected_revision: indicator.revision })
  });
}

export function deleteSpecialIndicator(projectUuid: string, indicator: Pick<SpecialIndicator, "indicator_uuid" | "revision">): Promise<SpecialIndicator> {
  const url = `${reportRoot(projectUuid)}/special-indicators/${encodeURIComponent(indicator.indicator_uuid)}`;
  return request<SpecialIndicator>(revisionDeleteUrl(url, indicator.revision), { method: "DELETE", headers: revisionHeaders(indicator.revision) });
}

function parseJsonObject(value: string | undefined, fallback: ReportJsonObject = {}): ReportJsonObject {
  if (!value) return fallback;
  try {
    const parsed = JSON.parse(value) as unknown;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as ReportJsonObject : fallback;
  } catch {
    return fallback;
  }
}

function normalizeAssessmentObject(value: RawAssessmentObject): AssessmentObject {
  return {
    ...value,
    properties: value.properties ?? parseJsonObject(value.properties_json),
    methods: value.methods ?? [],
    remark: value.remark ?? ""
  };
}

function normalizeObjectRelation(value: RawObjectRelation): ObjectRelation {
  return { ...value, properties: value.properties ?? parseJsonObject(value.properties_json) };
}

export function listAssessmentObjects(projectUuid: string): Promise<AssessmentObject[]> {
  return request<RawAssessmentObject[]>(`${reportRoot(projectUuid)}/objects`).then((items) => items.map(normalizeAssessmentObject));
}

export function createAssessmentObject(projectUuid: string, payload: AssessmentObjectInput): Promise<AssessmentObject> {
  return request<RawAssessmentObject>(`${reportRoot(projectUuid)}/objects`, {
    method: "POST",
    body: JSON.stringify(payload)
  }).then(normalizeAssessmentObject);
}

export function updateAssessmentObject(projectUuid: string, object: AssessmentObject): Promise<AssessmentObject> {
  const payload: AssessmentObjectInput = {
    object_type: object.object_type,
    name_snapshot: object.name_snapshot,
    source_section_code: object.source_section_code ?? null,
    source_row_id: object.source_row_id ?? null,
    properties: object.properties,
    active: object.active
  };
  return request<RawAssessmentObject>(`${reportRoot(projectUuid)}/objects/${encodeURIComponent(object.object_uuid)}`, {
    method: "PUT",
    headers: revisionHeaders(object.revision),
    body: JSON.stringify({ ...payload, expected_revision: object.revision })
  }).then(normalizeAssessmentObject);
}

export function deleteAssessmentObject(projectUuid: string, object: AssessmentObject): Promise<AssessmentObject> {
  const url = `${reportRoot(projectUuid)}/objects/${encodeURIComponent(object.object_uuid)}`;
  return request<RawAssessmentObject>(revisionDeleteUrl(url, object.revision), {
    method: "DELETE", headers: revisionHeaders(object.revision)
  }).then(normalizeAssessmentObject);
}

export function listDuplicateObjectCandidates(projectUuid: string): Promise<DuplicateObjectGroup[]> {
  return request<Array<Omit<DuplicateObjectGroup, "objects"> & { objects: RawAssessmentObject[] }>>(
    `${reportRoot(projectUuid)}/objects/duplicate-candidates`, { method: "POST" }
  ).then((groups) => groups.map((group) => ({ ...group, objects: group.objects.map(normalizeAssessmentObject) })));
}

export function mergeAssessmentObjects(projectUuid: string, source: AssessmentObject, target: AssessmentObject): Promise<AssessmentObject> {
  return request<RawAssessmentObject>(`${reportRoot(projectUuid)}/objects/${encodeURIComponent(source.object_uuid)}/merge`, {
    method: "POST",
    body: JSON.stringify({
      target_object_uuid: target.object_uuid,
      source_expected_revision: source.revision,
      target_expected_revision: target.revision
    })
  }).then(normalizeAssessmentObject);
}

export function previewAppendixBindings(projectUuid: string): Promise<BindingPreview> {
  return request<BindingPreview>(`${reportRoot(projectUuid)}/appendix-a-bindings/preview`, { method: "POST" });
}

export function confirmAppendixBindings(projectUuid: string, choices: BindingChoice[]): Promise<{ bound_count: number; bindings: BindingChoice[] }> {
  return request(`${reportRoot(projectUuid)}/appendix-a-bindings/confirm`, {
    method: "POST",
    body: JSON.stringify({ choices })
  });
}

export function listObjectSubsystems(projectUuid: string): Promise<ObjectSubsystem[]> {
  return request<ObjectSubsystem[]>(`${reportRoot(projectUuid)}/assessment-object-subsystems`);
}

export function upsertObjectSubsystem(projectUuid: string, payload: ObjectSubsystemInput): Promise<ObjectSubsystem> {
  const headers = typeof payload.expected_revision === "number" ? revisionHeaders(payload.expected_revision) : undefined;
  return request<ObjectSubsystem>(`${reportRoot(projectUuid)}/assessment-object-subsystems`, {
    method: "PUT", headers, body: JSON.stringify(payload)
  });
}

export function listObjectRelations(projectUuid: string): Promise<ObjectRelation[]> {
  return request<RawObjectRelation[]>(`${reportRoot(projectUuid)}/object-relations`).then((items) => items.map(normalizeObjectRelation));
}

export function createObjectRelation(projectUuid: string, payload: ObjectRelationInput): Promise<ObjectRelation> {
  return request<RawObjectRelation>(`${reportRoot(projectUuid)}/object-relations`, {
    method: "POST", body: JSON.stringify(payload)
  }).then(normalizeObjectRelation);
}

export function updateObjectRelation(projectUuid: string, relation: ObjectRelation): Promise<ObjectRelation> {
  const payload: ObjectRelationInput = {
    source_object_uuid: relation.source_object_uuid,
    target_object_uuid: relation.target_object_uuid,
    relation_type: relation.relation_type,
    properties: relation.properties,
    active: relation.active
  };
  return request<RawObjectRelation>(`${reportRoot(projectUuid)}/object-relations/${encodeURIComponent(relation.relation_uuid)}`, {
    method: "PUT", headers: revisionHeaders(relation.revision),
    body: JSON.stringify({ ...payload, expected_revision: relation.revision })
  }).then(normalizeObjectRelation);
}

export function deleteObjectRelation(projectUuid: string, relation: ObjectRelation): Promise<ObjectRelation> {
  const url = `${reportRoot(projectUuid)}/object-relations/${encodeURIComponent(relation.relation_uuid)}`;
  return request<RawObjectRelation>(revisionDeleteUrl(url, relation.revision), {
    method: "DELETE", headers: revisionHeaders(relation.revision)
  }).then(normalizeObjectRelation);
}

export function listCorrectionRelations(projectUuid: string): Promise<CorrectionRelation[]> {
  return request<CorrectionRelation[]>(`${reportRoot(projectUuid)}/result-correction-relations`);
}

export function createCorrectionRelation(projectUuid: string, payload: CorrectionRelationInput): Promise<CorrectionRelation> {
  return request<CorrectionRelation>(`${reportRoot(projectUuid)}/result-correction-relations`, {
    method: "POST", body: JSON.stringify(payload)
  });
}

export function updateCorrectionRelation(projectUuid: string, relation: CorrectionRelation): Promise<CorrectionRelation> {
  const payload: CorrectionRelationInput = {
    a2_object_uuid: relation.a2_object_uuid,
    a4_object_uuid: relation.a4_object_uuid,
    correction_kind: relation.correction_kind,
    a2_metric_code: relation.a2_metric_code,
    a4_metric_code: relation.a4_metric_code,
    original_references: relation.original_references
  };
  return request<CorrectionRelation>(`${reportRoot(projectUuid)}/result-correction-relations/${encodeURIComponent(relation.correction_uuid)}`, {
    method: "PUT", headers: revisionHeaders(relation.revision),
    body: JSON.stringify({ ...payload, expected_revision: relation.revision })
  });
}

export function deleteCorrectionRelation(projectUuid: string, relation: CorrectionRelation): Promise<CorrectionRelation> {
  const url = `${reportRoot(projectUuid)}/result-correction-relations/${encodeURIComponent(relation.correction_uuid)}`;
  return request<CorrectionRelation>(revisionDeleteUrl(url, relation.revision), {
    method: "DELETE", headers: revisionHeaders(relation.revision)
  });
}

export function getAppendixTransmissionRelations(projectUuid: string): Promise<AppendixTransmissionRelations> {
  return request<AppendixTransmissionRelations>(
    `${reportRoot(projectUuid)}/appendix-transmission-relations`
  );
}

export function updateAppendixTransmissionRelation(
  projectUuid: string,
  payload: AppendixTransmissionRelationWrite
): Promise<AppendixTransmissionRelations> {
  const headers = typeof payload.expected_revision === "number"
    ? revisionHeaders(payload.expected_revision)
    : undefined;
  return request<AppendixTransmissionRelations>(
    `${reportRoot(projectUuid)}/appendix-transmission-relations`,
    { method: "PUT", headers, body: JSON.stringify(payload) }
  );
}

export function updateReportBlock(
  projectUuid: string,
  block: Pick<ReportBlock, "block_uuid" | "revision" | "payload">
): Promise<ReportBlock> {
  return request<ReportBlock>(`${reportRoot(projectUuid)}/blocks/${encodeURIComponent(block.block_uuid)}`, {
    method: "PUT",
    headers: revisionHeaders(block.revision),
    body: JSON.stringify({ payload: block.payload, expected_revision: block.revision })
  });
}

export function deleteReportBlock(projectUuid: string, block: Pick<ReportBlock, "block_uuid" | "revision">): Promise<ReportBlock> {
  const url = `${reportRoot(projectUuid)}/blocks/${encodeURIComponent(block.block_uuid)}`;
  return request<ReportBlock>(revisionDeleteUrl(url, block.revision), { method: "DELETE", headers: revisionHeaders(block.revision) });
}

export function updateReportSection(
  projectUuid: string,
  section: Pick<ReportSection, "section_uuid" | "revision" | "completion_status">,
  completionStatus: "not_started" | "in_progress" | "complete"
): Promise<ReportSection> {
  return request<ReportSection>(`${reportRoot(projectUuid)}/sections/${encodeURIComponent(section.section_uuid)}`, {
    method: "PUT",
    headers: revisionHeaders(section.revision),
    body: JSON.stringify({ completion_status: completionStatus, expected_revision: section.revision })
  });
}

export function createReportBlock(
  projectUuid: string,
  sectionUuid: string,
  blockType: ReportBlockType,
  payload: ReportBlockPayload
): Promise<ReportBlock> {
  return request<ReportBlock>(`${reportRoot(projectUuid)}/sections/${encodeURIComponent(sectionUuid)}/blocks`, {
    method: "POST",
    body: JSON.stringify({ block_type: blockType, payload })
  });
}

export function validateReport(projectUuid: string): Promise<ReportValidation> {
  return request<ReportValidation>(`${reportRoot(projectUuid)}/validate`, { method: "POST", body: "{}" });
}

export function getAppendixB(projectUuid: string): Promise<AppendixBWorkspace> {
  return request<AppendixBWorkspace>(`${reportRoot(projectUuid)}/appendix-b`);
}

export function updateAppendixBCategory(
  projectUuid: string,
  category: AppendixBCategory,
  projectRevision: number,
  payload: { is_not_applicable: boolean; not_applicable_reason: string; acknowledge_warning: boolean }
): Promise<AppendixBWorkspace> {
  return request<AppendixBWorkspace>(
    `${reportRoot(projectUuid)}/appendix-b/${encodeURIComponent(category.category_code)}`,
    {
      method: "PUT",
      body: JSON.stringify({
        expected_project_revision: projectRevision,
        expected_revision: category.revision,
        ...payload
      })
    }
  );
}

export function createAppendixBRecord(
  projectUuid: string,
  categoryCode: string,
  projectRevision: number,
  payload: AppendixBRecordInput
): Promise<AppendixBEvidenceItem> {
  return request<AppendixBEvidenceItem>(
    `${reportRoot(projectUuid)}/appendix-b/${encodeURIComponent(categoryCode)}/items`,
    {
      method: "POST",
      body: JSON.stringify({ expected_project_revision: projectRevision, ...payload })
    }
  );
}

export function updateAppendixBRecord(
  item: AppendixBEvidenceItem,
  projectRevision: number,
  payload: AppendixBRecordInput
): Promise<AppendixBEvidenceItem> {
  return request<AppendixBEvidenceItem>(`/api/report-evidence-items/${encodeURIComponent(item.item_uuid)}`, {
    method: "PUT",
    body: JSON.stringify({
      expected_project_revision: projectRevision,
      expected_revision: item.revision,
      ...payload
    })
  });
}

export function updateAppendixBImage(
  item: AppendixBEvidenceItem,
  projectRevision: number,
  payload: { subtype: string; caption: string; alt_text: string; sort_order: number }
): Promise<AppendixBEvidenceItem> {
  return request<AppendixBEvidenceItem>(`/api/report-evidence-items/${encodeURIComponent(item.item_uuid)}`, {
    method: "PUT",
    body: JSON.stringify({
      expected_project_revision: projectRevision,
      expected_revision: item.revision,
      ...payload
    })
  });
}

export function deleteAppendixBItem(
  item: Pick<AppendixBEvidenceItem, "item_uuid" | "revision">,
  projectRevision: number
): Promise<AppendixBEvidenceItem> {
  const query = new URLSearchParams({
    expected_project_revision: String(projectRevision),
    expected_revision: String(item.revision)
  });
  return request<AppendixBEvidenceItem>(
    `/api/report-evidence-items/${encodeURIComponent(item.item_uuid)}?${query.toString()}`,
    { method: "DELETE" }
  );
}

export function reorderAppendixBRecords(
  projectUuid: string,
  categoryCode: string,
  projectRevision: number,
  itemUuids: string[]
): Promise<AppendixBEvidenceItem[]> {
  return request<AppendixBEvidenceItem[]>(
    `${reportRoot(projectUuid)}/appendix-b/${encodeURIComponent(categoryCode)}/reorder`,
    {
      method: "PUT",
      body: JSON.stringify({ expected_project_revision: projectRevision, item_uuids: itemUuids })
    }
  );
}

export function uploadAppendixBImages(
  parentItemUuid: string,
  projectRevision: number,
  payload: { subtype: string; caption: string; alt_text: string; files: File[] }
): Promise<AppendixBEvidenceItem[]> {
  const form = new FormData();
  form.append("expected_project_revision", String(projectRevision));
  form.append("subtype", payload.subtype);
  form.append("caption", payload.caption);
  form.append("alt_text", payload.alt_text);
  payload.files.forEach((file) => form.append("files", file));
  return requestFormData<AppendixBEvidenceItem[]>(
    `/api/report-evidence-items/${encodeURIComponent(parentItemUuid)}/images`, form
  );
}

export function replaceAppendixBImage(
  item: AppendixBEvidenceItem,
  projectRevision: number,
  file: File
): Promise<AppendixBEvidenceItem> {
  const form = new FormData();
  form.append("expected_project_revision", String(projectRevision));
  form.append("expected_revision", String(item.revision));
  form.append("file", file);
  return requestFormData<AppendixBEvidenceItem>(
    `/api/report-evidence-items/${encodeURIComponent(item.item_uuid)}/file`, form
  );
}

export function validateAppendixB(projectUuid: string, projectRevision: number): Promise<AppendixBValidation> {
  return request<AppendixBValidation>(`${reportRoot(projectUuid)}/appendix-b/validations`, {
    method: "POST",
    body: JSON.stringify({ expected_project_revision: projectRevision })
  });
}

export function previewDerivedGeneration(projectUuid: string): Promise<GenerationImpact> {
  return request<GenerationImpact>(`${reportRoot(projectUuid)}/generation/impact-preview`, {
    method: "POST"
  });
}

export function createDerivedGenerationRun(
  projectUuid: string,
  expectedProjectRevision: number
): Promise<DerivedGenerationRun> {
  return request<DerivedGenerationRun>(`${reportRoot(projectUuid)}/generation/runs`, {
    method: "POST",
    body: JSON.stringify({ expected_project_revision: expectedProjectRevision })
  });
}

export function getDerivedGenerationReview(projectUuid: string): Promise<DerivedReview> {
  return request<DerivedReview>(`${reportRoot(projectUuid)}/generation/review`);
}

export function listDerivedRisks(projectUuid: string): Promise<DerivedRiskCollection> {
  return request<DerivedRiskCollection>(`${reportRoot(projectUuid)}/risks`);
}

export function updateDerivedRisk(
  projectUuid: string,
  risk: DerivedRisk,
  projectRevision: number,
  payload: {
    risk_level?: "high" | "medium" | "low" | null;
    threat_ids: string[];
    analysis_text?: string | null;
    override_reason?: string;
    confirm: boolean;
  }
): Promise<{ project_revision: number; risk: DerivedRisk }> {
  return request<{ project_revision: number; risk: DerivedRisk }>(
    `${reportRoot(projectUuid)}/risks/${encodeURIComponent(risk.risk_uuid)}`,
    {
      method: "PUT",
      body: JSON.stringify({
        expected_project_revision: projectRevision,
        expected_revision: risk.revision,
        ...payload
      })
    }
  );
}

export function overrideDerivedBlock(
  projectUuid: string,
  blockUuid: string,
  projectRevision: number,
  override: Record<string, unknown>,
  overrideReason: string
): Promise<{ project_revision: number; block: DerivedBlock }> {
  return request<{ project_revision: number; block: DerivedBlock }>(
    `${reportRoot(projectUuid)}/derived-blocks/${encodeURIComponent(blockUuid)}/override`,
    {
      method: "PUT",
      body: JSON.stringify({
        expected_project_revision: projectRevision,
        override,
        override_reason: overrideReason
      })
    }
  );
}

export function confirmDerivedBlock(
  projectUuid: string,
  blockUuid: string,
  projectRevision: number,
  action: "confirm" | "keep_override" | "discard_override" | "reset" = "confirm"
): Promise<{ project_revision: number; block: DerivedBlock }> {
  return request<{ project_revision: number; block: DerivedBlock }>(
    `${reportRoot(projectUuid)}/derived-blocks/${encodeURIComponent(blockUuid)}/confirmation`,
    {
      method: "POST",
      body: JSON.stringify({ expected_project_revision: projectRevision, action })
    }
  );
}

export function runDerivedConsistencyCheck(
  projectUuid: string,
  expectedProjectRevision: number
): Promise<ConsistencyResult> {
  return request<ConsistencyResult>(`${reportRoot(projectUuid)}/consistency-checks`, {
    method: "POST",
    body: JSON.stringify({ expected_project_revision: expectedProjectRevision })
  });
}

export function getLatestDerivedConsistency(projectUuid: string): Promise<ConsistencyResult | null> {
  return request<ConsistencyResult | null>(`${reportRoot(projectUuid)}/consistency-checks/latest`);
}

export function validateReportExport(
  projectUuid: string,
  mode: ReportExportMode
): Promise<ReportExportValidation> {
  return request<ReportExportValidation>(
    `/api/projects/${encodeURIComponent(projectUuid)}/report-validations?mode=${encodeURIComponent(mode)}`,
    { method: "POST" }
  );
}

export function createReportExportJob(
  projectUuid: string,
  payload: {
    mode: ReportExportMode;
    version: string;
    expected_project_revision: number;
    roundtrip_capable?: boolean;
  }
): Promise<ReportExportJob> {
  return request<ReportExportJob>(
    `/api/projects/${encodeURIComponent(projectUuid)}/report-export-jobs`,
    { method: "POST", body: JSON.stringify(payload) }
  );
}

export function getReportExportJob(jobUuid: string): Promise<ReportExportJob> {
  return request<ReportExportJob>(`/api/report-export-jobs/${encodeURIComponent(jobUuid)}`);
}

export function getReportExportIssues(jobUuid: string): Promise<ReportExportIssueCollection> {
  return request<ReportExportIssueCollection>(`/api/report-export-jobs/${encodeURIComponent(jobUuid)}/issues`);
}

export function downloadReportExportDocx(jobUuid: string): Promise<string> {
  return downloadFile(
    `/api/report-export-jobs/${encodeURIComponent(jobUuid)}/docx`,
    `完整报告-${jobUuid}.docx`
  );
}

export function getProjectReportMigrationReview(projectUuid: string): Promise<ReportImportJob> {
  return request<ReportImportJob>(
    `/api/projects/${encodeURIComponent(projectUuid)}/report/migration-review`
  );
}
