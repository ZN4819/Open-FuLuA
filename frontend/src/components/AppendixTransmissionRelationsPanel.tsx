import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  getAppendixTransmissionRelations,
  updateAppendixTransmissionRelation,
  type AppendixTransmissionObject,
  type AppendixTransmissionRelation,
  type AppendixTransmissionRelations,
  type TransmissionRelationKind
} from "../api/reportClient.ts";

type AppendixTransmissionRelationsPanelProps = {
  projectUuid: string;
  sectionCode: "A-2" | "A-4";
  hasUnsavedChanges: boolean;
  hasUnsavedObjects: boolean;
  onSharedSubsystemsChange: (names: string[]) => void;
};

const KIND_ORDER: TransmissionRelationKind[] = ["confidentiality", "integrity"];

const KIND_DETAILS: Record<TransmissionRelationKind, { label: string; a2: string; a4: string }> = {
  confidentiality: {
    label: "机密性",
    a2: "通信过程中重要数据的机密性",
    a4: "重要数据传输机密性"
  },
  integrity: {
    label: "完整性",
    a2: "通信数据完整性",
    a4: "重要数据传输完整性"
  }
};

export function AppendixTransmissionRelationsPanel({
  projectUuid,
  sectionCode,
  hasUnsavedChanges,
  hasUnsavedObjects,
  onSharedSubsystemsChange
}: AppendixTransmissionRelationsPanelProps) {
  const [data, setData] = useState<AppendixTransmissionRelations>();
  const [isLoading, setIsLoading] = useState(true);
  const [savingKey, setSavingKey] = useState<string>();
  const [error, setError] = useState<string>();
  const [message, setMessage] = useState<string>();
  const previousHasUnsavedChanges = useRef(hasUnsavedChanges);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(undefined);
    try {
      const next = await getAppendixTransmissionRelations(projectUuid);
      setData(next);
      onSharedSubsystemsChange(uniqueNames(next.shared_subsystems));
    } catch (loadError) {
      setError(errorMessage(loadError, "读取双向传输指标关联失败"));
    } finally {
      setIsLoading(false);
    }
  }, [onSharedSubsystemsChange, projectUuid]);

  useEffect(() => { void load(); }, [load, sectionCode]);

  useEffect(() => {
    const wasUnsaved = previousHasUnsavedChanges.current;
    previousHasUnsavedChanges.current = hasUnsavedChanges;
    if (wasUnsaved && !hasUnsavedChanges && !savingKey) {
      void load();
    }
  }, [hasUnsavedChanges, load, savingKey]);

  const objects = sectionCode === "A-2" ? data?.a2_objects ?? [] : data?.a4_objects ?? [];
  const objectMap = useMemo(
    () => new Map([...(data?.a2_objects ?? []), ...(data?.a4_objects ?? [])].map((item) => [item.object_uuid, item])),
    [data]
  );
  const editingBlocked = hasUnsavedChanges || hasUnsavedObjects || Boolean(savingKey);

  async function saveRelation(
    kind: TransmissionRelationKind,
    a4ObjectUuid: string,
    a2ObjectUuid: string | null,
    current?: AppendixTransmissionRelation
  ) {
    if (hasUnsavedChanges || savingKey) return;
    const key = `${kind}:${a4ObjectUuid}`;
    setSavingKey(key);
    setError(undefined);
    setMessage(undefined);
    try {
      const refreshed = await updateAppendixTransmissionRelation(projectUuid, {
        kind,
        a4_object_uuid: a4ObjectUuid,
        a2_object_uuid: a2ObjectUuid,
        expected_correction_uuid: current?.correction_uuid ?? null,
        expected_revision: current?.revision ?? null
      });
      setData(refreshed);
      onSharedSubsystemsChange(uniqueNames(refreshed.shared_subsystems));
      setMessage("关联已更新，A-2 与 A-4 两侧已重新读取并同步。" );
    } catch (saveError) {
      setError(errorMessage(saveError, "保存传输指标关联失败"));
    } finally {
      setSavingKey(undefined);
    }
  }

  return (
    <section className="appendix-transmission-relations" aria-label="双向传输指标关联">
      <div className="appendix-transmission-heading">
        <div>
          <p className="eyebrow">A-2 / A-4 业务关系</p>
          <h3>双向传输指标关联</h3>
        </div>
        <button type="button" className="secondary-button" onClick={() => void load()} disabled={isLoading || Boolean(savingKey)}>
          {isLoading ? "读取中..." : "刷新关联"}
        </button>
      </div>
      <p className="appendix-transmission-help">
        仅显示同一子系统、且同时具备对应传输指标的候选对象。A-4 每类指标关联一个 A-2 网络通道；一个 A-2 通道可以关联多个 A-4 对象。
      </p>

      {hasUnsavedObjects ? (
        <p className="warning-text" role="status">存在新对象尚未保存，请先保存附录 A 后再设置关联。</p>
      ) : hasUnsavedChanges ? (
        <p className="warning-text" role="status">当前附录 A 有未保存修改，请先保存后再调整关联。</p>
      ) : null}
      {error ? <p className="error" role="alert">{error}</p> : null}
      {message ? <p className="success-message" role="status">{message}</p> : null}
      {isLoading && !data ? <p className="loading-text">正在读取已保存对象和关联...</p> : null}

      {!isLoading && data && objects.length === 0 ? (
        <p className="technical-object-empty">当前章节还没有已保存且可参与传输指标关联的对象。</p>
      ) : null}

      {data && objects.length > 0 ? (
        <div className="appendix-transmission-object-list">
          {objects.map((object) => (
            <article className="appendix-transmission-object" key={object.object_uuid}>
              <header>
                <strong>{object.object_name}</strong>
                <span>{object.subsystem || "未设置子系统"}</span>
              </header>
              {!object.subsystem.trim() ? (
                <p className="warning-text">请先为该对象设置子系统并保存，之后才能匹配候选对象。</p>
              ) : null}
              {orderedKinds(object).length === 0 ? (
                <p className="technical-object-empty">该对象不涉及机密性或完整性传输指标。</p>
              ) : sectionCode === "A-4" ? (
                <A4RelationControls
                  object={object}
                  a2Objects={data.a2_objects}
                  disabled={editingBlocked}
                  savingKey={savingKey}
                  onSave={saveRelation}
                />
              ) : (
                <A2RelationControls
                  object={object}
                  a4Objects={data.a4_objects}
                  objectMap={objectMap}
                  disabled={editingBlocked}
                  savingKey={savingKey}
                  onSave={saveRelation}
                />
              )}
            </article>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function A4RelationControls({
  object,
  a2Objects,
  disabled,
  savingKey,
  onSave
}: {
  object: AppendixTransmissionObject;
  a2Objects: AppendixTransmissionObject[];
  disabled: boolean;
  savingKey?: string;
  onSave: (
    kind: TransmissionRelationKind,
    a4ObjectUuid: string,
    a2ObjectUuid: string | null,
    current?: AppendixTransmissionRelation
  ) => void | Promise<void>;
}) {
  return (
    <div className="appendix-transmission-kind-list">
      {orderedKinds(object).map((kind) => {
        const candidates = candidatesFor(object, a2Objects, kind);
        const current = relationForA4(object, kind);
        const saving = savingKey === `${kind}:${object.object_uuid}`;
        return (
          <label className="appendix-transmission-select" key={kind}>
            <span>{KIND_DETAILS[kind].label}关联网络通道</span>
            <small>{KIND_DETAILS[kind].a4} ↔ {KIND_DETAILS[kind].a2}</small>
            <select
              value={current?.a2_object_uuid ?? ""}
              disabled={disabled || (candidates.length === 0 && !current)}
              onChange={(event) => void onSave(kind, object.object_uuid, event.target.value || null, current)}
            >
              <option value="">不关联</option>
              {candidates.map((candidate) => <option value={candidate.object_uuid} key={candidate.object_uuid}>{candidate.object_name}</option>)}
            </select>
            {saving ? <em>正在同步两侧...</em> : null}
            {candidates.length === 0 ? <em>当前子系统没有具备{KIND_DETAILS[kind].label}指标的 A-2 网络通道。</em> : null}
          </label>
        );
      })}
    </div>
  );
}

function A2RelationControls({
  object,
  a4Objects,
  objectMap,
  disabled,
  savingKey,
  onSave
}: {
  object: AppendixTransmissionObject;
  a4Objects: AppendixTransmissionObject[];
  objectMap: Map<string, AppendixTransmissionObject>;
  disabled: boolean;
  savingKey?: string;
  onSave: (
    kind: TransmissionRelationKind,
    a4ObjectUuid: string,
    a2ObjectUuid: string | null,
    current?: AppendixTransmissionRelation
  ) => void | Promise<void>;
}) {
  return (
    <div className="appendix-transmission-kind-list">
      {orderedKinds(object).map((kind) => {
        const candidates = candidatesFor(object, a4Objects, kind);
        return (
          <fieldset className="appendix-transmission-checkboxes" key={kind} disabled={disabled || candidates.length === 0}>
            <legend>{KIND_DETAILS[kind].label}关联应用与重要数据对象</legend>
            <small>{KIND_DETAILS[kind].a2} ↔ {KIND_DETAILS[kind].a4}</small>
            {candidates.length === 0 ? (
              <p>当前子系统没有具备{KIND_DETAILS[kind].label}指标的 A-4 对象。</p>
            ) : candidates.map((candidate) => {
              const current = relationForA4(candidate, kind);
              const checked = current?.a2_object_uuid === object.object_uuid;
              const otherChannel = current && !checked ? objectMap.get(current.a2_object_uuid) : undefined;
              const saving = savingKey === `${kind}:${candidate.object_uuid}`;
              return (
                <label key={candidate.object_uuid}>
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={(event) => void onSave(
                      kind,
                      candidate.object_uuid,
                      event.target.checked ? object.object_uuid : null,
                      current
                    )}
                  />
                  <span>
                    <strong>{candidate.object_name}</strong>
                    {saving ? <small>正在同步两侧...</small> : null}
                    {otherChannel ? <small>当前关联其他网络通道，勾选后将改为当前通道。</small> : null}
                  </span>
                </label>
              );
            })}
          </fieldset>
        );
      })}
    </div>
  );
}

function candidatesFor(
  source: AppendixTransmissionObject,
  candidates: AppendixTransmissionObject[],
  kind: TransmissionRelationKind
) {
  const subsystem = normalizeSubsystemName(source.subsystem);
  if (!subsystem) return [];
  return candidates.filter((candidate) =>
    normalizeSubsystemName(candidate.subsystem) === subsystem && candidate.available_kinds.includes(kind)
  );
}

function relationForA4(object: AppendixTransmissionObject, kind: TransmissionRelationKind) {
  return object.relations.find((relation) => relation.kind === kind && relation.a4_object_uuid === object.object_uuid);
}

function orderedKinds(object: AppendixTransmissionObject) {
  return KIND_ORDER.filter((kind) => object.available_kinds.includes(kind));
}

function uniqueNames(names: string[]) {
  const result = new Map<string, string>();
  names.forEach((name) => {
    const displayName = name.trim();
    const normalized = normalizeSubsystemName(displayName);
    if (normalized && !result.has(normalized)) result.set(normalized, displayName);
  });
  return [...result.values()];
}

function normalizeSubsystemName(value: string) {
  return value.trim().replace(/\s+/g, " ").toLowerCase();
}

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback;
}
