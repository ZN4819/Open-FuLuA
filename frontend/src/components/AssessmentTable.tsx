import { useEffect, useRef, useState } from "react";

import type { AssessmentRowInput, CrossReferenceInput, EvidenceImage, RecordTemplateSlot, TemplateProfile } from "../api/client";

export type SubsystemUiState = {
  manualSubsystemNames: string[];
  activeSubsystem: string;
};

export type EvidenceImageFilterState = {
  active: boolean;
  imageIds: number[];
};

type SubsystemUiStateUpdater = (current: SubsystemUiState) => SubsystemUiState;
type SubsystemUiStateChangeOptions = {
  dirty?: boolean;
};

type AssessmentTableProps = {
  sectionCode: string;
  rows: AssessmentRowInput[];
  profile: TemplateProfile;
  isSaving: boolean;
  isDirty: boolean;
  evidenceImages: EvidenceImage[];
  recordTemplateSlots: RecordTemplateSlot[];
  subsystemUiState: SubsystemUiState;
  onRowsChange: (rows: AssessmentRowInput[]) => void;
  onSubsystemUiStateChange: (updater: SubsystemUiStateUpdater, options?: SubsystemUiStateChangeOptions) => void;
  onVisibleEvidenceFilterChange?: (filter: EvidenceImageFilterState) => void;
  onSave: () => void;
};

const EMPTY_METRIC = {
  d: undefined,
  a: undefined,
  k: undefined,
  object_score: "",
  unit_score: "",
  compliance: undefined
};
const FIGURE_PLACEHOLDER = "[插入图片引用]";

type TextSelection = {
  start: number;
  end: number;
};

type FigureReferenceOption = {
  token: string;
  displayText: string;
  target_image_id?: number | null;
};

function isTechnicalSection(code: string) {
  return ["A-1", "A-2", "A-3", "A-4"].includes(code);
}

const SUBSYSTEM_SECTION_CODES = ["A-2", "A-4"];
const A4_SECTION_CODE = "A-4";
const A4_UNIT_SCOPED_OBJECT_UNIT = "重要信息资源安全标记完整性";

function supportsSubsystem(sectionCode: string) {
  return SUBSYSTEM_SECTION_CODES.includes(sectionCode);
}

const A4_OBJECT_CATEGORIES = [
  { value: "user", label: "用户", placeholder: "例如：PC端用户", units: ["身份鉴别"] },
  { value: "access_control_info", label: "访问控制信息", placeholder: "例如：用户权限控制信息", units: ["访问控制信息完整性"] },
  {
    value: "important_data",
    label: "重要数据",
    placeholder: "例如：用户个人信息",
    units: ["重要数据传输机密性", "重要数据传输完整性", "重要数据存储机密性", "重要数据存储完整性"]
  },
  { value: "key_business_action", label: "关键业务行为", placeholder: "例如：支付交易", units: ["不可否认性"] }
] as const;

type TechnicalObjectCategoryValue = (typeof A4_OBJECT_CATEGORIES)[number]["value"];

function isA4UnitScopedObjectUnit(sectionCode: string, unit: string) {
  return sectionCode === A4_SECTION_CODE && unit.trim() === A4_UNIT_SCOPED_OBJECT_UNIT;
}

function isSectionManagedTechnicalObjectUnit(sectionCode: string, unit: string) {
  return !isA4UnitScopedObjectUnit(sectionCode, unit);
}

function technicalObjectCategoriesForSection(sectionCode: string) {
  return sectionCode === A4_SECTION_CODE ? A4_OBJECT_CATEGORIES : [];
}

function targetUnitsForTechnicalObject(sectionCode: string, unitOrder: string[], categoryValue: TechnicalObjectCategoryValue) {
  if (sectionCode !== A4_SECTION_CODE) {
    return unitOrder;
  }

  const category = A4_OBJECT_CATEGORIES.find((item) => item.value === categoryValue) ?? A4_OBJECT_CATEGORIES[0];
  const targetUnits = new Set<string>(category.units);
  return unitOrder.filter((unit) => targetUnits.has(unit));
}

function technicalObjectCategoryPlaceholder(categoryValue: TechnicalObjectCategoryValue) {
  return A4_OBJECT_CATEGORIES.find((item) => item.value === categoryValue)?.placeholder ?? "例如：XX机房";
}

function canAddObjectWithinUnit(sectionCode: string, unit: string, technical: boolean) {
  return !technical || isA4UnitScopedObjectUnit(sectionCode, unit);
}

function createEmptyRow(sortOrder: number, unit: string, subsystem = ""): AssessmentRowInput {
  return {
    unit,
    object_name: "",
    subsystem,
    record_text: "",
    sort_order: sortOrder,
    metric_result: { ...EMPTY_METRIC },
    cross_references: []
  };
}

function uniqueValues(values: string[]) {
  const result: string[] = [];
  values.forEach((value) => {
    const trimmed = value.trim();
    if (trimmed && !result.includes(trimmed)) {
      result.push(trimmed);
    }
  });
  return result;
}

function sectionFigurePrefix(sectionCode: string, profile: TemplateProfile) {
  return profile.sections.find((section) => section.code === sectionCode)?.figure_prefix ?? `图${sectionCode}-`;
}

function imageFigureDisplayText(image: EvidenceImage, sectionCode: string, profile: TemplateProfile) {
  return image.figure_label ?? `${sectionFigurePrefix(sectionCode, profile)}${image.sort_order}`;
}

function figureReferenceOptions(
  row: AssessmentRowInput,
  evidenceImages: EvidenceImage[],
  sectionCode: string,
  profile: TemplateProfile
) {
  const optionsByToken = new Map<string, FigureReferenceOption>();

  evidenceImages.forEach((image) => {
    const token = `[[FIG:${image.id}]]`;
    optionsByToken.set(token, {
      token,
      displayText: imageFigureDisplayText(image, sectionCode, profile),
      target_image_id: image.id
    });
  });

  (row.cross_references ?? []).forEach((reference) => {
    const token = reference.token.trim();
    if (!token) {
      return;
    }
    const existing = optionsByToken.get(token);
    optionsByToken.set(token, {
      token,
      displayText: reference.display_text?.trim() || existing?.displayText || token,
      target_image_id: reference.target_image_id ?? existing?.target_image_id ?? null
    });
  });

  return Array.from(optionsByToken.values()).sort(
    (first, second) => second.displayText.length - first.displayText.length || first.token.localeCompare(second.token)
  );
}

function displayRecordText(
  row: AssessmentRowInput,
  evidenceImages: EvidenceImage[],
  sectionCode: string,
  profile: TemplateProfile
) {
  return figureReferenceOptions(row, evidenceImages, sectionCode, profile).reduce(
    (text, reference) => replaceAllText(text, reference.token, reference.displayText),
    row.record_text ?? ""
  );
}

function storedRecordText(
  displayText: string,
  row: AssessmentRowInput,
  evidenceImages: EvidenceImage[],
  sectionCode: string,
  profile: TemplateProfile
) {
  return figureReferenceOptions(row, evidenceImages, sectionCode, profile).reduce(
    (text, reference) => replaceAllText(text, reference.displayText, reference.token),
    displayText
  );
}

function crossReferencesForRecordText(
  recordText: string,
  row: AssessmentRowInput,
  evidenceImages: EvidenceImage[],
  sectionCode: string,
  profile: TemplateProfile
): CrossReferenceInput[] {
  const optionsByToken = new Map(
    figureReferenceOptions(row, evidenceImages, sectionCode, profile).map((reference) => [reference.token, reference])
  );
  const references: CrossReferenceInput[] = [];
  const seenTokens = new Set<string>();
  const tokenPattern = /\[\[FIG:(\d+)\]\]/g;
  let match = tokenPattern.exec(recordText);

  while (match) {
    const token = match[0];
    if (!seenTokens.has(token)) {
      seenTokens.add(token);
      const imageId = Number(match[1]);
      const reference = optionsByToken.get(token);
      references.push({
        target_image_id: reference?.target_image_id ?? (Number.isFinite(imageId) ? imageId : null),
        token,
        display_text: reference?.displayText ?? token
      });
    }
    match = tokenPattern.exec(recordText);
  }

  return references;
}

function referencedEvidenceImageIds(rows: AssessmentRowInput[]) {
  const result: number[] = [];
  rows.forEach((row) => {
    (row.cross_references ?? []).forEach((reference) => {
      const imageId = reference.target_image_id;
      if (typeof imageId === "number" && Number.isFinite(imageId) && !result.includes(imageId)) {
        result.push(imageId);
      }
    });

    const tokenPattern = /\[\[FIG:(\d+)\]\]/g;
    let match = tokenPattern.exec(row.record_text ?? "");
    while (match) {
      const imageId = Number(match[1]);
      if (Number.isFinite(imageId) && !result.includes(imageId)) {
        result.push(imageId);
      }
      match = tokenPattern.exec(row.record_text ?? "");
    }
  });
  return result;
}

function replaceAllText(text: string, search: string, replacement: string) {
  if (!search || search === replacement) {
    return text;
  }
  return text.split(search).join(replacement);
}

const SCORE_EXCLUDED_VALUE = "/";

function scoreText(value: string | null | undefined) {
  return (value ?? "").trim();
}

function formatScoreToFourDecimals(value: string | null | undefined) {
  const text = scoreText(value);
  if (!text || text === SCORE_EXCLUDED_VALUE) {
    return text;
  }
  const score = Number(text);
  if (!Number.isFinite(score)) {
    return text;
  }
  return score.toFixed(4);
}

function calculateTechnicalUnitScore(rows: AssessmentRowInput[]) {
  const numericScores: number[] = [];
  let filledScores = 0;
  let excludedScores = 0;

  rows.forEach((row) => {
    const score = scoreText(row.metric_result?.object_score);
    if (!score) {
      return;
    }
    filledScores += 1;
    if (score === SCORE_EXCLUDED_VALUE) {
      excludedScores += 1;
      return;
    }
    const numericScore = Number(score);
    if (Number.isFinite(numericScore)) {
      numericScores.push(numericScore);
    }
  });

  if (numericScores.length > 0) {
    const total = numericScores.reduce((sum, score) => sum + score, 0);
    return (total / numericScores.length).toFixed(4);
  }
  if (rows.length > 0 && filledScores === rows.length && excludedScores === rows.length) {
    return SCORE_EXCLUDED_VALUE;
  }
  return "";
}

function applyCalculatedUnitScores(rows: AssessmentRowInput[], shouldCalculate: boolean) {
  if (!shouldCalculate) {
    return rows;
  }

  const rowsByUnit = new Map<string, AssessmentRowInput[]>();
  rows.forEach((row) => {
    const unit = row.unit.trim();
    rowsByUnit.set(unit, [...(rowsByUnit.get(unit) ?? []), row]);
  });

  const scoreByUnit = new Map<string, string>();
  rowsByUnit.forEach((unitRows, unit) => {
    scoreByUnit.set(unit, calculateTechnicalUnitScore(unitRows));
  });

  return rows.map((row) => ({
    ...row,
    metric_result: {
      ...(row.metric_result ?? EMPTY_METRIC),
      unit_score: scoreByUnit.get(row.unit.trim()) ?? ""
    }
  }));
}
function fixedUnitsFromSlots(recordTemplateSlots: RecordTemplateSlot[], rows: AssessmentRowInput[]) {
  return uniqueValues([
    ...recordTemplateSlots.map((slot) => slot.unit),
    ...rows.map((row) => row.unit)
  ]);
}

const TEMPLATE_SLOT_ORDER: Record<RecordTemplateSlot["template_type"], number> = {
  compliant: 0,
  non_compliant: 1,
  not_applicable: 2
};

function templateSlotOptionLabel(slot: RecordTemplateSlot) {
  const label = slot.template_type_label.trim();
  const title = slot.title.trim();
  if (title && label && title !== label) {
    return `${label} - ${title}`;
  }
  return title || label || "未命名模板";
}

function templateSlotsForUnit(recordTemplateSlots: RecordTemplateSlot[], unit: string) {
  return recordTemplateSlots
    .filter((slot) => slot.unit === unit)
    .sort((first, second) => TEMPLATE_SLOT_ORDER[first.template_type] - TEMPLATE_SLOT_ORDER[second.template_type]);
}
function normalizeRows(rows: AssessmentRowInput[], unitOrder: string[] = [], calculateUnitScores = false) {
  const order = new Map(unitOrder.map((unit, index) => [unit, index]));
  const normalizedRows = rows
    .map((row, index) => {
      const unit = row.unit.trim();
      return {
        row: {
          ...row,
          unit,
          subsystem: row.subsystem?.trim() ?? "",
          sort_order: index + 1,
          metric_result: row.metric_result ?? { ...EMPTY_METRIC },
          cross_references: row.cross_references ?? []
        },
        index
      };
    })
    .sort((first, second) => {
      const firstOrder = order.get(first.row.unit) ?? unitOrder.length;
      const secondOrder = order.get(second.row.unit) ?? unitOrder.length;
      return firstOrder - secondOrder || first.index - second.index;
    })
    .map(({ row }, index) => ({
      ...row,
      sort_order: index + 1
    }));

  return applyCalculatedUnitScores(normalizedRows, calculateUnitScores);
}

export function AssessmentTable({
  sectionCode,
  rows,
  profile,
  isSaving,
  isDirty,
  evidenceImages,
  recordTemplateSlots,
  subsystemUiState,
  onRowsChange,
  onSubsystemUiStateChange,
  onVisibleEvidenceFilterChange,
  onSave
}: AssessmentTableProps) {
  const technical = isTechnicalSection(sectionCode);
  const metricOptions = profile.content_controls.technical_metric.options;
  const complianceOptions = profile.content_controls.management_compliance.options;
  const recordSelections = useRef<Record<number, TextSelection>>({});
  const [technicalObjectName, setTechnicalObjectName] = useState("");
  const [technicalObjectCategory, setTechnicalObjectCategory] = useState<TechnicalObjectCategoryValue>("user");
  const [technicalUnitFilter, setTechnicalUnitFilter] = useState("");
  const [technicalObjectFilter, setTechnicalObjectFilter] = useState("");
  const [newSubsystemName, setNewSubsystemName] = useState("");
  useEffect(() => {
    setNewSubsystemName("");
    setTechnicalUnitFilter("");
    setTechnicalObjectFilter("");
  }, [sectionCode]);
  const tableTitle = technical ? "D / A / K 指标录入" : "符合情况录入";
  const unitOrder = fixedUnitsFromSlots(recordTemplateSlots, rows);
  const normalizedRows = normalizeRows(rows, unitOrder, technical);
  const sectionSupportsSubsystem = supportsSubsystem(sectionCode);
  const manualSubsystemNames = subsystemUiState.manualSubsystemNames;
  const activeSubsystem = subsystemUiState.activeSubsystem;
  const subsystemNames = sectionSupportsSubsystem
    ? uniqueValues([...manualSubsystemNames, ...normalizedRows.map((row) => row.subsystem ?? "")])
    : [];
  const activeSubsystemName =
    sectionSupportsSubsystem && subsystemNames.includes(activeSubsystem.trim()) ? activeSubsystem.trim() : "";
  const subsystemRowEntries = normalizedRows
    .map((row, index) => ({ row, index }))
    .filter((entry) => !sectionSupportsSubsystem || !activeSubsystemName || entry.row.subsystem?.trim() === activeSubsystemName);
  const unitFilterOptions = technical
    ? unitOrder.filter((unit) => subsystemRowEntries.some((entry) => entry.row.unit.trim() === unit))
    : [];
  const activeUnitFilter = technical && unitFilterOptions.includes(technicalUnitFilter.trim()) ? technicalUnitFilter.trim() : "";
  const unitFilteredRowEntries = subsystemRowEntries.filter(
    (entry) => !activeUnitFilter || entry.row.unit.trim() === activeUnitFilter
  );
  const objectFilterOptions = technical ? uniqueValues(unitFilteredRowEntries.map((entry) => entry.row.object_name)) : [];
  const activeObjectFilter =
    technical && objectFilterOptions.includes(technicalObjectFilter.trim()) ? technicalObjectFilter.trim() : "";
  const visibleRowEntries = unitFilteredRowEntries.filter(
    (entry) => !activeObjectFilter || entry.row.object_name.trim() === activeObjectFilter
  );
  const sectionManagedTechnicalRows = technical
    ? visibleRowEntries
        .map((entry) => entry.row)
        .filter((row) => isSectionManagedTechnicalObjectUnit(sectionCode, row.unit))
    : [];
  const allTechnicalObjectNames = uniqueValues(visibleRowEntries.map((entry) => entry.row.object_name));
  const technicalObjectNames = uniqueValues(sectionManagedTechnicalRows.map((row) => row.object_name));
  const technicalObjectEntries = technicalObjectNames.map((objectName) => {
    const objectSubsystemNames = uniqueValues(
      sectionManagedTechnicalRows
        .filter((row) => row.object_name.trim() === objectName)
        .map((row) => row.subsystem ?? "")
    );
    return { objectName, objectSubsystemNames };
  });
  const unassignedTechnicalObjectNames = sectionSupportsSubsystem
    ? uniqueValues(
        normalizedRows
          .filter((row) => !row.subsystem?.trim())
          .map((row) => row.object_name)
      )
    : [];
  const objectCount = technical ? allTechnicalObjectNames.length : visibleRowEntries.length;
  const technicalObjectCategoryOptions = technicalObjectCategoriesForSection(sectionCode);
  const technicalObjectTargetUnits = targetUnitsForTechnicalObject(sectionCode, unitOrder, technicalObjectCategory);
  const technicalObjectPlaceholder = sectionSupportsSubsystem && !activeSubsystemName
    ? "请先新增或选择子系统"
    : sectionCode === A4_SECTION_CODE ? technicalObjectCategoryPlaceholder(technicalObjectCategory) : "例如：XX机房";
  const technicalObjectAddDisabled =
    isSaving ||
    technicalObjectTargetUnits.length === 0 ||
    !technicalObjectName.trim() ||
    (sectionSupportsSubsystem && !activeSubsystemName);
  const technicalObjectEmptyText = sectionSupportsSubsystem && activeSubsystemName
    ? "当前子系统还没有测评对象。"
    : sectionCode === A4_SECTION_CODE ? "当前章节还没有通过上方分类新增测评对象。" : "当前章节还没有测评对象。";
  const templateSlotCount = recordTemplateSlots.length;
  const templateTypeCount = uniqueValues(recordTemplateSlots.map((slot) => slot.template_type)).length;
  const filterActive = Boolean(activeSubsystemName || activeUnitFilter || activeObjectFilter);
  const visibleEvidenceImageIds = referencedEvidenceImageIds(visibleRowEntries.map((entry) => entry.row));
  const groupedUnitOrder = technical && activeUnitFilter ? [activeUnitFilter] : unitOrder;
  const groupedRows = groupedUnitOrder.map((unit) => {
    const entries = visibleRowEntries.filter((entry) => entry.row.unit.trim() === unit);
    const unitScore = technical ? calculateTechnicalUnitScore(entries.map((entry) => entry.row)) : scoreText(entries[0]?.row.metric_result?.unit_score);
    return { unit, entries, unitScore };
  });
  const tableColumnCount = technical ? 9 : 6;
  const visibleEvidenceFilterKey = `${filterActive ? "1" : "0"}:${visibleEvidenceImageIds.join(",")}`;

  useEffect(() => {
    onVisibleEvidenceFilterChange?.({
      active: filterActive,
      imageIds: filterActive ? visibleEvidenceImageIds : []
    });
  }, [onVisibleEvidenceFilterChange, visibleEvidenceFilterKey]);

  function setManualSubsystemNamesForSection(updater: (current: string[]) => string[]) {
    onSubsystemUiStateChange((current) => ({
      ...current,
      manualSubsystemNames: updater(current.manualSubsystemNames)
    }), { dirty: true });
  }

  function setActiveSubsystemForSection(subsystemName: string) {
    onSubsystemUiStateChange((current) => ({
      ...current,
      activeSubsystem: subsystemName
    }));
  }

  function updateRow(index: number, patch: Partial<AssessmentRowInput>) {
    const next = normalizeRows(
      normalizedRows.map((row, rowIndex) => (rowIndex === index ? { ...row, ...patch } : row)),
      unitOrder,
      technical
    );
    onRowsChange(next);
  }

  function updateMetric(index: number, key: string, value: string) {
    const row = normalizedRows[index];
    updateRow(index, {
      metric_result: {
        ...(row.metric_result ?? EMPTY_METRIC),
        [key]: value
      }
    });
  }

  function formatObjectScore(index: number) {
    const value = normalizedRows[index]?.metric_result?.object_score;
    updateMetric(index, "object_score", formatScoreToFourDecimals(value));
  }

  function addSubsystem() {
    const subsystemName = newSubsystemName.trim();
    if (!sectionSupportsSubsystem || !subsystemName) {
      return;
    }
    setManualSubsystemNamesForSection((current) => uniqueValues([...current, subsystemName]));
    setActiveSubsystemForSection(subsystemName);
    setNewSubsystemName("");
  }

  function removeSubsystem(subsystemNameValue: string) {
    const subsystemName = subsystemNameValue.trim();
    if (!sectionSupportsSubsystem || !subsystemName) {
      return;
    }
    recordSelections.current = {};
    setManualSubsystemNamesForSection((current) => current.filter((item) => item.trim() !== subsystemName));
    if (activeSubsystemName === subsystemName) {
      setActiveSubsystemForSection("");
    }
    onRowsChange(
      normalizeRows(
        normalizedRows.filter((row) => row.subsystem?.trim() !== subsystemName),
        unitOrder,
        technical
      )
    );
  }

  function assignUnassignedObjectsToSubsystem(objectNameValue?: string) {
    const objectName = (objectNameValue ?? "").trim();
    if (!sectionSupportsSubsystem || !activeSubsystemName) {
      return;
    }
    onRowsChange(
      normalizeRows(
        normalizedRows.map((row) => {
          const rowObjectName = row.object_name.trim();
          const shouldAssign =
            !row.subsystem?.trim() &&
            rowObjectName &&
            (!objectName || rowObjectName === objectName);
          return shouldAssign ? { ...row, subsystem: activeSubsystemName } : row;
        }),
        unitOrder,
        technical
      )
    );
  }

  function removeObjectFromActiveSubsystem(objectNameValue: string | undefined, subsystemNameValue = activeSubsystemName) {
    const objectName = (objectNameValue ?? "").trim();
    const subsystemName = subsystemNameValue.trim();
    if (!sectionSupportsSubsystem || !subsystemName || !objectName) {
      return;
    }
    recordSelections.current = {};
    onRowsChange(
      normalizeRows(
        normalizedRows.map((row) => {
          const shouldRemove =
            row.object_name.trim() === objectName &&
            row.subsystem?.trim() === subsystemName;
          return shouldRemove ? { ...row, subsystem: "" } : row;
        }),
        unitOrder,
        technical
      )
    );
  }

  function addRow(unit: string) {
    if (sectionSupportsSubsystem && !activeSubsystemName) {
      return;
    }
    onRowsChange(
      normalizeRows(
        [...normalizedRows, createEmptyRow(normalizedRows.length + 1, unit, sectionSupportsSubsystem ? activeSubsystemName : "")],
        unitOrder,
        technical
      )
    );
  }

  function addTechnicalSectionObject() {
    const objectName = technicalObjectName.trim();
    if (!technical || !objectName || technicalObjectTargetUnits.length === 0 || (sectionSupportsSubsystem && !activeSubsystemName)) {
      return;
    }
    const existingObjectUnits = new Set(
      normalizedRows
        .filter(
          (row) =>
            row.object_name.trim() === objectName &&
            (!sectionSupportsSubsystem || row.subsystem?.trim() === activeSubsystemName)
        )
        .map((row) => row.unit.trim())
    );
    const appendedRows = technicalObjectTargetUnits
      .filter((unit) => !existingObjectUnits.has(unit))
      .map((unit, offset) => ({
        ...createEmptyRow(normalizedRows.length + offset + 1, unit, sectionSupportsSubsystem ? activeSubsystemName : ""),
        object_name: objectName,
        subsystem: activeSubsystemName
      }));
    if (appendedRows.length > 0) {
      onRowsChange(normalizeRows([...normalizedRows, ...appendedRows], unitOrder, technical));
    }
    setTechnicalObjectName("");
  }

  function removeTechnicalSectionObject(objectNameValue: string | undefined) {
    const objectName = (objectNameValue ?? "").trim();
    if (!technical || !objectName) {
      return;
    }
    recordSelections.current = {};
    onRowsChange(
      normalizeRows(
        normalizedRows.filter(
          (row) =>
            row.object_name.trim() !== objectName ||
            !isSectionManagedTechnicalObjectUnit(sectionCode, row.unit) ||
            (sectionSupportsSubsystem && activeSubsystemName && row.subsystem?.trim() !== activeSubsystemName)
        ),
        unitOrder,
        technical
      )
    );
  }

  function removeRow(index: number) {
    onRowsChange(normalizeRows(normalizedRows.filter((_, rowIndex) => rowIndex !== index), unitOrder, technical));
  }

  function updateUnitScoreForUnit(unit: string, value: string) {
    const next = normalizeRows(
      normalizedRows.map((row) => {
        if (row.unit.trim() !== unit) {
          return row;
        }
        return {
          ...row,
          metric_result: {
            ...(row.metric_result ?? EMPTY_METRIC),
            unit_score: value
          }
        };
      }),
      unitOrder,
      false
    );
    onRowsChange(next);
  }

  function rememberRecordSelection(index: number, target: HTMLTextAreaElement) {
    recordSelections.current[index] = {
      start: target.selectionStart,
      end: target.selectionEnd
    };
  }

  function insertReferenceToken(index: number, imageIdValue: string) {
    const imageId = Number(imageIdValue);
    const image = evidenceImages.find((item) => item.id === imageId);
    if (!image) {
      return;
    }
    const row = normalizedRows[index];
    const displayText = imageFigureDisplayText(image, sectionCode, profile);
    const displayRecordTextValue = insertAtSelectionOrPlaceholder(
      displayRecordText(row, evidenceImages, sectionCode, profile),
      displayText,
      recordSelections.current[index]
    );
    const recordText = storedRecordText(displayRecordTextValue, row, evidenceImages, sectionCode, profile);
    updateRow(index, {
      record_text: recordText,
      cross_references: crossReferencesForRecordText(recordText, row, evidenceImages, sectionCode, profile)
    });
  }

  function applyRecordTemplate(index: number, slotId: string) {
    const row = normalizedRows[index];
    const selectedSlotId = Number(slotId);
    const slot = recordTemplateSlots.find((item) => item.id === selectedSlotId && item.unit === row.unit);
    if (!slot) {
      return;
    }

    updateRow(index, {
      record_text: slot.record_text
    });
  }

  return (
    <div className="editor-block">
      <div className="editor-toolbar">
        <div className="editor-toolbar-main">
          <p className="eyebrow">{technical ? "技术测评表" : "管理测评表"}</p>
          <h3>{tableTitle}</h3>
          <div className="editor-toolbar-meta">
            <span className="status-chip">测评对象 {objectCount}</span>
            <span className="status-chip">固定单元 {unitOrder.length}</span>
            <span className="status-chip">三类模板 {templateTypeCount}</span>
            <span className="status-chip">模板槽位 {templateSlotCount}</span>
            <span className="status-chip">证据 {evidenceImages.length}</span>
          </div>
        </div>
        <div className="toolbar-actions">
          <div className="save-action-row">
            {isDirty ? <span className="dirty-chip">有未保存修改</span> : <span className="clean-chip">已保存</span>}
            <button type="button" onClick={onSave} disabled={isSaving || !isDirty}>
              {isSaving ? "保存中..." : "保存"}
            </button>
          </div>
          {technical ? (
            <>
              <div className="technical-filter-controls">
                <label>
                  <span>测评单元筛选</span>
                  <select
                    value={activeUnitFilter}
                    onChange={(event) => {
                      setTechnicalUnitFilter(event.target.value);
                      setTechnicalObjectFilter("");
                    }}
                  >
                    <option value="">全部测评单元</option>
                    {unitFilterOptions.map((unit) => (
                      <option key={unit} value={unit}>
                        {unit}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>测评对象筛选</span>
                  <select
                    value={activeObjectFilter}
                    onChange={(event) => setTechnicalObjectFilter(event.target.value)}
                    disabled={objectFilterOptions.length === 0}
                  >
                    <option value="">全部测评对象</option>
                    {objectFilterOptions.map((objectName) => (
                      <option key={objectName} value={objectName}>
                        {objectName}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <div className={`technical-entry-row${sectionSupportsSubsystem ? " with-subsystem" : ""}`}>
                {sectionSupportsSubsystem ? (
                  <div className="subsystem-controls">
                    <label>
                      <span>所属子系统</span>
                      <input
                        value={newSubsystemName}
                        onChange={(event) => setNewSubsystemName(event.target.value)}
                        onKeyDown={(event) => {
                          if (event.key === "Enter") {
                            event.preventDefault();
                            addSubsystem();
                          }
                        }}
                        placeholder="例如：核心业务系统"
                      />
                    </label>
                    <button type="button" onClick={addSubsystem} disabled={isSaving || !newSubsystemName.trim()}>
                      新增子系统
                    </button>
                    <label className="subsystem-filter">
                      <span>子系统筛选</span>
                      <select value={activeSubsystemName} onChange={(event) => setActiveSubsystemForSection(event.target.value)}>
                        <option value="">全部子系统</option>
                        {subsystemNames.map((subsystemName) => (
                          <option key={subsystemName} value={subsystemName}>
                            {subsystemName}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                ) : null}
                <div className={`technical-object-add toolbar-object-add${technicalObjectCategoryOptions.length > 0 ? " categorized-object-add" : ""}`}>
                {technicalObjectCategoryOptions.length > 0 ? (
                  <label>
                    <span>对象分类</span>
                    <select
                      value={technicalObjectCategory}
                      onChange={(event) => setTechnicalObjectCategory(event.target.value as TechnicalObjectCategoryValue)}
                    >
                      {technicalObjectCategoryOptions.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>
                ) : null}
                <label>
                  <span>测评对象</span>
                  <input
                    value={technicalObjectName}
                    onChange={(event) => setTechnicalObjectName(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault();
                        addTechnicalSectionObject();
                      }
                    }}
                    placeholder={technicalObjectPlaceholder}
                  />
                </label>
                <button type="button" onClick={addTechnicalSectionObject} disabled={technicalObjectAddDisabled}>
                  新增对象
                </button>
                </div>
              </div>
            </>
          ) : null}
        </div>
      </div>

      {technical ? (
        <div className="technical-object-toolbar">
          {sectionSupportsSubsystem ? (
            <div className="subsystem-list" aria-label="已添加子系统">
              {subsystemNames.length > 0 ? (
                subsystemNames.map((subsystemName) => (
                  <span className={`subsystem-item${activeSubsystemName === subsystemName ? " active" : ""}`} key={subsystemName}>
                    <button type="button" onClick={() => setActiveSubsystemForSection(subsystemName)}>
                      {subsystemName}
                    </button>
                    <button type="button" className="danger-button object-delete-button" onClick={() => removeSubsystem(subsystemName)}>
                      删除子系统
                    </button>
                  </span>
                ))
              ) : (
                <p className="technical-object-empty">请先新增子系统，再录入测评对象。</p>
              )}
            </div>
          ) : null}
          {sectionSupportsSubsystem && unassignedTechnicalObjectNames.length > 0 ? (
            <div className="unassigned-object-panel">
              <div className="unassigned-object-heading">
                <strong>未归属测评对象</strong>
                {activeSubsystemName ? (
                  <button type="button" className="secondary-button" onClick={() => assignUnassignedObjectsToSubsystem()}>
                    全部归入当前子系统
                  </button>
                ) : (
                  <span>选择子系统后可归属</span>
                )}
              </div>
              <div className="unassigned-object-list" aria-label="未归属测评对象">
                {unassignedTechnicalObjectNames.map((objectName) => (
                  <span className="technical-object-item unassigned-object-item" key={objectName}>
                    <span>{objectName}</span>
                    <button
                      type="button"
                      className="secondary-button object-assign-button"
                      disabled={!activeSubsystemName}
                      onClick={() => assignUnassignedObjectsToSubsystem(objectName)}
                    >
                      归入当前子系统
                    </button>
                  </span>
                ))}
              </div>
            </div>
          ) : null}
          {technicalObjectEntries.length > 0 ? (
            <div className="technical-object-list" aria-label="已添加测评对象">
              {technicalObjectEntries.map(({ objectName, objectSubsystemNames }) => {
                const targetSubsystemName = activeSubsystemName || (objectSubsystemNames.length === 1 ? objectSubsystemNames[0] : "");
                return (
                  <span className="technical-object-item" key={objectName}>
                    <span>{objectName}</span>
                    {sectionSupportsSubsystem && targetSubsystemName ? (
                      <button
                        type="button"
                        className="secondary-button object-assign-button"
                        onClick={() => removeObjectFromActiveSubsystem(objectName, targetSubsystemName)}
                      >
                        移出当前子系统
                      </button>
                    ) : null}
                    <button type="button" className="danger-button object-delete-button" onClick={() => removeTechnicalSectionObject(objectName)}>
                      删除对象
                    </button>
                  </span>
                );
              })}
            </div>
          ) : (
            <p className="technical-object-empty">{technicalObjectEmptyText}</p>
          )}
        </div>
      ) : null}
      {unitOrder.length === 0 ? (
        <div className="empty-table">
          <p>当前章节还没有可用的固定测评单元，请确认结果记录模板是否已加载。</p>
        </div>
      ) : (
        <div className="table-scroll">
          <table className={`assessment-table ${technical ? "technical-table" : "management-table"}`}>
            <colgroup>
              <col className="col-unit" />
              <col className="col-object" />
              <col className="col-record" />
              {technical ? (
                <>
                  <col className="col-metric" />
                  <col className="col-metric" />
                  <col className="col-metric" />
                  <col className="col-score" />
                  <col className="col-score" />
                </>
              ) : (
                <>
                  <col className="col-compliance" />
                  <col className="col-score" />
                </>
              )}
              <col className="col-action" />
            </colgroup>
            <thead>
              <tr>
                <th>测评单元</th>
                <th>测评对象</th>
                <th>结果记录</th>
                {technical ? (
                  <>
                    <th>D</th>
                    <th>A</th>
                    <th>K</th>
                    <th>对象评分</th>
                    <th>单元得分</th>
                  </>
                ) : (
                  <>
                    <th>符合情况</th>
                    <th>单元得分</th>
                  </>
                )}
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {groupedRows.map((group) =>
                group.entries.length === 0 ? (
                  <tr className="unit-empty-row" key={`${sectionCode}-${group.unit}-empty`}>
                    <td className="unit-cell fixed-unit-cell">
                      <div className="fixed-unit-content">
                        <strong>{group.unit}</strong>
                        <span>对象 0</span>
                        {canAddObjectWithinUnit(sectionCode, group.unit, technical) ? (
                          <button
                            type="button"
                            className="unit-add-button"
                            onClick={() => addRow(group.unit)}
                            disabled={sectionSupportsSubsystem && !activeSubsystemName}
                          >
                            新增对象
                          </button>
                        ) : null}
                      </div>
                    </td>
                    <td className="unit-empty-cell" colSpan={tableColumnCount - 1}>
                      {technical && !canAddObjectWithinUnit(sectionCode, group.unit, technical) ? "当前章节还没有为该单元新增测评对象。" : "当前测评单元还没有测评对象。"}
                    </td>
                  </tr>
                ) : (
                  group.entries.map(({ row, index }, entryIndex) => {
                    const templateSlots = templateSlotsForUnit(recordTemplateSlots, row.unit);
                    const templateOptionsCount = templateSlots.length;
                    const canAddWithinUnit = canAddObjectWithinUnit(sectionCode, group.unit, technical);
                    const showObjectDeleteLabel = technical && isSectionManagedTechnicalObjectUnit(sectionCode, row.unit);
                    return (
                      <tr key={`${sectionCode}-${group.unit}-${index}`}>
                        {entryIndex === 0 ? (
                          <td className="unit-cell fixed-unit-cell" rowSpan={group.entries.length}>
                            <div className="fixed-unit-content">
                              <strong>{group.unit}</strong>
                              <span>对象 {group.entries.length}</span>
                              {canAddWithinUnit ? (
                                <button
                                  type="button"
                                  className="unit-add-button"
                                  onClick={() => addRow(group.unit)}
                                  disabled={sectionSupportsSubsystem && !activeSubsystemName}
                                >
                                  新增对象
                                </button>
                              ) : null}
                            </div>
                          </td>
                        ) : null}
                        <td className="object-cell">
                          <textarea
                            value={row.object_name}
                            onChange={(event) => updateRow(index, { object_name: event.target.value })}
                            rows={2}
                          />
                        </td>
                        <td className="record-cell">
                          <div className="record-input-group">
                            <textarea
                              className="record-textarea"
                              value={displayRecordText(row, evidenceImages, sectionCode, profile)}
                              onChange={(event) => {
                                rememberRecordSelection(index, event.target);
                                const recordText = storedRecordText(event.target.value, row, evidenceImages, sectionCode, profile);
                                updateRow(index, {
                                  record_text: recordText,
                                  cross_references: crossReferencesForRecordText(recordText, row, evidenceImages, sectionCode, profile)
                                });
                              }}
                              onClick={(event) => rememberRecordSelection(index, event.currentTarget)}
                              onKeyUp={(event) => rememberRecordSelection(index, event.currentTarget)}
                              onSelect={(event) => rememberRecordSelection(index, event.currentTarget)}
                              rows={5}
                            />
                            <div className="record-control-row">
                              <select
                                className="record-template-select"
                                value=""
                                disabled={templateOptionsCount === 0}
                                title={templateOptionsCount === 0 ? "当前测评单元暂无可套用模板" : "选择三类结果记录模板"}
                                onChange={(event) => applyRecordTemplate(index, event.target.value)}
                              >
                                <option value="">
                                  {templateOptionsCount > 0 ? "套用模板" : "本单元暂无三类模板"}
                                </option>
                                {templateSlots.map((slot) => (
                                  <option key={slot.id} value={slot.id}>
                                    {templateSlotOptionLabel(slot)}
                                  </option>
                                ))}
                              </select>
                              <select
                                className="reference-select"
                                value=""
                                onChange={(event) => insertReferenceToken(index, event.target.value)}
                              >
                                <option value="">插入图片引用</option>
                                {evidenceImages.map((image) => (
                                  <option key={image.id} value={image.id}>
                                    {image.figure_label ?? `${sectionCode}-${image.sort_order}`} {image.caption || image.original_name}
                                  </option>
                                ))}
                              </select>
                            </div>
                          </div>
                        </td>
                        {technical ? (
                          <>
                            {(["d", "a", "k"] as const).map((key) => (
                              <td className="metric-cell" key={key}>
                                <select
                                  className="metric-select"
                                  value={row.metric_result?.[key] ?? ""}
                                  onChange={(event) => updateMetric(index, key, event.target.value)}
                                >
                                  <option value="">选择</option>
                                  {metricOptions.map((option) => (
                                    <option key={option} value={option}>
                                      {option}
                                    </option>
                                  ))}
                                </select>
                              </td>
                            ))}
                            <td className="score-cell">
                              <input
                                className="score-input"
                                value={row.metric_result?.object_score ?? ""}
                                onChange={(event) => updateMetric(index, "object_score", event.target.value)}
                                onBlur={() => formatObjectScore(index)}
                              />
                            </td>
                            {entryIndex === 0 ? (
                              <td className="score-cell unit-score-cell" rowSpan={group.entries.length}>
                                <output className="unit-score-output">{group.unitScore}</output>
                              </td>
                            ) : null}
                          </>
                        ) : (
                          <>
                            <td className="compliance-cell">
                              <select
                                className="compliance-select"
                                value={row.metric_result?.compliance ?? ""}
                                onChange={(event) => updateMetric(index, "compliance", event.target.value)}
                              >
                                <option value="">选择</option>
                                {complianceOptions.map((option) => (
                                  <option key={option} value={option}>
                                    {option}
                                  </option>
                                ))}
                              </select>
                            </td>
                            {entryIndex === 0 ? (
                              <td className="score-cell unit-score-cell" rowSpan={group.entries.length}>
                                <input
                                  className="score-input"
                                  value={group.unitScore}
                                  onChange={(event) => updateUnitScoreForUnit(group.unit, event.target.value)}
                                />
                              </td>
                            ) : null}
                          </>
                        )}
                        <td className="row-action-cell">
                          <button
                            type="button"
                            className="danger-button"
                            onClick={() => removeRow(index)}
                          >
                            {showObjectDeleteLabel ? "删除对象" : "删除"}
                          </button>
                        </td>
                      </tr>
                    );
                  })
                )
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function insertAtSelectionOrPlaceholder(text: string, token: string, selection?: TextSelection) {
  if (!text) {
    return token;
  }

  const range = insertionRange(text, selection);
  if (!range) {
    return `${text}${token}`;
  }

  return `${text.slice(0, range.start)}${token}${text.slice(range.end)}`;
}

function insertionRange(text: string, selection?: TextSelection): TextSelection | undefined {
  if (!selection) {
    const placeholderIndex = text.indexOf(FIGURE_PLACEHOLDER);
    if (placeholderIndex >= 0) {
      return {
        start: placeholderIndex,
        end: placeholderIndex + FIGURE_PLACEHOLDER.length
      };
    }
    return undefined;
  }

  const start = Math.max(0, Math.min(selection.start, text.length));
  const end = Math.max(start, Math.min(selection.end, text.length));
  const placeholderRange = placeholderAroundSelection(text, { start, end });

  return placeholderRange ?? { start, end };
}

function placeholderAroundSelection(text: string, selection: TextSelection): TextSelection | undefined {
  let index = text.indexOf(FIGURE_PLACEHOLDER);
  while (index >= 0) {
    const end = index + FIGURE_PLACEHOLDER.length;
    const caretInsidePlaceholder = selection.start === selection.end && selection.start >= index && selection.start <= end;
    const selectionTouchesPlaceholder = selection.start < end && selection.end > index;
    if (caretInsidePlaceholder || selectionTouchesPlaceholder) {
      return { start: index, end };
    }
    index = text.indexOf(FIGURE_PLACEHOLDER, end);
  }
  return undefined;
}
