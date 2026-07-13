export const UNSAVED_EXPORT_MESSAGE = "当前还有未保存的章节，请先保存后再导出。";

export function scoreWorkbookExportBlockReason(dirtySectionCount: number): string | undefined {
  return dirtySectionCount > 0 ? UNSAVED_EXPORT_MESSAGE : undefined;
}
