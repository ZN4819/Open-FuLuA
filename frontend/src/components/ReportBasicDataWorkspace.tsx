import { FormEvent, useCallback, useEffect, useState, type ReactNode } from "react";

import { ApiError } from "../api/client.ts";
import {
  createCryptoProduct,
  createReportMember,
  createReportOrganization,
  createReportStandard,
  createSpecialIndicator,
  deleteCryptoProduct,
  deleteReportMember,
  deleteReportOrganization,
  deleteReportStandard,
  deleteSpecialIndicator,
  getReportDistribution,
  getReportMetadata,
  getReportPhaseDates,
  getReportSystemProfile,
  listCryptoProducts,
  listReportMembers,
  listReportOrganizations,
  listReportStandards,
  listSpecialIndicators,
  updateCryptoProduct,
  updateReportDistribution,
  updateReportMember,
  updateReportMetadata,
  updateReportOrganization,
  updateReportPhaseDates,
  updateReportStandard,
  updateReportSystemProfile,
  updateSpecialIndicator,
  type CryptoProduct,
  type CryptoProductCollection,
  type CryptoProductInput,
  type ReportDistribution,
  type ReportMember,
  type ReportMemberInput,
  type ReportMetadata,
  type ReportOnsiteRecord,
  type ReportOrganization,
  type ReportOrganizationInput,
  type ReportPhaseDates,
  type ReportPhaseDatesInput,
  type ReportStandard,
  type ReportStandardInput,
  type ReportSystemProfile,
  type ReportSystemProfileInput,
  type ReportTravelRecord,
  type SpecialIndicator,
  type SpecialIndicatorInput
} from "../api/reportClient.ts";

type BasicGroup = "organizations" | "members" | "phases" | "distribution" | "profile" | "products" | "standards";

type BasicEditorProps = {
  projectUuid: string;
  onDirtyChange: (group: BasicGroup, dirty: boolean) => void;
  onChanged: () => void;
};

type ConflictState = { message: string; currentRevision?: number };

export function ReportBasicDataWorkspace({
  projectUuid,
  onDirtyChange,
  onChanged
}: {
  projectUuid: string;
  onDirtyChange: (dirty: boolean) => void;
  onChanged: () => void;
}) {
  const [dirtyGroups, setDirtyGroups] = useState<Set<BasicGroup>>(new Set());
  const reportGroupDirty = useCallback((group: BasicGroup, dirty: boolean) => {
    setDirtyGroups((current) => {
      const next = new Set(current);
      if (dirty) next.add(group);
      else next.delete(group);
      return sameSet(current, next) ? current : next;
    });
  }, []);

  useEffect(() => onDirtyChange(dirtyGroups.size > 0), [dirtyGroups, onDirtyChange]);
  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);

  const props = { projectUuid, onDirtyChange: reportGroupDirty, onChanged };
  return (
    <div className="report-page-stack report-basic-data-workspace">
      <section className="report-page-heading">
        <p className="eyebrow">完整报告权威数据</p>
        <h3>基础数据</h3>
        <p>这里维护复核闸门使用的单位、人员、日期、系统画像和标准等事实。每组数据独立保存，派生字段保持只读。</p>
        {dirtyGroups.size ? <span className="dirty-chip">{dirtyGroups.size} 组数据未保存</span> : <span className="clean-chip">基础数据已保存</span>}
      </section>
      <nav className="report-basic-jump-links" aria-label="基础数据分组">
        <a href="#basic-organizations">单位</a><a href="#basic-members">人员与角色</a><a href="#basic-phases">阶段与现场</a>
        <a href="#basic-distribution">报告分发</a><a href="#basic-profile">系统画像</a><a href="#basic-products">密码产品</a>
        <a href="#basic-standards">标准与特殊指标</a>
      </nav>
      <OrganizationsEditor {...props} />
      <MembersRolesEditor {...props} />
      <PhaseDatesEditor {...props} />
      <DistributionEditor {...props} />
      <SystemProfileEditor {...props} />
      <CryptoProductsEditor {...props} />
      <StandardsIndicatorsEditor {...props} />
    </div>
  );
}

type OrganizationDraft = ReportOrganizationInput & {
  organization_uuid?: string;
  revision?: number;
};

function emptyOrganization(organization_type: "assessed" | "client"): OrganizationDraft {
  return {
    organization_type,
    name: "",
    address: "",
    postal_code: "",
    contact_name: "",
    contact_title: "",
    contact_department: "",
    office_phone: "",
    mobile_phone: "",
    email: "",
    active: true,
    sort_order: organization_type === "assessed" ? 0 : 1
  };
}

function organizationDraft(value: ReportOrganization | undefined, type: "assessed" | "client"): OrganizationDraft {
  return value ? { ...value } : emptyOrganization(type);
}

function organizationPayload(value: OrganizationDraft): ReportOrganizationInput {
  return {
    organization_type: value.organization_type,
    name: value.name.trim(),
    address: value.address.trim(),
    postal_code: value.postal_code.trim(),
    contact_name: value.contact_name.trim(),
    contact_title: value.contact_title.trim(),
    contact_department: value.contact_department.trim(),
    office_phone: value.office_phone.trim(),
    mobile_phone: value.mobile_phone.trim(),
    email: value.email.trim(),
    active: value.active,
    sort_order: value.sort_order
  };
}

function OrganizationsEditor({ projectUuid, onDirtyChange, onChanged }: BasicEditorProps) {
  const [server, setServer] = useState<Record<"assessed" | "client", OrganizationDraft>>({
    assessed: emptyOrganization("assessed"), client: emptyOrganization("client")
  });
  const [drafts, setDrafts] = useState(server);
  const [metadata, setMetadata] = useState<ReportMetadata>();
  const [isLoading, setIsLoading] = useState(true);
  const [savingType, setSavingType] = useState<"assessed" | "client">();
  const [error, setError] = useState<string>();
  const [message, setMessage] = useState<string>();
  const [conflict, setConflict] = useState<ConflictState>();

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(undefined);
    try {
      const [organizations, nextMetadata] = await Promise.all([
        listReportOrganizations(projectUuid), getReportMetadata(projectUuid)
      ]);
      const next = {
        assessed: organizationDraft(organizations.find((item) => item.organization_type === "assessed"), "assessed"),
        client: organizationDraft(organizations.find((item) => item.organization_type === "client"), "client")
      };
      setServer(next);
      setDrafts(next);
      setMetadata(nextMetadata);
      setConflict(undefined);
    } catch (loadError) {
      setError(errorMessage(loadError, "读取单位数据失败"));
    } finally {
      setIsLoading(false);
    }
  }, [projectUuid]);

  useEffect(() => { void load(); }, [load]);
  const dirty = !sameData(server, drafts);
  useEffect(() => onDirtyChange("organizations", dirty), [dirty, onDirtyChange]);

  function updateDraft(type: "assessed" | "client", field: keyof OrganizationDraft, value: string | boolean | number) {
    setDrafts((current) => ({ ...current, [type]: { ...current[type], [field]: value } }));
    setMessage(undefined);
  }

  async function save(type: "assessed" | "client") {
    const value = drafts[type];
    if (type === "assessed" && !value.name.trim()) {
      setError("被测单位名称不能为空。");
      return;
    }
    setSavingType(type);
    setError(undefined);
    setMessage(undefined);
    setConflict(undefined);
    try {
      if (type === "client" && !value.name.trim()) {
        if (value.organization_uuid && typeof value.revision === "number") {
          await deleteReportOrganization(projectUuid, { organization_uuid: value.organization_uuid, revision: value.revision });
        }
        const empty = emptyOrganization("client");
        setServer((current) => ({ ...current, client: empty }));
        setDrafts((current) => ({ ...current, client: empty }));
      } else {
        const saved = value.organization_uuid && typeof value.revision === "number"
          ? await updateReportOrganization(projectUuid, { ...organizationPayload(value), organization_uuid: value.organization_uuid, revision: value.revision })
          : await createReportOrganization(projectUuid, organizationPayload(value));
        const next = organizationDraft(saved, type);
        setServer((current) => ({ ...current, [type]: next }));
        setDrafts((current) => ({ ...current, [type]: next }));
      }
      setMetadata(await getReportMetadata(projectUuid));
      setMessage(type === "assessed" ? "被测单位已保存。" : value.name.trim() ? "委托单位已保存。" : "已按无独立委托单位处理。");
      onChanged();
    } catch (saveError) {
      handleSaveError(saveError, setConflict, setError, "保存单位失败");
    } finally {
      setSavingType(undefined);
    }
  }

  return (
    <section className="report-form-card report-basic-group" id="basic-organizations" aria-labelledby="basic-organizations-heading">
      <GroupHeading eyebrow="报告身份" title="被测单位与委托单位" headingId="basic-organizations-heading" dirty={dirty} />
      <p className="report-form-help">没有独立委托单位时将委托单位名称留空；后端会把被测单位派生为有效委托单位。</p>
      <Feedback error={error} message={message} conflict={conflict} onReload={load} />
      {isLoading ? <p className="report-loading">正在读取单位...</p> : (
        <div className="report-organization-grid">
          {(["assessed", "client"] as const).map((type) => (
            <fieldset key={type} className="report-subform-card">
              <legend>{type === "assessed" ? "被测单位（必填）" : "独立委托单位（选填）"}</legend>
              <label><span>单位名称</span><input value={drafts[type].name} onChange={(event) => updateDraft(type, "name", event.target.value)} maxLength={200} /></label>
              <label><span>通信地址</span><input value={drafts[type].address} onChange={(event) => updateDraft(type, "address", event.target.value)} maxLength={500} /></label>
              <label><span>邮政编码</span><input value={drafts[type].postal_code} onChange={(event) => updateDraft(type, "postal_code", event.target.value)} maxLength={20} /></label>
              <div className="report-field-row report-field-row-three">
                <label><span>联系人</span><input value={drafts[type].contact_name} onChange={(event) => updateDraft(type, "contact_name", event.target.value)} /></label>
                <label><span>职务/职称</span><input value={drafts[type].contact_title} onChange={(event) => updateDraft(type, "contact_title", event.target.value)} /></label>
                <label><span>所属部门</span><input value={drafts[type].contact_department} onChange={(event) => updateDraft(type, "contact_department", event.target.value)} /></label>
                <label><span>办公电话</span><input value={drafts[type].office_phone} onChange={(event) => updateDraft(type, "office_phone", event.target.value)} /></label>
                <label><span>移动电话</span><input value={drafts[type].mobile_phone} onChange={(event) => updateDraft(type, "mobile_phone", event.target.value)} /></label>
                <label><span>电子邮件</span><input type="email" value={drafts[type].email} onChange={(event) => updateDraft(type, "email", event.target.value)} /></label>
              </div>
              <button type="button" onClick={() => void save(type)} disabled={savingType === type || sameData(server[type], drafts[type])}>
                {savingType === type ? "保存中..." : type === "client" && !drafts.client.name.trim() && drafts.client.organization_uuid ? "清除委托单位" : "保存单位"}
              </button>
            </fieldset>
          ))}
          <aside className="report-derived-card" aria-label="有效委托单位派生结果">
            <span>有效委托单位（只读派生）</span>
            <strong>{metadata?.effective_client_organization_name || "尚未形成"}</strong>
            <small>{metadata?.client_organization_uuid ? "来源：独立委托单位" : "来源：被测单位回退"}</small>
          </aside>
        </div>
      )}
    </section>
  );
}

function emptyMember(): ReportMemberInput {
  return {
    organization_uuid: null,
    name: "",
    team_role: "member",
    is_leader: false,
    qualification_passed_at: null,
    title: "",
    department: "",
    certificate_no: "",
    office_phone: "",
    mobile_phone: "",
    email: "",
    active: true,
    sort_order: 0
  };
}

function memberInput(value: ReportMember | ReportMemberInput): ReportMemberInput {
  return {
    organization_uuid: value.organization_uuid || null,
    name: value.name.trim(),
    team_role: value.team_role,
    is_leader: value.team_role === "leader",
    qualification_passed_at: value.qualification_passed_at || null,
    title: value.title.trim(),
    department: value.department.trim(),
    certificate_no: value.certificate_no.trim(),
    office_phone: value.office_phone.trim(),
    mobile_phone: value.mobile_phone.trim(),
    email: value.email.trim(),
    active: value.active,
    sort_order: value.sort_order
  };
}

function MembersRolesEditor({ projectUuid, onDirtyChange, onChanged }: BasicEditorProps) {
  const [members, setMembers] = useState<ReportMember[]>([]);
  const [drafts, setDrafts] = useState<ReportMember[]>([]);
  const [organizations, setOrganizations] = useState<ReportOrganization[]>([]);
  const [metadata, setMetadata] = useState<ReportMetadata>();
  const [roles, setRoles] = useState({ compiler: "", reviewer: "", approver: "" });
  const [newMember, setNewMember] = useState<ReportMemberInput>(emptyMember());
  const [isLoading, setIsLoading] = useState(true);
  const [savingId, setSavingId] = useState<string>();
  const [isSavingRoles, setIsSavingRoles] = useState(false);
  const [error, setError] = useState<string>();
  const [message, setMessage] = useState<string>();
  const [conflict, setConflict] = useState<ConflictState>();

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(undefined);
    try {
      const [nextMembers, nextOrganizations, nextMetadata] = await Promise.all([
        listReportMembers(projectUuid), listReportOrganizations(projectUuid), getReportMetadata(projectUuid)
      ]);
      setMembers(nextMembers);
      setDrafts(nextMembers.map((item) => ({ ...item })));
      setOrganizations(nextOrganizations);
      setMetadata(nextMetadata);
      setRoles({
        compiler: nextMetadata.compiler_member_uuid ?? "",
        reviewer: nextMetadata.reviewer_member_uuid ?? "",
        approver: nextMetadata.approver_member_uuid ?? ""
      });
      setNewMember(emptyMember());
      setConflict(undefined);
    } catch (loadError) {
      setError(errorMessage(loadError, "读取项目成员失败"));
    } finally {
      setIsLoading(false);
    }
  }, [projectUuid]);

  useEffect(() => { void load(); }, [load]);
  const memberDraftDirty = drafts.some((draft) => !sameData(draft, members.find((item) => item.member_uuid === draft.member_uuid)));
  const newMemberDirty = Boolean(
    newMember.name.trim() || newMember.qualification_passed_at || newMember.title.trim()
    || newMember.department.trim() || newMember.certificate_no.trim() || newMember.organization_uuid
    || newMember.office_phone.trim() || newMember.mobile_phone.trim() || newMember.email.trim()
  );
  const rolesDirty = Boolean(metadata) && (
    roles.compiler !== (metadata?.compiler_member_uuid ?? "")
    || roles.reviewer !== (metadata?.reviewer_member_uuid ?? "")
    || roles.approver !== (metadata?.approver_member_uuid ?? "")
  );
  const dirty = memberDraftDirty || newMemberDirty || rolesDirty;
  useEffect(() => onDirtyChange("members", dirty), [dirty, onDirtyChange]);

  function updateDraft(memberUuid: string, field: keyof ReportMember, value: string | boolean | number | null) {
    setDrafts((current) => current.map((item) => item.member_uuid === memberUuid
      ? { ...item, [field]: value, ...(field === "team_role" ? { is_leader: value === "leader" } : {}) }
      : item));
    setMessage(undefined);
  }

  function updateNew(field: keyof ReportMemberInput, value: string | boolean | number | null) {
    setNewMember((current) => ({ ...current, [field]: value, ...(field === "team_role" ? { is_leader: value === "leader" } : {}) }));
    setMessage(undefined);
  }

  async function saveMember(memberUuid: string) {
    const draft = drafts.find((item) => item.member_uuid === memberUuid);
    if (!draft) return;
    setSavingId(memberUuid);
    setError(undefined);
    setMessage(undefined);
    setConflict(undefined);
    try {
      const saved = await updateReportMember(projectUuid, { ...draft, ...memberInput(draft) });
      setMembers((current) => current.map((item) => item.member_uuid === memberUuid ? saved : item));
      setDrafts((current) => current.map((item) => item.member_uuid === memberUuid ? saved : item));
      setMessage(`成员“${saved.name}”已保存。`);
      onChanged();
    } catch (saveError) {
      handleSaveError(saveError, setConflict, setError, "保存成员失败");
    } finally {
      setSavingId(undefined);
    }
  }

  async function createMember(event: FormEvent) {
    event.preventDefault();
    if (!newMember.name.trim()) return;
    setSavingId("new");
    setError(undefined);
    setMessage(undefined);
    try {
      const saved = await createReportMember(projectUuid, memberInput(newMember));
      setMembers((current) => [...current, saved]);
      setDrafts((current) => [...current, saved]);
      setNewMember(emptyMember());
      setMessage(`成员“${saved.name}”已新增。`);
      onChanged();
    } catch (saveError) {
      setError(errorMessage(saveError, "新增成员失败"));
    } finally {
      setSavingId(undefined);
    }
  }

  async function removeMember(member: ReportMember) {
    if (!window.confirm(`确定删除项目成员“${member.name}”吗？如仍被编审角色引用，后端会拒绝删除。`)) return;
    setSavingId(member.member_uuid);
    setError(undefined);
    try {
      await deleteReportMember(projectUuid, member);
      setMembers((current) => current.filter((item) => item.member_uuid !== member.member_uuid));
      setDrafts((current) => current.filter((item) => item.member_uuid !== member.member_uuid));
      setMessage(`成员“${member.name}”已删除。`);
      onChanged();
    } catch (saveError) {
      setError(errorMessage(saveError, "删除成员失败"));
    } finally {
      setSavingId(undefined);
    }
  }

  async function saveRoles() {
    if (!metadata) return;
    setIsSavingRoles(true);
    setError(undefined);
    setConflict(undefined);
    try {
      const saved = await updateReportMetadata(projectUuid, metadata.revision, {
        compiler_member_uuid: roles.compiler || null,
        reviewer_member_uuid: roles.reviewer || null,
        approver_member_uuid: roles.approver || null
      });
      setMetadata(saved);
      setRoles({
        compiler: saved.compiler_member_uuid ?? "", reviewer: saved.reviewer_member_uuid ?? "", approver: saved.approver_member_uuid ?? ""
      });
      setMessage("编制、审核和批准角色已保存。");
      onChanged();
    } catch (saveError) {
      handleSaveError(saveError, setConflict, setError, "保存编审角色失败");
    } finally {
      setIsSavingRoles(false);
    }
  }

  const activeQualifiedMembers = members.filter((member) => member.active && member.qualification_passed_at);
  const compilerCandidates = activeQualifiedMembers.filter((member) => member.team_role === "member");

  return (
    <section className="report-form-card report-basic-group" id="basic-members" aria-labelledby="basic-members-heading">
      <GroupHeading eyebrow="项目组" title="项目成员与编审角色" headingId="basic-members-heading" dirty={dirty} />
      <p className="report-form-help">进入复核前至少需要两名已填写考核通过日期的成员；编制人必须是组员，三个编审角色不得由同一人兼任。</p>
      <Feedback error={error} message={message} conflict={conflict} onReload={load} />
      {isLoading ? <p className="report-loading">正在读取项目成员...</p> : (
        <>
          <div className="report-repeat-list">
            {drafts.map((member) => {
              const saved = members.find((item) => item.member_uuid === member.member_uuid);
              const rowDirty = !sameData(member, saved);
              return (
                <fieldset className="report-repeat-card" key={member.member_uuid}>
                  <legend>{member.name || "未命名成员"}</legend>
                  <div className="report-field-row report-field-row-three">
                    <label><span>姓名</span><input value={member.name} onChange={(event) => updateDraft(member.member_uuid, "name", event.target.value)} required /></label>
                    <label><span>项目角色</span><select value={member.team_role} onChange={(event) => updateDraft(member.member_uuid, "team_role", event.target.value)}><option value="member">组员</option><option value="leader">项目负责人</option></select></label>
                    <label><span>考核通过日期</span><input type="date" value={dateValue(member.qualification_passed_at)} onChange={(event) => updateDraft(member.member_uuid, "qualification_passed_at", event.target.value || null)} /></label>
                  </div>
                  <div className="report-field-row report-field-row-three">
                    <label><span>所属单位</span><select value={member.organization_uuid ?? ""} onChange={(event) => updateDraft(member.member_uuid, "organization_uuid", event.target.value || null)}><option value="">未指定</option>{organizations.filter((item) => item.name.trim()).map((item) => <option key={item.organization_uuid} value={item.organization_uuid}>{item.name}</option>)}</select></label>
                    <label><span>职务/职称</span><input value={member.title} onChange={(event) => updateDraft(member.member_uuid, "title", event.target.value)} /></label>
                    <label><span>所属部门</span><input value={member.department} onChange={(event) => updateDraft(member.member_uuid, "department", event.target.value)} /></label>
                  </div>
                  <div className="report-field-row report-field-row-three">
                    <label><span>密评人员证书编号</span><input value={member.certificate_no} onChange={(event) => updateDraft(member.member_uuid, "certificate_no", event.target.value)} /></label>
                    <label><span>办公电话</span><input value={member.office_phone} onChange={(event) => updateDraft(member.member_uuid, "office_phone", event.target.value)} /></label>
                    <label><span>移动电话</span><input value={member.mobile_phone} onChange={(event) => updateDraft(member.member_uuid, "mobile_phone", event.target.value)} /></label>
                    <label><span>电子邮件</span><input type="email" value={member.email} onChange={(event) => updateDraft(member.member_uuid, "email", event.target.value)} /></label>
                  </div>
                  <div className="report-inline-actions">
                    <button type="button" onClick={() => void saveMember(member.member_uuid)} disabled={!rowDirty || savingId === member.member_uuid}>{savingId === member.member_uuid ? "保存中..." : "保存成员"}</button>
                    <button type="button" className="danger-button" onClick={() => void removeMember(member)} disabled={savingId === member.member_uuid}>删除</button>
                  </div>
                </fieldset>
              );
            })}
          </div>
          <form className="report-repeat-card report-new-entity" onSubmit={createMember}>
            <h5>新增项目成员</h5>
            <div className="report-field-row report-field-row-three">
              <label><span>姓名</span><input value={newMember.name} onChange={(event) => updateNew("name", event.target.value)} required /></label>
              <label><span>项目角色</span><select value={newMember.team_role} onChange={(event) => updateNew("team_role", event.target.value)}><option value="member">组员</option><option value="leader">项目负责人</option></select></label>
              <label><span>考核通过日期</span><input type="date" value={dateValue(newMember.qualification_passed_at)} onChange={(event) => updateNew("qualification_passed_at", event.target.value || null)} /></label>
            </div>
            <div className="report-field-row report-field-row-three">
              <label><span>所属单位</span><select value={newMember.organization_uuid ?? ""} onChange={(event) => updateNew("organization_uuid", event.target.value || null)}><option value="">未指定</option>{organizations.filter((item) => item.name.trim()).map((item) => <option key={item.organization_uuid} value={item.organization_uuid}>{item.name}</option>)}</select></label>
              <label><span>职务/职称</span><input value={newMember.title} onChange={(event) => updateNew("title", event.target.value)} /></label>
              <label><span>所属部门</span><input value={newMember.department} onChange={(event) => updateNew("department", event.target.value)} /></label>
            </div>
            <div className="report-field-row report-field-row-three">
              <label><span>密评人员证书编号</span><input value={newMember.certificate_no} onChange={(event) => updateNew("certificate_no", event.target.value)} /></label>
              <label><span>办公电话</span><input value={newMember.office_phone} onChange={(event) => updateNew("office_phone", event.target.value)} /></label>
              <label><span>移动电话</span><input value={newMember.mobile_phone} onChange={(event) => updateNew("mobile_phone", event.target.value)} /></label>
              <label><span>电子邮件</span><input type="email" value={newMember.email} onChange={(event) => updateNew("email", event.target.value)} /></label>
            </div>
            <button type="submit" disabled={!newMember.name.trim() || savingId === "new"}>{savingId === "new" ? "新增中..." : "新增成员"}</button>
          </form>
          <fieldset className="report-subform-card report-role-card">
            <legend>编制、审核和批准</legend>
            <div className="report-field-row report-field-row-three">
              <MemberSelect label="编制人（组员 / 密评报告编制人）" value={roles.compiler} members={compilerCandidates} onChange={(value) => setRoles((current) => ({ ...current, compiler: value }))} allowEmpty={false} />
              <MemberSelect label="审核人（正式导出可空）" value={roles.reviewer} members={activeQualifiedMembers} onChange={(value) => setRoles((current) => ({ ...current, reviewer: value }))} />
              <MemberSelect label="批准人（正式导出可空）" value={roles.approver} members={activeQualifiedMembers} onChange={(value) => setRoles((current) => ({ ...current, approver: value }))} />
            </div>
            <button type="button" onClick={() => void saveRoles()} disabled={!rolesDirty || isSavingRoles}>{isSavingRoles ? "保存中..." : "保存编审角色"}</button>
          </fieldset>
        </>
      )}
    </section>
  );
}

function MemberSelect({ label, value, members, onChange, allowEmpty = true }: {
  label: string; value: string; members: ReportMember[]; onChange: (value: string) => void; allowEmpty?: boolean;
}) {
  return (
    <label><span>{label}</span><select value={value} onChange={(event) => onChange(event.target.value)}>
      <option value="">{allowEmpty ? "未指定" : "请选择"}</option>
      {members.map((member) => <option key={member.member_uuid} value={member.member_uuid}>{member.name}</option>)}
    </select></label>
  );
}

function phaseInput(value: ReportPhaseDates): ReportPhaseDatesInput {
  return {
    preparation_start: value.preparation_start || null,
    preparation_end: value.preparation_end || null,
    plan_start: value.plan_start || null,
    plan_end: value.plan_end || null,
    onsite_start: value.onsite_start || null,
    onsite_end: value.onsite_end || null,
    report_start: value.report_start || null,
    report_end: value.report_end || null,
    travel_records: value.travel_records.map((record) => ({ ...record, member_uuids: [...record.member_uuids] })),
    onsite_records: value.onsite_records.map((record) => ({ ...record, member_uuids: [...record.member_uuids] })),
    plan_review_date: value.plan_review_date || null,
    report_review_date: value.report_review_date || null,
    approval_date: value.approval_date || null
  };
}

function deriveOnsitePeriod(value: ReportPhaseDatesInput): ReportPhaseDatesInput {
  if (!value.onsite_records.length) return value;
  const entries = value.onsite_records.map((record) => record.entry_date).filter(Boolean).sort();
  const exits = value.onsite_records.map((record) => record.exit_date).filter(Boolean).sort();
  return {
    ...value,
    onsite_start: entries.length === value.onsite_records.length ? entries[0] : null,
    onsite_end: exits.length === value.onsite_records.length ? exits[exits.length - 1] : null
  };
}

function PhaseDatesEditor({ projectUuid, onDirtyChange, onChanged }: BasicEditorProps) {
  const [server, setServer] = useState<ReportPhaseDates>();
  const [draft, setDraft] = useState<ReportPhaseDatesInput>();
  const [members, setMembers] = useState<ReportMember[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string>();
  const [message, setMessage] = useState<string>();
  const [conflict, setConflict] = useState<ConflictState>();

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(undefined);
    try {
      const [nextPhase, nextMembers] = await Promise.all([getReportPhaseDates(projectUuid), listReportMembers(projectUuid)]);
      setServer(nextPhase);
      setDraft(phaseInput(nextPhase));
      setMembers(nextMembers.filter((member) => member.active));
      setConflict(undefined);
    } catch (loadError) {
      setError(errorMessage(loadError, "读取阶段日期失败"));
    } finally {
      setIsLoading(false);
    }
  }, [projectUuid]);

  useEffect(() => { void load(); }, [load]);
  const dirty = Boolean(server && draft && !sameData(phaseInput(server), draft));
  useEffect(() => onDirtyChange("phases", dirty), [dirty, onDirtyChange]);

  function updateField(field: keyof ReportPhaseDatesInput, value: string | null) {
    setDraft((current) => current ? { ...current, [field]: value } : current);
    setMessage(undefined);
  }

  function updateOnsite(index: number, nextRecord: ReportOnsiteRecord) {
    setDraft((current) => {
      if (!current) return current;
      const records = current.onsite_records.map((record, recordIndex) => recordIndex === index ? nextRecord : record);
      return deriveOnsitePeriod({ ...current, onsite_records: records });
    });
    setMessage(undefined);
  }

  function updateTravel(index: number, nextRecord: ReportTravelRecord) {
    setDraft((current) => current ? {
      ...current,
      travel_records: current.travel_records.map((record, recordIndex) => recordIndex === index ? nextRecord : record)
    } : current);
    setMessage(undefined);
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!server || !draft) return;
    setIsSaving(true);
    setError(undefined);
    setMessage(undefined);
    setConflict(undefined);
    try {
      const saved = await updateReportPhaseDates(projectUuid, server.revision, draft);
      setServer(saved);
      setDraft(phaseInput(saved));
      setMessage("测评阶段、差旅和进离场记录已保存。");
      onChanged();
    } catch (saveError) {
      handleSaveError(saveError, setConflict, setError, "保存阶段日期失败");
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <section className="report-form-card report-basic-group" id="basic-phases" aria-labelledby="basic-phases-heading">
      <GroupHeading eyebrow="日期同源" title="四阶段日期、差旅与进离场" headingId="basic-phases-heading" dirty={dirty} />
      <p className="report-form-help">测评开始日期取准备阶段开始日期；测评结束、编制、首页报告和声明日期均取报告编制阶段结束日期。现场记录存在时，现场阶段自动取最早进场和最晚离场。</p>
      <Feedback error={error} message={message} conflict={conflict} onReload={load} />
      {isLoading || !draft ? <p className="report-loading">正在读取阶段日期...</p> : (
        <form onSubmit={save} className="report-basic-form report-phase-form">
          <div className="report-phase-grid">
            <DateRange title="测评准备阶段" start={draft.preparation_start} end={draft.preparation_end} onStart={(value) => updateField("preparation_start", value)} onEnd={(value) => updateField("preparation_end", value)} />
            <DateRange title="方案编制阶段" start={draft.plan_start} end={draft.plan_end} onStart={(value) => updateField("plan_start", value)} onEnd={(value) => updateField("plan_end", value)} />
            <DateRange title="现场测评阶段" start={draft.onsite_start} end={draft.onsite_end} onStart={(value) => updateField("onsite_start", value)} onEnd={(value) => updateField("onsite_end", value)} readOnly={draft.onsite_records.length > 0} />
            <DateRange title="分析与报告编制阶段" start={draft.report_start} end={draft.report_end} onStart={(value) => updateField("report_start", value)} onEnd={(value) => updateField("report_end", value)} />
          </div>
          <div className="report-derived-strip">
            <span>测评起止：<strong>{draft.preparation_start || "—"} 至 {draft.report_end || "—"}</strong></span>
            <span>编制/报告日期：<strong>{draft.report_end || "—"}</strong></span>
          </div>
          <div className="report-field-row report-field-row-three">
            <label><span>方案评审日期（应早于现场开始）</span><input type="date" value={dateValue(draft.plan_review_date)} onChange={(event) => updateField("plan_review_date", event.target.value || null)} /></label>
            <label><span>报告审核日期（可空）</span><input type="date" value={dateValue(draft.report_review_date)} onChange={(event) => updateField("report_review_date", event.target.value || null)} /></label>
            <label><span>批准日期（可空）</span><input type="date" value={dateValue(draft.approval_date)} onChange={(event) => updateField("approval_date", event.target.value || null)} /></label>
          </div>
          <fieldset className="report-subform-card">
            <legend>B.3 进离场记录</legend>
            {draft.onsite_records.map((record, index) => (
              <div className="report-record-card" key={`onsite-${index}`}>
                <div className="report-field-row">
                  <label><span>进场日期</span><input type="date" value={record.entry_date} onChange={(event) => updateOnsite(index, { ...record, entry_date: event.target.value })} required /></label>
                  <label><span>离场日期</span><input type="date" value={record.exit_date} onChange={(event) => updateOnsite(index, { ...record, exit_date: event.target.value })} required /></label>
                </div>
                <MemberChecklist members={members} selected={record.member_uuids} onChange={(memberUuids) => updateOnsite(index, { ...record, member_uuids: memberUuids })} />
                <button type="button" className="danger-button" onClick={() => setDraft((current) => current ? deriveOnsitePeriod({ ...current, onsite_records: current.onsite_records.filter((_, itemIndex) => itemIndex !== index) }) : current)}>删除本次现场</button>
              </div>
            ))}
            <button type="button" className="secondary-button" onClick={() => setDraft((current) => current ? deriveOnsitePeriod({ ...current, onsite_records: [...current.onsite_records, { entry_date: "", exit_date: "", member_uuids: [] }] }) : current)}>新增进离场记录</button>
          </fieldset>
          <fieldset className="report-subform-card">
            <legend>B.2 差旅记录</legend>
            {draft.travel_records.map((record, index) => (
              <div className="report-record-card" key={`travel-${index}`}>
                <label className="report-checkbox-line"><input type="checkbox" checked={record.local_project} onChange={(event) => updateTravel(index, { ...record, local_project: event.target.checked, start_date: event.target.checked ? null : record.start_date, end_date: event.target.checked ? null : record.end_date })} /><span>本地项目，无差旅（导出时按“/”处理）</span></label>
                <div className="report-field-row">
                  <label><span>差旅开始</span><input type="date" disabled={record.local_project} value={dateValue(record.start_date)} onChange={(event) => updateTravel(index, { ...record, start_date: event.target.value || null })} /></label>
                  <label><span>差旅结束</span><input type="date" disabled={record.local_project} value={dateValue(record.end_date)} onChange={(event) => updateTravel(index, { ...record, end_date: event.target.value || null })} /></label>
                </div>
                <MemberChecklist members={members} selected={record.member_uuids} onChange={(memberUuids) => updateTravel(index, { ...record, member_uuids: memberUuids })} />
                <button type="button" className="danger-button" onClick={() => setDraft((current) => current ? { ...current, travel_records: current.travel_records.filter((_, itemIndex) => itemIndex !== index) } : current)}>删除差旅记录</button>
              </div>
            ))}
            <button type="button" className="secondary-button" onClick={() => setDraft((current) => current ? { ...current, travel_records: [...current.travel_records, { local_project: false, start_date: null, end_date: null, member_uuids: [] }] } : current)}>新增差旅记录</button>
          </fieldset>
          <button type="submit" disabled={!dirty || isSaving}>{isSaving ? "保存中..." : "保存阶段与现场记录"}</button>
        </form>
      )}
    </section>
  );
}

function DateRange({ title, start, end, onStart, onEnd, readOnly = false }: {
  title: string; start?: string | null; end?: string | null; onStart: (value: string | null) => void; onEnd: (value: string | null) => void; readOnly?: boolean;
}) {
  return (
    <fieldset className="report-subform-card"><legend>{title}</legend>
      <label><span>开始日期</span><input type="date" value={dateValue(start)} readOnly={readOnly} onChange={(event) => onStart(event.target.value || null)} /></label>
      <label><span>结束日期</span><input type="date" value={dateValue(end)} readOnly={readOnly} onChange={(event) => onEnd(event.target.value || null)} /></label>
      {readOnly ? <small>由进离场记录自动确定。</small> : null}
    </fieldset>
  );
}

function MemberChecklist({ members, selected, onChange }: { members: ReportMember[]; selected: string[]; onChange: (value: string[]) => void }) {
  return (
    <fieldset className="report-member-checklist"><legend>参与人员（至少一人）</legend>
      {members.length ? members.map((member) => (
        <label key={member.member_uuid}><input type="checkbox" checked={selected.includes(member.member_uuid)} onChange={(event) => onChange(event.target.checked ? [...selected, member.member_uuid] : selected.filter((item) => item !== member.member_uuid))} /><span>{member.name}</span></label>
      )) : <small>请先在“项目成员与编审角色”中新增成员。</small>}
    </fieldset>
  );
}

function DistributionEditor({ projectUuid, onDirtyChange, onChanged }: BasicEditorProps) {
  const [server, setServer] = useState<ReportDistribution>();
  const [draft, setDraft] = useState({ regulator_copies: 0, client_copies: 0, assessment_copies: 0 });
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string>();
  const [message, setMessage] = useState<string>();
  const [conflict, setConflict] = useState<ConflictState>();

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(undefined);
    try {
      const next = await getReportDistribution(projectUuid);
      setServer(next);
      setDraft({ regulator_copies: next.regulator_copies, client_copies: next.client_copies, assessment_copies: next.assessment_copies });
      setConflict(undefined);
    } catch (loadError) {
      setError(errorMessage(loadError, "读取报告分发份数失败"));
    } finally {
      setIsLoading(false);
    }
  }, [projectUuid]);

  useEffect(() => { void load(); }, [load]);
  const dirty = Boolean(server) && !sameData(draft, {
    regulator_copies: server?.regulator_copies,
    client_copies: server?.client_copies,
    assessment_copies: server?.assessment_copies
  });
  useEffect(() => onDirtyChange("distribution", dirty), [dirty, onDirtyChange]);
  const total = draft.regulator_copies + draft.client_copies + draft.assessment_copies;

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!server) return;
    setIsSaving(true);
    setError(undefined);
    setConflict(undefined);
    try {
      const saved = await updateReportDistribution(projectUuid, server.revision, draft);
      setServer(saved);
      setDraft({ regulator_copies: saved.regulator_copies, client_copies: saved.client_copies, assessment_copies: saved.assessment_copies });
      setMessage("报告分发份数已保存。");
      onChanged();
    } catch (saveError) {
      handleSaveError(saveError, setConflict, setError, "保存报告分发份数失败");
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <section className="report-form-card report-basic-group" id="basic-distribution" aria-labelledby="basic-distribution-heading">
      <GroupHeading eyebrow="报告分发" title="分发份数" headingId="basic-distribution-heading" dirty={dirty} />
      <p className="report-form-help">总份数由监管部门、有效委托单位和密评机构留存份数相加得出，必须大于 0。</p>
      <Feedback error={error} message={message} conflict={conflict} onReload={load} />
      {isLoading ? <p className="report-loading">正在读取分发份数...</p> : (
        <form className="report-field-row report-distribution-form" onSubmit={save}>
          <NumberField label="监管部门" value={draft.regulator_copies} onChange={(value) => setDraft((current) => ({ ...current, regulator_copies: value }))} />
          <NumberField label="有效委托单位" value={draft.client_copies} onChange={(value) => setDraft((current) => ({ ...current, client_copies: value }))} />
          <NumberField label="密评机构留存" value={draft.assessment_copies} onChange={(value) => setDraft((current) => ({ ...current, assessment_copies: value }))} />
          <label><span>总份数（只读派生）</span><output>{total}</output></label>
          <button type="submit" disabled={!dirty || isSaving}>{isSaving ? "保存中..." : "保存分发份数"}</button>
        </form>
      )}
    </section>
  );
}

function NumberField({ label, value, onChange }: { label: string; value: number; onChange: (value: number) => void }) {
  return <label><span>{label}</span><input type="number" min={0} max={100} value={value} onChange={(event) => onChange(Math.max(0, Number(event.target.value) || 0))} /></label>;
}

const ALGORITHM_OPTIONS = [
  "SM1", "SM2", "SM3", "SM4", "SM7", "SM9", "AES", "DES", "3DES", "RSA1024", "RSA2048",
  "SHA-1", "SHA-256", "SHA-384", "SHA-512", "ZUC", "MD5"
];

function profileInput(value: ReportSystemProfile): ReportSystemProfileInput {
  return {
    system_name: value.system_name,
    system_summary: value.system_summary,
    critical_infrastructure_status: value.critical_infrastructure_status,
    critical_infrastructure_department: value.critical_infrastructure_department,
    level_filing_status: value.level_filing_status,
    filing_s: value.filing_s,
    filing_a: value.filing_a,
    filing_g: value.filing_g,
    filing_certificate_no: value.filing_certificate_no,
    filing_system_same: value.filing_system_same,
    filing_system_name: value.filing_system_name,
    filing_difference: value.filing_difference,
    level_assessment_status: value.level_assessment_status,
    level_assessment_organization: value.level_assessment_organization,
    level_assessment_date: value.level_assessment_date || null,
    level_assessment_conclusion: value.level_assessment_conclusion,
    cloud_dependency: value.cloud_dependency,
    cloud_platform_name: value.cloud_platform_name,
    cloud_assessment_status: value.cloud_assessment_status,
    cloud_assessment_organization: value.cloud_assessment_organization,
    cloud_assessment_date: value.cloud_assessment_date || null,
    cloud_assessment_conclusion: value.cloud_assessment_conclusion,
    crypto_plan_status: value.crypto_plan_status,
    crypto_plan_passed_at: value.crypto_plan_passed_at || null,
    crypto_plan_assessment_mode: value.crypto_plan_assessment_mode,
    crypto_plan_assessment_organization: value.crypto_plan_assessment_organization,
    operation_status: value.operation_status,
    operation_started_at: value.operation_started_at || null,
    construction_stage: value.construction_stage,
    service_scope: value.service_scope,
    service_scope_count: value.service_scope_count ?? null,
    service_scope_other: value.service_scope_other,
    no_crypto_products: value.no_crypto_products,
    selected_algorithms: [...value.selected_algorithms],
    other_algorithms: [...value.other_algorithms],
    application_catalog: [...value.application_catalog]
  };
}

function SystemProfileEditor({ projectUuid, onDirtyChange, onChanged }: BasicEditorProps) {
  const [server, setServer] = useState<ReportSystemProfile>();
  const [draft, setDraft] = useState<ReportSystemProfileInput>();
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string>();
  const [message, setMessage] = useState<string>();
  const [conflict, setConflict] = useState<ConflictState>();

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(undefined);
    try {
      const next = await getReportSystemProfile(projectUuid);
      setServer(next);
      setDraft(profileInput(next));
      setConflict(undefined);
    } catch (loadError) {
      setError(errorMessage(loadError, "读取系统画像失败"));
    } finally {
      setIsLoading(false);
    }
  }, [projectUuid]);

  useEffect(() => { void load(); }, [load]);
  const dirty = Boolean(server && draft && !sameData(profileInput(server), draft));
  useEffect(() => onDirtyChange("profile", dirty), [dirty, onDirtyChange]);

  function update<K extends keyof ReportSystemProfileInput>(field: K, value: ReportSystemProfileInput[K]) {
    setDraft((current) => current ? { ...current, [field]: value } : current);
    setMessage(undefined);
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!server || !draft) return;
    setIsSaving(true);
    setError(undefined);
    setMessage(undefined);
    setConflict(undefined);
    try {
      const saved = await updateReportSystemProfile(projectUuid, server.revision, draft);
      setServer(saved);
      setDraft(profileInput(saved));
      setMessage("系统画像与受控分支已保存。");
      onChanged();
    } catch (saveError) {
      handleSaveError(saveError, setConflict, setError, "保存系统画像失败");
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <section className="report-form-card report-basic-group" id="basic-profile" aria-labelledby="basic-profile-heading">
      <GroupHeading eyebrow="基本信息表" title="系统画像与受控分支" headingId="basic-profile-heading" dirty={dirty} />
      <p className="report-form-help">所有分支数据都会保留。当前选择与已填内容不一致时由后端产生可确认警告，不在前端自动删除信息。</p>
      <Feedback error={error} message={message} conflict={conflict} onReload={load} />
      {isLoading || !draft ? <p className="report-loading">正在读取系统画像...</p> : (
        <form className="report-profile-form" onSubmit={save}>
          <fieldset className="report-subform-card">
            <legend>系统基本描述</legend>
            <label><span>系统名称（报告权威来源）</span><input value={draft.system_name} onChange={(event) => update("system_name", event.target.value)} maxLength={300} required /></label>
            <label><span>系统简介</span><textarea rows={5} value={draft.system_summary} onChange={(event) => update("system_summary", event.target.value)} maxLength={20000} /></label>
          </fieldset>
          <fieldset className="report-subform-card">
            <legend>关键信息基础设施</legend>
            <div className="report-field-row">
              <label><span>认定情况</span><select value={draft.critical_infrastructure_status} onChange={(event) => update("critical_infrastructure_status", event.target.value as ReportSystemProfileInput["critical_infrastructure_status"])}><option value="">未选择</option><option value="recognized">已认定</option><option value="not_recognized">未认定</option></select></label>
              <label><span>安全保护工作部门</span><input value={draft.critical_infrastructure_department} onChange={(event) => update("critical_infrastructure_department", event.target.value)} /></label>
            </div>
          </fieldset>
          <fieldset className="report-subform-card">
            <legend>网络安全等级保护备案</legend>
            <div className="report-field-row report-field-row-three">
              <label><span>备案状态</span><select value={draft.level_filing_status} onChange={(event) => update("level_filing_status", event.target.value as ReportSystemProfileInput["level_filing_status"])}><option value="">未选择</option><option value="filed">已定级备案</option><option value="not_filed">未定级备案</option></select></label>
              <label><span>S</span><input value={draft.filing_s} onChange={(event) => update("filing_s", event.target.value)} maxLength={80} /></label>
              <label><span>A</span><input value={draft.filing_a} onChange={(event) => update("filing_a", event.target.value)} maxLength={80} /></label>
              <label><span>G（可空）</span><input value={draft.filing_g} onChange={(event) => update("filing_g", event.target.value)} maxLength={80} /></label>
              <label><span>备案证明编号</span><input value={draft.filing_certificate_no} onChange={(event) => update("filing_certificate_no", event.target.value)} /></label>
              <label><span>备案系统名称是否一致</span><select value={draft.filing_system_same === null ? "" : draft.filing_system_same ? "yes" : "no"} onChange={(event) => update("filing_system_same", event.target.value === "" ? null : event.target.value === "yes")}><option value="">未选择</option><option value="yes">一致</option><option value="no">不一致</option></select></label>
            </div>
            <label><span>备案系统名称</span><input value={draft.filing_system_name} onChange={(event) => update("filing_system_name", event.target.value)} /></label>
            <label><span>差异说明</span><textarea rows={3} value={draft.filing_difference} onChange={(event) => update("filing_difference", event.target.value)} /></label>
          </fieldset>
          <fieldset className="report-subform-card">
            <legend>网络安全等级保护测评</legend>
            <div className="report-field-row report-field-row-three">
              <label><span>测评状态</span><select value={draft.level_assessment_status} onChange={(event) => update("level_assessment_status", event.target.value as ReportSystemProfileInput["level_assessment_status"])}><option value="">未选择</option><option value="assessed">已测评</option><option value="assessing">正在测评</option><option value="not_assessed">未测评</option></select></label>
              <label><span>测评机构</span><input value={draft.level_assessment_organization} onChange={(event) => update("level_assessment_organization", event.target.value)} /></label>
              <label><span>测评日期</span><input type="date" value={dateValue(draft.level_assessment_date)} onChange={(event) => update("level_assessment_date", event.target.value || null)} /></label>
            </div>
            <label><span>测评结论</span><textarea rows={2} value={draft.level_assessment_conclusion} onChange={(event) => update("level_assessment_conclusion", event.target.value)} /></label>
          </fieldset>
          <fieldset className="report-subform-card">
            <legend>云平台依赖</legend>
            <div className="report-field-row report-field-row-three">
              <label><span>是否依赖云平台</span><select value={draft.cloud_dependency} onChange={(event) => update("cloud_dependency", event.target.value as ReportSystemProfileInput["cloud_dependency"])}><option value="">未选择</option><option value="yes">是</option><option value="no">否</option></select></label>
              <label><span>云平台名称</span><input value={draft.cloud_platform_name} onChange={(event) => update("cloud_platform_name", event.target.value)} /></label>
              <label><span>云平台测评状态</span><select value={draft.cloud_assessment_status} onChange={(event) => update("cloud_assessment_status", event.target.value as ReportSystemProfileInput["cloud_assessment_status"])}><option value="">未选择</option><option value="assessed">已测评</option><option value="assessing">正在测评</option><option value="not_assessed">未测评</option></select></label>
              <label><span>云平台测评机构</span><input value={draft.cloud_assessment_organization} onChange={(event) => update("cloud_assessment_organization", event.target.value)} /></label>
              <label><span>云平台测评日期</span><input type="date" value={dateValue(draft.cloud_assessment_date)} onChange={(event) => update("cloud_assessment_date", event.target.value || null)} /></label>
            </div>
            <label><span>云平台测评结论</span><textarea rows={2} value={draft.cloud_assessment_conclusion} onChange={(event) => update("cloud_assessment_conclusion", event.target.value)} /></label>
          </fieldset>
          <fieldset className="report-subform-card">
            <legend>密码应用方案</legend>
            <div className="report-field-row report-field-row-three">
              <label><span>方案状态</span><select value={draft.crypto_plan_status} onChange={(event) => update("crypto_plan_status", event.target.value as ReportSystemProfileInput["crypto_plan_status"])}><option value="">未选择</option><option value="passed">已通过密评</option><option value="not_passed">有方案但未通过</option><option value="none">无方案</option></select></label>
              <label><span>通过日期</span><input type="date" value={dateValue(draft.crypto_plan_passed_at)} onChange={(event) => update("crypto_plan_passed_at", event.target.value || null)} /></label>
              <label><span>评估方式</span><select value={draft.crypto_plan_assessment_mode} onChange={(event) => update("crypto_plan_assessment_mode", event.target.value as ReportSystemProfileInput["crypto_plan_assessment_mode"])}><option value="">未选择</option><option value="self">自行评估</option><option value="commissioned">委托密评机构</option></select></label>
            </div>
            <label><span>受托评估机构</span><input value={draft.crypto_plan_assessment_organization} onChange={(event) => update("crypto_plan_assessment_organization", event.target.value)} /></label>
          </fieldset>
          <fieldset className="report-subform-card">
            <legend>运行状态与服务范围</legend>
            <div className="report-field-row report-field-row-three">
              <label><span>运行状态</span><select value={draft.operation_status} onChange={(event) => update("operation_status", event.target.value as ReportSystemProfileInput["operation_status"])}><option value="">未选择</option><option value="running">已投入运行</option><option value="not_running">未投入运行</option></select></label>
              <label><span>投入运行年月</span><input type="month" value={dateValue(draft.operation_started_at, 7)} onChange={(event) => update("operation_started_at", event.target.value || null)} /></label>
              <label><span>当前建设阶段</span><input value={draft.construction_stage} onChange={(event) => update("construction_stage", event.target.value)} /></label>
              <label><span>服务范围</span><select value={draft.service_scope} onChange={(event) => update("service_scope", event.target.value as ReportSystemProfileInput["service_scope"])}><option value="">未选择</option><option value="national">全国</option><option value="cross_province">跨省</option><option value="province">全省</option><option value="cross_city">跨市</option><option value="local">本地</option><option value="other">其他</option></select></label>
              <label><span>跨域数量</span><input type="number" min={1} value={draft.service_scope_count ?? ""} onChange={(event) => update("service_scope_count", event.target.value ? Number(event.target.value) : null)} /></label>
              <label><span>其他范围说明</span><input value={draft.service_scope_other} onChange={(event) => update("service_scope_other", event.target.value)} /></label>
            </div>
          </fieldset>
          <fieldset className="report-subform-card">
            <legend>密码算法与应用目录</legend>
            <label className="report-checkbox-line"><input type="checkbox" checked={draft.no_crypto_products} onChange={(event) => update("no_crypto_products", event.target.checked)} /><span>系统未使用密码产品</span></label>
            <div className="report-algorithm-grid" aria-label="密码算法人工勾选">
              {ALGORITHM_OPTIONS.map((algorithm) => <label key={algorithm}><input type="checkbox" checked={draft.selected_algorithms.includes(algorithm)} onChange={(event) => update("selected_algorithms", event.target.checked ? [...draft.selected_algorithms, algorithm] : draft.selected_algorithms.filter((item) => item !== algorithm))} /><span>{algorithm}</span></label>)}
            </div>
            <label><span>其他算法（每行一项）</span><textarea rows={3} value={draft.other_algorithms.join("\n")} onChange={(event) => update("other_algorithms", lines(event.target.value))} /></label>
            <label><span>表 2-7 应用名称目录（每行一项）</span><textarea rows={4} value={draft.application_catalog.join("\n")} onChange={(event) => update("application_catalog", lines(event.target.value))} /></label>
          </fieldset>
          <button type="submit" disabled={!dirty || isSaving}>{isSaving ? "保存中..." : "保存系统画像"}</button>
        </form>
      )}
    </section>
  );
}

function emptyProduct(): CryptoProductInput {
  return {
    name: "",
    model: "",
    manufacturer: "",
    certificate_no: "",
    quantity_text: "",
    use_mode: "exclusive",
    classification: "certified",
    sort_order: 0
  };
}

function productInput(value: CryptoProduct | CryptoProductInput): CryptoProductInput {
  return {
    name: value.name.trim(),
    model: value.model.trim(),
    manufacturer: value.manufacturer.trim(),
    certificate_no: value.certificate_no.trim(),
    quantity_text: value.quantity_text.trim(),
    use_mode: value.use_mode,
    classification: value.classification,
    sort_order: value.sort_order
  };
}

function CryptoProductsEditor({ projectUuid, onDirtyChange, onChanged }: BasicEditorProps) {
  const [products, setProducts] = useState<CryptoProduct[]>([]);
  const [drafts, setDrafts] = useState<CryptoProduct[]>([]);
  const [summary, setSummary] = useState<CryptoProductCollection["summary"]>({ total: 0, exclusive: 0, shared: 0, certified: 0, uncertified_domestic: 0, foreign: 0 });
  const [newProduct, setNewProduct] = useState<CryptoProductInput>(emptyProduct());
  const [isLoading, setIsLoading] = useState(true);
  const [savingId, setSavingId] = useState<string>();
  const [error, setError] = useState<string>();
  const [message, setMessage] = useState<string>();
  const [conflict, setConflict] = useState<ConflictState>();

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(undefined);
    try {
      const collection = await listCryptoProducts(projectUuid);
      setProducts(collection.items);
      setDrafts(collection.items.map((item) => ({ ...item })));
      setSummary(collection.summary);
      setNewProduct(emptyProduct());
      setConflict(undefined);
    } catch (loadError) {
      setError(errorMessage(loadError, "读取密码产品失败"));
    } finally {
      setIsLoading(false);
    }
  }, [projectUuid]);

  useEffect(() => { void load(); }, [load]);
  const rowsDirty = drafts.some((draft) => !sameData(draft, products.find((item) => item.product_uuid === draft.product_uuid)));
  const newDirty = Boolean(newProduct.name.trim() || newProduct.model.trim() || newProduct.manufacturer.trim() || newProduct.certificate_no.trim() || newProduct.quantity_text.trim());
  const dirty = rowsDirty || newDirty;
  useEffect(() => onDirtyChange("products", dirty), [dirty, onDirtyChange]);

  function updateDraft(productUuid: string, field: keyof CryptoProduct, value: string | number) {
    setDrafts((current) => current.map((item) => item.product_uuid === productUuid ? { ...item, [field]: value } : item));
    setMessage(undefined);
  }

  function updateNew<K extends keyof CryptoProductInput>(field: K, value: CryptoProductInput[K]) {
    setNewProduct((current) => ({ ...current, [field]: value }));
    setMessage(undefined);
  }

  async function refreshSummary() {
    const collection = await listCryptoProducts(projectUuid);
    setSummary(collection.summary);
  }

  async function saveProduct(productUuid: string) {
    const draft = drafts.find((item) => item.product_uuid === productUuid);
    if (!draft) return;
    setSavingId(productUuid);
    setError(undefined);
    setConflict(undefined);
    try {
      const saved = await updateCryptoProduct(projectUuid, draft);
      setProducts((current) => current.map((item) => item.product_uuid === productUuid ? saved : item));
      setDrafts((current) => current.map((item) => item.product_uuid === productUuid ? saved : item));
      await refreshSummary();
      setMessage(`密码产品“${saved.name}”已保存。`);
      onChanged();
    } catch (saveError) {
      handleSaveError(saveError, setConflict, setError, "保存密码产品失败");
    } finally {
      setSavingId(undefined);
    }
  }

  async function createProduct(event: FormEvent) {
    event.preventDefault();
    if (!newProduct.name.trim() || !newProduct.quantity_text.trim()) return;
    setSavingId("new");
    setError(undefined);
    try {
      const saved = await createCryptoProduct(projectUuid, productInput(newProduct));
      setProducts((current) => [...current, saved]);
      setDrafts((current) => [...current, saved]);
      setNewProduct(emptyProduct());
      await refreshSummary();
      setMessage(`密码产品“${saved.name}”已新增。`);
      onChanged();
    } catch (saveError) {
      setError(errorMessage(saveError, "新增密码产品失败"));
    } finally {
      setSavingId(undefined);
    }
  }

  async function removeProduct(product: CryptoProduct) {
    if (!window.confirm(`确定删除密码产品“${product.name}”吗？`)) return;
    setSavingId(product.product_uuid);
    setError(undefined);
    try {
      await deleteCryptoProduct(projectUuid, product);
      setProducts((current) => current.filter((item) => item.product_uuid !== product.product_uuid));
      setDrafts((current) => current.filter((item) => item.product_uuid !== product.product_uuid));
      await refreshSummary();
      setMessage(`密码产品“${product.name}”已删除。`);
      onChanged();
    } catch (saveError) {
      setError(errorMessage(saveError, "删除密码产品失败"));
    } finally {
      setSavingId(undefined);
    }
  }

  return (
    <section className="report-form-card report-basic-group" id="basic-products" aria-labelledby="basic-products-heading">
      <GroupHeading eyebrow="表 2-3" title="密码产品" headingId="basic-products-heading" dirty={dirty} />
      <p className="report-form-help">数量仅允许非负整数或“若干”；“若干”在统计中按 1 计，但导出保留原文。</p>
      <Feedback error={error} message={message} conflict={conflict} onReload={load} />
      <div className="report-summary-grid" aria-label="密码产品派生统计">
        <span>总数<strong>{summary.total}</strong></span><span>独享<strong>{summary.exclusive}</strong></span><span>共享<strong>{summary.shared}</strong></span>
        <span>已认证<strong>{summary.certified}</strong></span><span>未认证国产<strong>{summary.uncertified_domestic}</strong></span><span>国外<strong>{summary.foreign}</strong></span>
      </div>
      {isLoading ? <p className="report-loading">正在读取密码产品...</p> : (
        <>
          <div className="report-repeat-list">
            {drafts.map((product) => {
              const rowDirty = !sameData(product, products.find((item) => item.product_uuid === product.product_uuid));
              return <ProductFields key={product.product_uuid} value={product} onChange={(field, value) => updateDraft(product.product_uuid, field, value)} actions={<><button type="button" onClick={() => void saveProduct(product.product_uuid)} disabled={!rowDirty || savingId === product.product_uuid}>{savingId === product.product_uuid ? "保存中..." : "保存产品"}</button><button type="button" className="danger-button" onClick={() => void removeProduct(product)} disabled={savingId === product.product_uuid}>删除</button></>} />;
            })}
          </div>
          <form onSubmit={createProduct}>
            <ProductFields value={newProduct} title="新增密码产品" onChange={updateNew} actions={<button type="submit" disabled={!newProduct.name.trim() || !newProduct.quantity_text.trim() || savingId === "new"}>{savingId === "new" ? "新增中..." : "新增产品"}</button>} />
          </form>
        </>
      )}
    </section>
  );
}

function ProductFields<T extends CryptoProduct | CryptoProductInput>({ value, title, onChange, actions }: {
  value: T;
  title?: string;
  onChange: (field: keyof CryptoProductInput, value: string | number) => void;
  actions: ReactNode;
}) {
  return (
    <fieldset className="report-repeat-card"><legend>{(title ?? value.name) || "未命名产品"}</legend>
      <div className="report-field-row report-field-row-three">
        <label><span>产品名称</span><input value={value.name} onChange={(event) => onChange("name", event.target.value)} required /></label>
        <label><span>型号</span><input value={value.model} onChange={(event) => onChange("model", event.target.value)} /></label>
        <label><span>生产厂商</span><input value={value.manufacturer} onChange={(event) => onChange("manufacturer", event.target.value)} /></label>
        <label><span>证书编号</span><input value={value.certificate_no} onChange={(event) => onChange("certificate_no", event.target.value)} /></label>
        <label><span>数量</span><input value={value.quantity_text} onChange={(event) => onChange("quantity_text", event.target.value)} required /></label>
        <label><span>使用方式</span><select value={value.use_mode} onChange={(event) => onChange("use_mode", event.target.value)}><option value="exclusive">独享</option><option value="shared">共享</option></select></label>
        <label><span>产品分类</span><select value={value.classification} onChange={(event) => onChange("classification", event.target.value)}><option value="certified">已认证</option><option value="uncertified_domestic">未认证国产</option><option value="foreign">国外产品</option></select></label>
      </div>
      <div className="report-inline-actions">{actions}</div>
    </fieldset>
  );
}

function emptyStandard(): ReportStandardInput {
  return { code: "", name: "", source_ref: "", sort_order: 100 };
}

function emptyIndicator(standardUuid = ""): SpecialIndicatorInput {
  return { manual_standard_uuid: standardUuid, indicator_code: "", indicator_name: "", description: "", sort_order: 0 };
}

function StandardsIndicatorsEditor({ projectUuid, onDirtyChange, onChanged }: BasicEditorProps) {
  const [standards, setStandards] = useState<ReportStandard[]>([]);
  const [standardDrafts, setStandardDrafts] = useState<ReportStandard[]>([]);
  const [indicators, setIndicators] = useState<SpecialIndicator[]>([]);
  const [indicatorDrafts, setIndicatorDrafts] = useState<SpecialIndicator[]>([]);
  const [newStandard, setNewStandard] = useState<ReportStandardInput>(emptyStandard());
  const [newIndicator, setNewIndicator] = useState<SpecialIndicatorInput>(emptyIndicator());
  const [isLoading, setIsLoading] = useState(true);
  const [savingId, setSavingId] = useState<string>();
  const [error, setError] = useState<string>();
  const [message, setMessage] = useState<string>();
  const [conflict, setConflict] = useState<ConflictState>();

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(undefined);
    try {
      const [nextStandards, nextIndicators] = await Promise.all([listReportStandards(projectUuid), listSpecialIndicators(projectUuid)]);
      setStandards(nextStandards);
      setStandardDrafts(nextStandards.map((item) => ({ ...item })));
      setIndicators(nextIndicators);
      setIndicatorDrafts(nextIndicators.map((item) => ({ ...item })));
      setNewStandard(emptyStandard());
      const firstManual = nextStandards.find((item) => item.kind === "manual")?.standard_uuid ?? "";
      setNewIndicator(emptyIndicator(firstManual));
      setConflict(undefined);
    } catch (loadError) {
      setError(errorMessage(loadError, "读取标准与特殊指标失败"));
    } finally {
      setIsLoading(false);
    }
  }, [projectUuid]);

  useEffect(() => { void load(); }, [load]);
  const standardRowsDirty = standardDrafts.some((draft) => !sameData(draft, standards.find((item) => item.standard_uuid === draft.standard_uuid)));
  const indicatorRowsDirty = indicatorDrafts.some((draft) => !sameData(draft, indicators.find((item) => item.indicator_uuid === draft.indicator_uuid)));
  const newStandardDirty = Boolean(newStandard.code.trim() || newStandard.name.trim() || newStandard.source_ref.trim());
  const newIndicatorDirty = Boolean(newIndicator.indicator_code.trim() || newIndicator.indicator_name.trim() || newIndicator.description.trim());
  const dirty = standardRowsDirty || indicatorRowsDirty || newStandardDirty || newIndicatorDirty;
  useEffect(() => onDirtyChange("standards", dirty), [dirty, onDirtyChange]);

  function updateStandardDraft(standardUuid: string, field: keyof ReportStandard, value: string | number) {
    setStandardDrafts((current) => current.map((item) => item.standard_uuid === standardUuid ? { ...item, [field]: value } : item));
    setMessage(undefined);
  }

  function updateIndicatorDraft(indicatorUuid: string, field: keyof SpecialIndicator, value: string | number) {
    setIndicatorDrafts((current) => current.map((item) => item.indicator_uuid === indicatorUuid ? { ...item, [field]: value } : item));
    setMessage(undefined);
  }

  async function saveStandard(standardUuid: string) {
    const draft = standardDrafts.find((item) => item.standard_uuid === standardUuid);
    if (!draft || draft.kind !== "manual") return;
    setSavingId(standardUuid);
    setError(undefined);
    setConflict(undefined);
    try {
      const saved = await updateReportStandard(projectUuid, draft);
      setStandards((current) => current.map((item) => item.standard_uuid === standardUuid ? saved : item));
      setStandardDrafts((current) => current.map((item) => item.standard_uuid === standardUuid ? saved : item));
      setMessage(`人工标准“${saved.name}”已保存。`);
      onChanged();
    } catch (saveError) {
      handleSaveError(saveError, setConflict, setError, "保存人工标准失败");
    } finally {
      setSavingId(undefined);
    }
  }

  async function createStandard(event: FormEvent) {
    event.preventDefault();
    if (!newStandard.name.trim()) return;
    setSavingId("new-standard");
    setError(undefined);
    try {
      const saved = await createReportStandard(projectUuid, { ...newStandard, name: newStandard.name.trim() });
      setStandards((current) => [...current, saved]);
      setStandardDrafts((current) => [...current, saved]);
      setNewStandard(emptyStandard());
      if (!newIndicator.manual_standard_uuid) setNewIndicator((current) => ({ ...current, manual_standard_uuid: saved.standard_uuid }));
      setMessage(`人工标准“${saved.name}”已新增。`);
      onChanged();
    } catch (saveError) {
      setError(errorMessage(saveError, "新增人工标准失败"));
    } finally {
      setSavingId(undefined);
    }
  }

  async function removeStandard(standard: ReportStandard) {
    if (!window.confirm(`确定删除人工标准“${standard.name}”吗？被特殊指标引用时后端会拒绝删除。`)) return;
    setSavingId(standard.standard_uuid);
    setError(undefined);
    try {
      await deleteReportStandard(projectUuid, standard);
      setStandards((current) => current.filter((item) => item.standard_uuid !== standard.standard_uuid));
      setStandardDrafts((current) => current.filter((item) => item.standard_uuid !== standard.standard_uuid));
      setMessage(`人工标准“${standard.name}”已删除。`);
      onChanged();
    } catch (saveError) {
      setError(errorMessage(saveError, "删除人工标准失败"));
    } finally {
      setSavingId(undefined);
    }
  }

  async function saveIndicator(indicatorUuid: string) {
    const draft = indicatorDrafts.find((item) => item.indicator_uuid === indicatorUuid);
    if (!draft) return;
    setSavingId(indicatorUuid);
    setError(undefined);
    setConflict(undefined);
    try {
      const saved = await updateSpecialIndicator(projectUuid, draft);
      setIndicators((current) => current.map((item) => item.indicator_uuid === indicatorUuid ? saved : item));
      setIndicatorDrafts((current) => current.map((item) => item.indicator_uuid === indicatorUuid ? saved : item));
      setMessage(`特殊指标“${saved.indicator_name}”已保存。`);
      onChanged();
    } catch (saveError) {
      handleSaveError(saveError, setConflict, setError, "保存特殊指标失败");
    } finally {
      setSavingId(undefined);
    }
  }

  async function createIndicator(event: FormEvent) {
    event.preventDefault();
    if (!newIndicator.manual_standard_uuid || !newIndicator.indicator_name.trim()) return;
    setSavingId("new-indicator");
    setError(undefined);
    try {
      const saved = await createSpecialIndicator(projectUuid, { ...newIndicator, indicator_name: newIndicator.indicator_name.trim() });
      setIndicators((current) => [...current, saved]);
      setIndicatorDrafts((current) => [...current, saved]);
      setNewIndicator(emptyIndicator(newIndicator.manual_standard_uuid));
      setMessage(`特殊指标“${saved.indicator_name}”已新增。`);
      onChanged();
    } catch (saveError) {
      setError(errorMessage(saveError, "新增特殊指标失败"));
    } finally {
      setSavingId(undefined);
    }
  }

  async function removeIndicator(indicator: SpecialIndicator) {
    if (!window.confirm(`确定删除特殊指标“${indicator.indicator_name}”吗？`)) return;
    setSavingId(indicator.indicator_uuid);
    setError(undefined);
    try {
      await deleteSpecialIndicator(projectUuid, indicator);
      setIndicators((current) => current.filter((item) => item.indicator_uuid !== indicator.indicator_uuid));
      setIndicatorDrafts((current) => current.filter((item) => item.indicator_uuid !== indicator.indicator_uuid));
      setMessage(`特殊指标“${indicator.indicator_name}”已删除。`);
      onChanged();
    } catch (saveError) {
      setError(errorMessage(saveError, "删除特殊指标失败"));
    } finally {
      setSavingId(undefined);
    }
  }

  const manualStandards = standards.filter((item) => item.kind === "manual");

  return (
    <section className="report-form-card report-basic-group" id="basic-standards" aria-labelledby="basic-standards-heading">
      <GroupHeading eyebrow="1.2.2 / 3.1.2" title="标准与特殊指标" headingId="basic-standards-heading" dirty={dirty} />
      <p className="report-form-help">前五项标准由母版固定且只读。每条特殊指标必须关联一项已保存的人工补充标准；人工标准可以不新增特殊指标。</p>
      <Feedback error={error} message={message} conflict={conflict} onReload={load} />
      {isLoading ? <p className="report-loading">正在读取标准...</p> : (
        <>
          <fieldset className="report-subform-card"><legend>固定前五项标准（只读）</legend>
            <ol className="report-standard-list">{standards.filter((item) => item.kind === "template_constant").map((standard) => <li key={standard.standard_uuid}><strong>{standard.code}</strong> {standard.name}</li>)}</ol>
          </fieldset>
          <fieldset className="report-subform-card"><legend>人工补充标准</legend>
            <div className="report-repeat-list">
              {standardDrafts.filter((item) => item.kind === "manual").map((standard) => {
                const rowDirty = !sameData(standard, standards.find((item) => item.standard_uuid === standard.standard_uuid));
                return <div className="report-repeat-card" key={standard.standard_uuid}><div className="report-field-row report-field-row-three"><label><span>标准编号</span><input value={standard.code} onChange={(event) => updateStandardDraft(standard.standard_uuid, "code", event.target.value)} /></label><label><span>标准名称</span><input value={standard.name} onChange={(event) => updateStandardDraft(standard.standard_uuid, "name", event.target.value)} required /></label><label><span>来源说明</span><input value={standard.source_ref} onChange={(event) => updateStandardDraft(standard.standard_uuid, "source_ref", event.target.value)} /></label></div><div className="report-inline-actions"><button type="button" onClick={() => void saveStandard(standard.standard_uuid)} disabled={!rowDirty || savingId === standard.standard_uuid}>{savingId === standard.standard_uuid ? "保存中..." : "保存标准"}</button><button type="button" className="danger-button" onClick={() => void removeStandard(standard)} disabled={savingId === standard.standard_uuid}>删除</button></div></div>;
              })}
            </div>
            <form className="report-repeat-card report-new-entity" onSubmit={createStandard}><h5>新增人工标准</h5><div className="report-field-row report-field-row-three"><label><span>标准编号</span><input value={newStandard.code} onChange={(event) => setNewStandard((current) => ({ ...current, code: event.target.value }))} /></label><label><span>标准名称</span><input value={newStandard.name} onChange={(event) => setNewStandard((current) => ({ ...current, name: event.target.value }))} required /></label><label><span>来源说明</span><input value={newStandard.source_ref} onChange={(event) => setNewStandard((current) => ({ ...current, source_ref: event.target.value }))} /></label></div><button type="submit" disabled={!newStandard.name.trim() || savingId === "new-standard"}>{savingId === "new-standard" ? "新增中..." : "新增人工标准"}</button></form>
          </fieldset>
          <fieldset className="report-subform-card"><legend>特殊指标</legend>
            <div className="report-repeat-list">
              {indicatorDrafts.map((indicator) => {
                const rowDirty = !sameData(indicator, indicators.find((item) => item.indicator_uuid === indicator.indicator_uuid));
                return <div className="report-repeat-card" key={indicator.indicator_uuid}><div className="report-field-row report-field-row-three"><label><span>关联人工标准</span><select value={indicator.manual_standard_uuid} onChange={(event) => updateIndicatorDraft(indicator.indicator_uuid, "manual_standard_uuid", event.target.value)}>{manualStandards.map((standard) => <option key={standard.standard_uuid} value={standard.standard_uuid}>{standard.code} {standard.name}</option>)}</select></label><label><span>指标编号</span><input value={indicator.indicator_code} onChange={(event) => updateIndicatorDraft(indicator.indicator_uuid, "indicator_code", event.target.value)} /></label><label><span>指标名称</span><input value={indicator.indicator_name} onChange={(event) => updateIndicatorDraft(indicator.indicator_uuid, "indicator_name", event.target.value)} required /></label></div><label><span>指标说明</span><textarea rows={3} value={indicator.description} onChange={(event) => updateIndicatorDraft(indicator.indicator_uuid, "description", event.target.value)} /></label><div className="report-inline-actions"><button type="button" onClick={() => void saveIndicator(indicator.indicator_uuid)} disabled={!rowDirty || savingId === indicator.indicator_uuid}>{savingId === indicator.indicator_uuid ? "保存中..." : "保存指标"}</button><button type="button" className="danger-button" onClick={() => void removeIndicator(indicator)} disabled={savingId === indicator.indicator_uuid}>删除</button></div></div>;
              })}
            </div>
            <form className="report-repeat-card report-new-entity" onSubmit={createIndicator}><h5>新增特殊指标</h5><div className="report-field-row report-field-row-three"><label><span>关联人工标准</span><select value={newIndicator.manual_standard_uuid} onChange={(event) => setNewIndicator((current) => ({ ...current, manual_standard_uuid: event.target.value }))} required><option value="">请先选择人工标准</option>{manualStandards.map((standard) => <option key={standard.standard_uuid} value={standard.standard_uuid}>{standard.code} {standard.name}</option>)}</select></label><label><span>指标编号</span><input value={newIndicator.indicator_code} onChange={(event) => setNewIndicator((current) => ({ ...current, indicator_code: event.target.value }))} /></label><label><span>指标名称</span><input value={newIndicator.indicator_name} onChange={(event) => setNewIndicator((current) => ({ ...current, indicator_name: event.target.value }))} required /></label></div><label><span>指标说明</span><textarea rows={3} value={newIndicator.description} onChange={(event) => setNewIndicator((current) => ({ ...current, description: event.target.value }))} /></label><button type="submit" disabled={!newIndicator.manual_standard_uuid || !newIndicator.indicator_name.trim() || savingId === "new-indicator"}>{savingId === "new-indicator" ? "新增中..." : "新增特殊指标"}</button></form>
          </fieldset>
        </>
      )}
    </section>
  );
}

function GroupHeading({ eyebrow, title, headingId, dirty }: { eyebrow: string; title: string; headingId: string; dirty: boolean }) {
  return (
    <div className="report-card-heading"><div><p className="eyebrow">{eyebrow}</p><h4 id={headingId}>{title}</h4></div>
      <span className={dirty ? "dirty-chip" : "clean-chip"}>{dirty ? "未保存" : "已保存"}</span>
    </div>
  );
}

function Feedback({ error, message, conflict, onReload }: {
  error?: string; message?: string; conflict?: ConflictState; onReload: () => Promise<void>;
}) {
  return (
    <>
      {conflict ? <div className="revision-conflict" role="alert"><strong>服务器版本已变化，本地草稿仍保留。</strong><p>{conflict.message}{typeof conflict.currentRevision === "number" ? `（服务器 revision ${conflict.currentRevision}）` : ""}</p><button type="button" className="secondary-button" onClick={() => void onReload()}>放弃本组草稿并刷新</button></div> : null}
      {error ? <p className="error" role="alert">{error}</p> : null}
      {message ? <p className="success" aria-live="polite">{message}</p> : null}
    </>
  );
}

function sameData(first: unknown, second: unknown): boolean {
  return JSON.stringify(first) === JSON.stringify(second);
}

function sameSet<T>(first: Set<T>, second: Set<T>): boolean {
  return first.size === second.size && [...first].every((item) => second.has(item));
}

function dateValue(value?: string | null, length = 10): string {
  return value ? value.slice(0, length) : "";
}

function lines(value: string): string[] {
  return value.split(/\r?\n/).map((item) => item.trim()).filter((item, index, values) => Boolean(item) && values.indexOf(item) === index);
}

function isRevisionConflict(error: unknown): error is ApiError {
  return error instanceof ApiError && error.status === 409 && error.code === "REVISION_CONFLICT";
}

function handleSaveError(
  error: unknown,
  setConflict: (value: ConflictState | undefined) => void,
  setError: (value: string | undefined) => void,
  fallback: string
) {
  if (isRevisionConflict(error)) {
    const details = error.details && typeof error.details === "object" ? error.details as Record<string, unknown> : {};
    setConflict({ message: error.message, currentRevision: typeof details.current_revision === "number" ? details.current_revision : undefined });
    return;
  }
  setError(errorMessage(error, fallback));
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}
