import { useEffect, useLayoutEffect, useRef, useState, type ClipboardEvent, type FormEvent, type MouseEvent } from "react";

import { resolveFileUrl, type AssessmentRowInput, type CrossReferenceInput, type EvidenceImage, type RecordTemplateSlot, type RecordTemplateSlotGroup, type TemplateProfile } from "../api/client";

export type SubsystemUiState = {
  manualSubsystemNames: string[];
  activeSubsystem: string;
};

export type EvidenceImageFilterState = {
  active: boolean;
  imageIds: number[];
};

type EvidenceUploadOptions = {
  caption?: string;
};

type PendingPastedImageUpload = {
  rowIndex: number;
  files: File[];
  caption: string;
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
  onUploadEvidenceImages?: (files: File[], options?: EvidenceUploadOptions) => Promise<EvidenceImage[]>;
  onRemoveUnusedImagesForRows?: (deletedRows: AssessmentRowInput[], remainingRows: AssessmentRowInput[]) => void | Promise<void>;
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
const VERIFICATION_MARKER = "测评验证记录：";
const SCORE_BASIS_MARKER = "测评对象评分计算依据：";
const RECORD_TEXTAREA_MIN_HEIGHT = 126;

type TextSelection = {
  start: number;
  end: number;
};

type FigureReferenceOption = {
  token: string;
  displayText: string;
  target_image_id?: number | null;
};

type FigureReferenceHoverItem = {
  displayText: string;
  image: EvidenceImage;
};

type FigureReferenceOverlayPart = {
  text: string;
  item?: FigureReferenceHoverItem;
};

type FigureReferenceHoverPreview = {
  label: string;
  image: EvidenceImage;
  top: number;
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
    const displayText = existing?.displayText ?? token;
    optionsByToken.set(token, {
      token,
      displayText: displayText || token,
      target_image_id: existing?.target_image_id ?? null
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
      const reference = optionsByToken.get(token);
      const resolvedTargetImageId = reference ? reference.target_image_id : null;
      references.push({
        target_image_id: resolvedTargetImageId,
        token,
        display_text: reference?.displayText ?? token
      });
    }
    match = tokenPattern.exec(recordText);
  }

  return references;
}

function recordFigureReferenceItems(
  row: AssessmentRowInput,
  evidenceImages: EvidenceImage[],
  sectionCode: string,
  profile: TemplateProfile
): FigureReferenceHoverItem[] {
  const imageById = new Map(evidenceImages.map((image) => [image.id, image]));
  return figureReferenceOptions(row, evidenceImages, sectionCode, profile)
    .map((reference) => {
      const image = typeof reference.target_image_id === "number" ? imageById.get(reference.target_image_id) : undefined;
      if (!image || !reference.displayText.trim()) {
        return null;
      }
      return {
        displayText: reference.displayText,
        image
      };
    })
    .filter((item): item is FigureReferenceHoverItem => item !== null);
}

function referenceLabelOccurrences(text: string, references: FigureReferenceHoverItem[]) {
  const occupiedRanges: Array<{ start: number; end: number }> = [];
  const occurrences: Array<{ start: number; end: number; item: FigureReferenceHoverItem }> = [];
  const sortedReferences = [...references].sort((first, second) => second.displayText.length - first.displayText.length);

  sortedReferences.forEach((item) => {
    let start = text.indexOf(item.displayText);
    while (start >= 0) {
      const end = start + item.displayText.length;
      const overlaps = occupiedRanges.some((range) => start < range.end && end > range.start);
      if (!overlaps) {
        occupiedRanges.push({ start, end });
        occurrences.push({ start, end, item });
      }
      start = text.indexOf(item.displayText, end);
    }
  });

  return occurrences.sort((first, second) => first.start - second.start);
}

function recordFigureReferenceParts(text: string, references: FigureReferenceHoverItem[]): FigureReferenceOverlayPart[] {
  const occurrences = referenceLabelOccurrences(text, references);
  if (occurrences.length === 0) {
    return [{ text }];
  }

  const parts: FigureReferenceOverlayPart[] = [];
  let cursor = 0;
  occurrences.forEach((occurrence) => {
    if (occurrence.start > cursor) {
      parts.push({ text: text.slice(cursor, occurrence.start) });
    }
    parts.push({
      text: text.slice(occurrence.start, occurrence.end),
      item: occurrence.item
    });
    cursor = occurrence.end;
  });
  if (cursor < text.length) {
    parts.push({ text: text.slice(cursor) });
  }
  return parts;
}

function renderRecordRichTextParts(text: string, references: FigureReferenceHoverItem[]) {
  return recordFigureReferenceParts(text, references);
}

function recordEditorPlainText(element: HTMLElement) {
  return (element.innerText ?? "")
    .replace(/\r\n/g, "\n")
    .replace(/\u00a0/g, " ")
    .replace(/\n$/, "");
}

function textSelectionFromContentEditable(element: HTMLElement): TextSelection | undefined {
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0) {
    return undefined;
  }
  const range = selection.getRangeAt(0);
  if (!element.contains(range.startContainer) || !element.contains(range.endContainer)) {
    return undefined;
  }

  const startRange = document.createRange();
  startRange.selectNodeContents(element);
  startRange.setEnd(range.startContainer, range.startOffset);
  const endRange = document.createRange();
  endRange.selectNodeContents(element);
  endRange.setEnd(range.endContainer, range.endOffset);
  return {
    start: startRange.toString().length,
    end: endRange.toString().length
  };
}

function textPositionAtOffset(element: HTMLElement, offset: number) {
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
  let remainingOffset = Math.max(0, offset);
  let lastTextNode: Text | null = null;
  let node = walker.nextNode();

  while (node) {
    const textNode = node as Text;
    lastTextNode = textNode;
    if (remainingOffset <= textNode.data.length) {
      return { node: textNode, offset: remainingOffset };
    }
    remainingOffset -= textNode.data.length;
    node = walker.nextNode();
  }

  if (lastTextNode) {
    return { node: lastTextNode, offset: lastTextNode.data.length };
  }
  return { node: element, offset: 0 };
}

function restoreContentEditableSelection(element: HTMLElement, selection: TextSelection) {
  const currentSelection = window.getSelection();
  if (!currentSelection) {
    return;
  }

  const range = document.createRange();
  const start = textPositionAtOffset(element, selection.start);
  const end = textPositionAtOffset(element, selection.end);
  range.setStart(start.node, start.offset);
  range.setEnd(end.node, end.offset);
  currentSelection.removeAllRanges();
  currentSelection.addRange(range);
}

function renderRecordRichEditorContent(
  element: HTMLElement,
  value: string,
  references: FigureReferenceHoverItem[]
) {
  const fragment = document.createDocumentFragment();
  const parts = renderRecordRichTextParts(value, references);

  parts.forEach((part, partIndex) => {
    if (!part.item) {
      fragment.append(document.createTextNode(part.text));
      return;
    }

    const span = document.createElement("span");
    span.className = "record-reference-token";
    span.dataset.referenceLabel = part.item.displayText;
    span.dataset.referenceImageId = String(part.item.image.id);
    span.dataset.referencePartIndex = String(partIndex);
    span.textContent = part.text;
    fragment.append(span);
  });

  element.replaceChildren(fragment);
}

function normalizeRecordReferenceTokenText(element: HTMLElement) {
  const selection = document.activeElement === element ? textSelectionFromContentEditable(element) : undefined;
  element.querySelectorAll<HTMLElement>(".record-reference-token").forEach((token) => {
    const referenceLabel = token.dataset.referenceLabel ?? "";
    const tokenText = token.textContent ?? "";
    if (!referenceLabel || tokenText === referenceLabel || !tokenText.startsWith(referenceLabel)) {
      return;
    }

    const suffix = tokenText.slice(referenceLabel.length);
    token.textContent = referenceLabel;
    if (suffix) {
      token.after(document.createTextNode(suffix));
    }
  });

  if (selection && document.activeElement === element) {
    restoreContentEditableSelection(element, selection);
  }
}

type RecordRichEditorProps = {
  value: string;
  references: FigureReferenceHoverItem[];
  onEditorRef: (node: HTMLDivElement | null) => void;
  onInputChange: (displayText: string, target: HTMLElement) => void;
  onSelectionChange: (target: HTMLElement) => void;
  onMouseMove: (event: MouseEvent<HTMLElement>) => void;
  onMouseLeave: () => void;
  onPaste: (event: ClipboardEvent<HTMLElement>) => void;
};

function RecordRichEditor({
  value,
  references,
  onEditorRef,
  onInputChange,
  onSelectionChange,
  onMouseMove,
  onMouseLeave,
  onPaste
}: RecordRichEditorProps) {
  const editorRef = useRef<HTMLDivElement | null>(null);
  const isComposingRef = useRef(false);
  const referenceKey = references.map((item) => `${item.image.id}:${item.displayText}`).join("|");

  useLayoutEffect(() => {
    const editor = editorRef.current;
    if (!editor) {
      return;
    }

    const currentText = recordEditorPlainText(editor);
    if (isComposingRef.current) {
      return;
    }
    if (document.activeElement === editor && currentText === value) {
      return;
    }

    const selection = document.activeElement === editor ? textSelectionFromContentEditable(editor) : undefined;
    renderRecordRichEditorContent(editor, value, references);
    if (document.activeElement === editor && selection) {
      restoreContentEditableSelection(editor, selection);
    }
  }, [value, referenceKey]);

  function setEditorNode(node: HTMLDivElement | null) {
    editorRef.current = node;
    onEditorRef(node);
  }

  function handleInput(event: FormEvent<HTMLDivElement>) {
    const target = event.currentTarget;
    if (isComposingRef.current) {
      onSelectionChange(target);
      return;
    }
    normalizeRecordReferenceTokenText(target);
    onSelectionChange(target);
    onInputChange(recordEditorPlainText(target), target);
  }

  return (
    <div
      className="record-rich-editor"
      contentEditable
      suppressContentEditableWarning
      role="textbox"
      aria-multiline="true"
      data-placeholder="请输入结果记录"
      ref={setEditorNode}
      onInput={handleInput}
      onClick={(event) => onSelectionChange(event.currentTarget)}
      onKeyUp={(event) => onSelectionChange(event.currentTarget)}
      onMouseMove={onMouseMove}
      onMouseLeave={onMouseLeave}
      onSelect={(event) => onSelectionChange(event.currentTarget)}
      onPaste={onPaste}
      onCompositionStart={() => {
        isComposingRef.current = true;
      }}
      onCompositionEnd={(event) => {
        isComposingRef.current = false;
        normalizeRecordReferenceTokenText(event.currentTarget);
        onSelectionChange(event.currentTarget);
        onInputChange(recordEditorPlainText(event.currentTarget), event.currentTarget);
      }}
    />
  );
}

function figurePreviewTop(clientY: number) {
  const previewHeight = 480;
  const minTop = 76;
  const maxTop = Math.max(minTop, window.innerHeight - previewHeight - 16);
  return Math.min(Math.max(clientY - previewHeight / 2, minTop), maxTop);
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
    ...recordTemplateSlots.filter((slot) => slot.template_group === "verification_record").map((slot) => slot.unit),
    ...rows.map((row) => row.unit)
  ]);
}

const TEMPLATE_GROUP_ORDER: Record<RecordTemplateSlotGroup, number> = {
  verification_record: 0,
  score_basis: 1
};

const TEMPLATE_SLOT_ORDER: Record<RecordTemplateSlotGroup, Partial<Record<RecordTemplateSlot["template_type"], number>>> = {
  verification_record: {
    compliant: 0,
    non_compliant: 1,
    not_applicable: 2
  },
  score_basis: {
    fully_compliant: 0,
    score_adjusted: 1,
    non_compliant: 2
  }
};

function templateSlotOptionLabel(slot: RecordTemplateSlot) {
  const label = slot.template_type_label.trim();
  const title = slot.title.trim();
  if (title && label && title !== label) {
    return `${label} - ${title}`;
  }
  return title || label || "未命名模板";
}

function templateSlotSortValue(slot: RecordTemplateSlot) {
  return TEMPLATE_SLOT_ORDER[slot.template_group]?.[slot.template_type] ?? 99;
}

function templateSlotsForUnit(recordTemplateSlots: RecordTemplateSlot[], unit: string, templateGroup?: RecordTemplateSlotGroup) {
  return recordTemplateSlots
    .filter((slot) => slot.unit === unit && (!templateGroup || slot.template_group === templateGroup))
    .sort(
      (first, second) =>
        TEMPLATE_GROUP_ORDER[first.template_group] - TEMPLATE_GROUP_ORDER[second.template_group] ||
        templateSlotSortValue(first) - templateSlotSortValue(second)
    );
}

function templateSlotsForGroup(recordTemplateSlots: RecordTemplateSlot[], templateGroup: RecordTemplateSlotGroup) {
  return recordTemplateSlots
    .filter((slot) => slot.template_group === templateGroup)
    .sort((first, second) => templateSlotSortValue(first) - templateSlotSortValue(second));
}

function extractTemplateSectionText(slot: RecordTemplateSlot) {
  const text = slot.record_text.trim();
  if (slot.template_group === "verification_record") {
    const scoreIndex = text.indexOf(SCORE_BASIS_MARKER);
    const verificationText = scoreIndex >= 0 ? text.slice(0, scoreIndex).trim() : text;
    return verificationText || VERIFICATION_MARKER;
  }
  if (slot.template_group === "score_basis") {
    const scoreIndex = text.indexOf(SCORE_BASIS_MARKER);
    return scoreIndex >= 0 ? text.slice(scoreIndex).trim() : text;
  }
  return text;
}

function replaceRecordTemplateSection(recordText: string, sectionText: string, templateGroup: RecordTemplateSlotGroup) {
  const currentText = (recordText ?? "").trim();
  const nextSectionText = sectionText.trim();
  if (!nextSectionText) {
    return currentText;
  }
  const scoreIndex = currentText.indexOf(SCORE_BASIS_MARKER);
  if (templateGroup === "verification_record") {
    const scorePart = scoreIndex >= 0 ? currentText.slice(scoreIndex).trim() : "";
    return [nextSectionText, scorePart].filter(Boolean).join("\n");
  }
  const verificationPart = scoreIndex >= 0 ? currentText.slice(0, scoreIndex).trim() : currentText;
  return [verificationPart, nextSectionText].filter(Boolean).join("\n");
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
  onUploadEvidenceImages,
  onRemoveUnusedImagesForRows,
  onSave
}: AssessmentTableProps) {
  const technical = isTechnicalSection(sectionCode);
  const metricOptions = profile.content_controls.technical_metric.options;
  const complianceOptions = profile.content_controls.management_compliance.options;
  const recordSelections = useRef<Record<number, TextSelection>>({});
  const recordEditorRefs = useRef<Record<number, HTMLDivElement | null>>({});
  const [technicalObjectName, setTechnicalObjectName] = useState("");
  const [technicalObjectCategory, setTechnicalObjectCategory] = useState<TechnicalObjectCategoryValue>("user");
  const [technicalUnitFilter, setTechnicalUnitFilter] = useState("");
  const [technicalObjectFilter, setTechnicalObjectFilter] = useState("");
  const [newSubsystemName, setNewSubsystemName] = useState("");
  const [figureHoverPreview, setFigureHoverPreview] = useState<FigureReferenceHoverPreview | null>(null);
  const [inlineImageUploadIndex, setInlineImageUploadIndex] = useState<number | null>(null);
  const [pendingPastedImageUpload, setPendingPastedImageUpload] = useState<PendingPastedImageUpload | null>(null);
  const [showSubsystemList, setShowSubsystemList] = useState(false);
  const [showTechnicalObjectList, setShowTechnicalObjectList] = useState(false);
  useEffect(() => {
    setNewSubsystemName("");
    setTechnicalUnitFilter("");
    setTechnicalObjectFilter("");
    setFigureHoverPreview(null);
    setPendingPastedImageUpload(null);
    setShowSubsystemList(false);
    setShowTechnicalObjectList(false);
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
  const shouldShowTechnicalObjectList = technical;
  const templateSlotCount = recordTemplateSlots.length;
  const templateGroupCount = uniqueValues(recordTemplateSlots.map((slot) => slot.template_group)).length;
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

  function removeRowsAndCleanupImages(deletedRows: AssessmentRowInput[], nextRows: AssessmentRowInput[]) {
    recordSelections.current = {};
    onRowsChange(nextRows);
    onRemoveUnusedImagesForRows?.(deletedRows, nextRows);
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
    setManualSubsystemNamesForSection((current) => current.filter((item) => item.trim() !== subsystemName));
    if (activeSubsystemName === subsystemName) {
      setActiveSubsystemForSection("");
    }
    const removedRows = normalizedRows.filter((row) => row.subsystem?.trim() === subsystemName);
    const nextRows = normalizeRows(
      normalizedRows.filter((row) => row.subsystem?.trim() !== subsystemName),
      unitOrder,
      technical
    );
    removeRowsAndCleanupImages(removedRows, nextRows);
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
    const shouldRemoveRow = (row: AssessmentRowInput) =>
      row.object_name.trim() === objectName &&
      isSectionManagedTechnicalObjectUnit(sectionCode, row.unit) &&
      !(sectionSupportsSubsystem && activeSubsystemName && row.subsystem?.trim() !== activeSubsystemName);
    const removedRows = normalizedRows.filter(shouldRemoveRow);
    const nextRows = normalizeRows(
      normalizedRows.filter((row) => !shouldRemoveRow(row)),
      unitOrder,
      technical
    );
    removeRowsAndCleanupImages(removedRows, nextRows);
  }

  function removeRow(index: number) {
    const deletedRow = normalizedRows[index];
    const nextRows = normalizeRows(normalizedRows.filter((_, rowIndex) => rowIndex !== index), unitOrder, technical);
    if (deletedRow) {
      removeRowsAndCleanupImages([deletedRow], nextRows);
      return;
    }
    onRowsChange(nextRows);
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

  function rememberRecordSelection(index: number, target: HTMLElement) {
    const selection = textSelectionFromContentEditable(target);
    if (selection) {
      recordSelections.current[index] = selection;
    }
  }

  function updateRecordTextFromEditor(index: number, row: AssessmentRowInput, displayText: string, target: HTMLElement) {
    rememberRecordSelection(index, target);
    const recordText = storedRecordText(displayText, row, evidenceImages, sectionCode, profile);
    updateRow(index, {
      record_text: recordText,
      cross_references: crossReferencesForRecordText(recordText, row, evidenceImages, sectionCode, profile)
    });
  }

  function insertReferenceToken(index: number, imageIdValue: string) {
    const imageId = Number(imageIdValue);
    const image = evidenceImages.find((item) => item.id === imageId);
    if (!image) {
      return;
    }
    insertReferenceImages(index, [image], evidenceImages);
  }

  function insertReferenceImages(index: number, images: EvidenceImage[], availableImages: EvidenceImage[]) {
    if (images.length === 0) {
      return;
    }
    const row = normalizedRows[index];
    const displayText = images.map((image) => imageFigureDisplayText(image, sectionCode, profile)).join("、");
    const displayRecordTextValue = insertAtSelectionOrPlaceholder(
      displayRecordText(row, availableImages, sectionCode, profile),
      displayText,
      recordSelections.current[index]
    );
    const recordText = storedRecordText(displayRecordTextValue, row, availableImages, sectionCode, profile);
    updateRow(index, {
      record_text: recordText,
      cross_references: crossReferencesForRecordText(recordText, row, availableImages, sectionCode, profile)
    });
  }

  async function uploadInlineEvidenceImages(
    index: number,
    files: FileList | File[] | null,
    insertUploadedReferences = false,
    options?: EvidenceUploadOptions
  ) {
    const selectedFiles = Array.from(files ?? []);
    if (selectedFiles.length === 0 || !onUploadEvidenceImages) {
      return;
    }

    setInlineImageUploadIndex(index);
    try {
      const caption = options?.caption;
      const uploadedImages = caption !== undefined
        ? await onUploadEvidenceImages(selectedFiles, { caption })
        : await onUploadEvidenceImages(selectedFiles);
      if (insertUploadedReferences) {
        insertReferenceImages(index, uploadedImages, [...evidenceImages, ...uploadedImages]);
      }
    } finally {
      setInlineImageUploadIndex(null);
    }
  }

  async function uploadPastedEvidenceImages(index: number, event: ClipboardEvent<HTMLElement>) {
    const selectedFiles = clipboardImageFiles(event.clipboardData);
    if (selectedFiles.length === 0) {
      const pastedText = event.clipboardData.getData("text/plain");
      if (!pastedText) {
        return;
      }
      event.preventDefault();
      const row = normalizedRows[index];
      rememberRecordSelection(index, event.currentTarget);
      const displayRecordTextValue = insertAtSelectionOrPlaceholder(
        displayRecordText(row, evidenceImages, sectionCode, profile),
        pastedText,
        recordSelections.current[index]
      );
      const recordText = storedRecordText(displayRecordTextValue, row, evidenceImages, sectionCode, profile);
      updateRow(index, {
        record_text: recordText,
        cross_references: crossReferencesForRecordText(recordText, row, evidenceImages, sectionCode, profile)
      });
      return;
    }
    event.preventDefault();
    rememberRecordSelection(index, event.currentTarget);
    setPendingPastedImageUpload({
      rowIndex: index,
      files: selectedFiles,
      caption: ""
    });
  }

  function cancelPastedImageUpload() {
    setPendingPastedImageUpload(null);
  }

  async function confirmPastedImageUpload() {
    if (!pendingPastedImageUpload) {
      return;
    }
    const caption = pendingPastedImageUpload.caption.trim();
    if (!caption) {
      return;
    }
    try {
      await uploadInlineEvidenceImages(pendingPastedImageUpload.rowIndex, pendingPastedImageUpload.files, true, { caption });
      setPendingPastedImageUpload(null);
    } catch {
      // The parent page already surfaces the upload failure; keep the dialog open so the caption is not lost.
    }
  }

  function applyRecordTemplate(index: number, slotId: string) {
    const row = normalizedRows[index];
    const selectedSlotId = Number(slotId);
    const slot = recordTemplateSlots.find((item) => item.id === selectedSlotId);
    if (!slot || (slot.template_group === "verification_record" && slot.unit !== row.unit)) {
      return;
    }

    const recordText = replaceRecordTemplateSection(
      row.record_text,
      extractTemplateSectionText(slot),
      slot.template_group
    );
    updateRow(index, {
      record_text: recordText,
      cross_references: crossReferencesForRecordText(recordText, row, evidenceImages, sectionCode, profile)
    });
  }

  function handleRecordReferenceHover(event: MouseEvent<HTMLElement>, row: AssessmentRowInput) {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      setFigureHoverPreview(null);
      return;
    }
    const tokenElement = target.closest<HTMLElement>(".record-reference-token");
    if (!tokenElement || !event.currentTarget.contains(tokenElement)) {
      setFigureHoverPreview(null);
      return;
    }
    const label = tokenElement.dataset.referenceLabel ?? tokenElement.textContent?.trim() ?? "";
    const item = recordFigureReferenceItems(row, evidenceImages, sectionCode, profile)
      .find((reference) => reference.displayText === label);
    if (!item) {
      setFigureHoverPreview(null);
      return;
    }
    showFigureReferencePreview(item, event.clientY);
  }

  function showFigureReferencePreview(item: FigureReferenceHoverItem, clientY: number) {
    setFigureHoverPreview({
      label: item.displayText,
      image: item.image,
      top: figurePreviewTop(clientY)
    });
  }

  const pastedImageUploadDialog = pendingPastedImageUpload ? (
    <div className="pasted-image-upload-backdrop">
      <form
        className="pasted-image-upload-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="pasted-image-upload-title"
        onSubmit={(event) => {
          event.preventDefault();
          void confirmPastedImageUpload();
        }}
      >
        <div className="pasted-image-upload-heading">
          <p className="eyebrow">粘贴截图题注</p>
          <h4 id="pasted-image-upload-title">请输入图片题注</h4>
        </div>
        <label className="pasted-image-upload-field">
          <span>图片题注</span>
          <input
            autoFocus
            value={pendingPastedImageUpload.caption}
            onChange={(event) => setPendingPastedImageUpload({
              ...pendingPastedImageUpload,
              caption: event.target.value
            })}
            placeholder="例如：机房门禁截图"
          />
        </label>
        <p className="pasted-image-upload-hint">
          确认后将上传剪贴板截图，并在当前光标位置插入图片编号。
        </p>
        <div className="pasted-image-upload-actions">
          <button type="button" className="secondary-button" onClick={cancelPastedImageUpload}>
            取消
          </button>
          <button
            type="submit"
            className="primary-button"
            disabled={!pendingPastedImageUpload.caption.trim() || inlineImageUploadIndex !== null}
          >
            {inlineImageUploadIndex !== null ? "上传中..." : "上传并插入"}
          </button>
        </div>
      </form>
    </div>
  ) : null;

  return (
    <div className="editor-block editor-workspace-panel">
      {pastedImageUploadDialog}
      {figureHoverPreview ? (
        <aside className="figure-hover-preview" style={{ top: `${figureHoverPreview.top}px` }} aria-label="图片引用预览">
          <div className="figure-hover-preview-heading">
            <strong>{figureHoverPreview.label}</strong>
            <span>{figureHoverPreview.image.caption || figureHoverPreview.image.original_name}</span>
          </div>
          <div className="figure-hover-preview-image">
            {figureHoverPreview.image.file_url ? (
              <img
                src={resolveFileUrl(figureHoverPreview.image.file_url)}
                alt={figureHoverPreview.image.alt_text || figureHoverPreview.image.caption || figureHoverPreview.image.original_name}
              />
            ) : (
              <span>暂无图片文件</span>
            )}
          </div>
          <dl className="figure-hover-preview-meta">
            <div>
              <dt>尺寸</dt>
              <dd>{figureHoverPreview.image.pixel_width ?? "-"} x {figureHoverPreview.image.pixel_height ?? "-"}px</dd>
            </div>
            <div>
              <dt>DPI</dt>
              <dd>{figureHoverPreview.image.dpi_x ?? "未知"} / {figureHoverPreview.image.dpi_y ?? "未知"}</dd>
            </div>
          </dl>
        </aside>
      ) : null}
      <div className="editor-toolbar">
        <div className="editor-toolbar-main">
          <p className="eyebrow">{technical ? "技术测评表" : "管理测评表"}</p>
          <h3>{tableTitle}</h3>
          <div className="editor-toolbar-meta">
            <span className="status-chip">测评对象 {objectCount}</span>
            <span className="status-chip">固定单元 {unitOrder.length}</span>
            <span className="status-chip">分段模板 {templateGroupCount}</span>
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
            <>
              <div className="technical-list-toggle-row">
                <button
                  type="button"
                  className="secondary-button technical-list-toggle-button"
                  onClick={() => setShowSubsystemList((current) => !current)}
                >
                  {showSubsystemList ? "隐藏子系统清单" : `显示子系统清单（${subsystemNames.length}）`}
                </button>
              </div>
              {showSubsystemList ? (
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
            </>
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
          {shouldShowTechnicalObjectList ? (
            <div className="technical-list-toggle-row">
              <button
                type="button"
                className="secondary-button technical-list-toggle-button"
                onClick={() => setShowTechnicalObjectList((current) => !current)}
              >
                {showTechnicalObjectList ? "隐藏测评对象清单" : `显示测评对象清单（${technicalObjectEntries.length}）`}
              </button>
            </div>
          ) : null}
          {shouldShowTechnicalObjectList && showTechnicalObjectList && technicalObjectEntries.length > 0 ? (
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
          ) : null}
          {shouldShowTechnicalObjectList && showTechnicalObjectList && technicalObjectEntries.length === 0 ? (
            <p className="technical-object-empty">{technicalObjectEmptyText}</p>
          ) : null}
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
                    const verificationTemplateSlots = templateSlotsForUnit(recordTemplateSlots, row.unit, "verification_record");
                    const scoreBasisTemplateSlots = templateSlotsForGroup(recordTemplateSlots, "score_basis");
                    const canAddWithinUnit = canAddObjectWithinUnit(sectionCode, group.unit, technical);
                    const showObjectDeleteLabel = technical && isSectionManagedTechnicalObjectUnit(sectionCode, row.unit);
                    const recordDisplayText = displayRecordText(row, evidenceImages, sectionCode, profile);
                    const figureReferenceItems = recordFigureReferenceItems(row, evidenceImages, sectionCode, profile);
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
                            <div className="record-textarea-shell">
                              <RecordRichEditor
                                value={recordDisplayText}
                                references={figureReferenceItems}
                                onEditorRef={(node) => {
                                  recordEditorRefs.current[index] = node;
                                }}
                                onInputChange={(displayText, target) => updateRecordTextFromEditor(index, row, displayText, target)}
                                onSelectionChange={(target) => rememberRecordSelection(index, target)}
                                onMouseMove={(event) => handleRecordReferenceHover(event, row)}
                                onMouseLeave={() => setFigureHoverPreview(null)}
                                onPaste={(event) => void uploadPastedEvidenceImages(index, event)}
                              />
                            </div>
                            <div className="record-control-row">
                              <select
                                className="record-template-select"
                                value=""
                                disabled={verificationTemplateSlots.length === 0}
                                title={verificationTemplateSlots.length === 0 ? "当前测评单元暂无验证记录模板" : "选择测评验证记录模板"}
                                onChange={(event) => applyRecordTemplate(index, event.target.value)}
                              >
                                <option value="">
                                  {verificationTemplateSlots.length > 0 ? "套用验证记录模板" : "本单元暂无验证记录模板"}
                                </option>
                                {verificationTemplateSlots.map((slot) => (
                                  <option key={slot.id} value={slot.id}>
                                    {templateSlotOptionLabel(slot)}
                                  </option>
                                ))}
                              </select>
                              <select
                                className="record-template-select"
                                value=""
                                disabled={scoreBasisTemplateSlots.length === 0}
                                title={scoreBasisTemplateSlots.length === 0 ? "当前测评单元暂无评分依据模板" : "选择测评对象评分计算依据模板"}
                                onChange={(event) => applyRecordTemplate(index, event.target.value)}
                              >
                                <option value="">
                                  {scoreBasisTemplateSlots.length > 0 ? "套用评分依据模板" : "本单元暂无评分依据模板"}
                                </option>
                                {scoreBasisTemplateSlots.map((slot) => (
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
                              <label className="inline-image-upload-button">
                                <span>{inlineImageUploadIndex === index ? "上传中..." : "上传图片"}</span>
                                <input
                                  type="file"
                                  accept="image/png,image/jpeg"
                                  multiple
                                  disabled={!onUploadEvidenceImages || inlineImageUploadIndex !== null}
                                  onChange={(event) => {
                                    void uploadInlineEvidenceImages(index, event.target.files);
                                    event.currentTarget.value = "";
                                  }}
                                />
                              </label>
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

function clipboardImageFiles(clipboardData: DataTransfer) {
  const files: File[] = [];
  Array.from(clipboardData.items).forEach((item, index) => {
    if (item.kind !== "file" || !item.type.startsWith("image/")) {
      return;
    }
    const file = item.getAsFile();
    if (!file) {
      return;
    }
    const extension = item.type.includes("jpeg") ? "jpg" : "png";
    const filename = file.name || `clipboard-screenshot-${Date.now()}-${index + 1}.${extension}`;
    files.push(new File([file], filename, { type: file.type || item.type }));
  });
  return files;
}
