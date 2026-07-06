import { ChangeEvent, DragEvent, useEffect, useState } from "react";
import {
  deleteEvidenceImage,
  replaceEvidenceImageFile,
  reorderEvidenceImages,
  resolveFileUrl,
  updateEvidenceImage,
  uploadEvidenceImages,
  type EvidenceImage
} from "../api/client";

type EvidencePanelProps = {
  projectId: number;
  sectionCode: string;
  images: EvidenceImage[];
  visibleImageIds?: number[];
  filterActive?: boolean;
  onImagesChange: (images: EvidenceImage[]) => void;
  onError: (message: string) => void;
};

type ImageDraft = {
  caption: string;
};

function isAltTextWarning(warning: string): boolean {
  return warning.toLowerCase().includes("alt");
}

function removeAndReindexProjectImageNumbers(images: EvidenceImage[], deletedImage: EvidenceImage): EvidenceImage[] {
  const deletedProjectImageNo = deletedImage.project_image_no;
  return images
    .filter((item) => item.id !== deletedImage.id)
    .map((item) => {
      if (
        typeof deletedProjectImageNo !== "number" ||
        typeof item.project_image_no !== "number" ||
        item.project_image_no <= deletedProjectImageNo
      ) {
        return item;
      }
      return { ...item, project_image_no: item.project_image_no - 1 };
    });
}

export function EvidencePanel({
  projectId,
  sectionCode,
  images,
  visibleImageIds = [],
  filterActive = false,
  onImagesChange,
  onError
}: EvidencePanelProps) {
  const [files, setFiles] = useState<File[]>([]);
  const [fileInputKey, setFileInputKey] = useState(0);
  const [caption, setCaption] = useState("");
  const [drafts, setDrafts] = useState<Record<number, ImageDraft>>({});
  const [isUploading, setIsUploading] = useState(false);
  const [busyImageId, setBusyImageId] = useState<number | null>(null);
  const [draggingImageId, setDraggingImageId] = useState<number | null>(null);
  const [selectedPreviewImage, setSelectedPreviewImage] = useState<EvidenceImage | null>(null);
  const visibleImageIdSet = new Set(visibleImageIds);
  const displayImages = filterActive
    ? images.filter((image) => visibleImageIdSet.has(image.id))
    : images;
  const warningCount = displayImages.reduce(
    (count, image) => count + image.warnings.filter((warning) => !isAltTextWarning(warning)).length,
    0
  );

  useEffect(() => {
    setDrafts(
      Object.fromEntries(
        images.map((image) => [
          image.id,
          {
            caption: image.caption
          }
        ])
      )
    );
  }, [images]);

  useEffect(() => {
    if (!selectedPreviewImage) {
      return;
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setSelectedPreviewImage(null);
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [selectedPreviewImage]);

  useEffect(() => {
    if (selectedPreviewImage && !images.some((image) => image.id === selectedPreviewImage.id)) {
      setSelectedPreviewImage(null);
    }
  }, [images, selectedPreviewImage]);

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    setFiles(Array.from(event.target.files ?? []));
  }

  async function handleUpload() {
    if (files.length === 0) {
      onError("请选择至少一张 PNG 或 JPEG 图片。");
      return;
    }

    setIsUploading(true);
    try {
      const uploaded = await uploadEvidenceImages(projectId, {
        section_code: sectionCode,
        files,
        caption
      });
      onImagesChange([...images, ...uploaded]);
      setFiles([]);
      setFileInputKey((current) => current + 1);
      setCaption("");
    } catch (err) {
      onError(err instanceof Error ? err.message : "批量上传图片失败");
    } finally {
      setIsUploading(false);
    }
  }

  async function saveImage(image: EvidenceImage) {
    const draft = drafts[image.id];
    setBusyImageId(image.id);
    try {
      const updated = await updateEvidenceImage(image.id, {
        caption: draft?.caption ?? ""
      });
      onImagesChange(images.map((item) => (item.id === updated.id ? updated : item)));
    } catch (err) {
      onError(err instanceof Error ? err.message : "保存图片信息失败");
    } finally {
      setBusyImageId(null);
    }
  }

  async function removeImage(image: EvidenceImage) {
    setBusyImageId(image.id);
    try {
      await deleteEvidenceImage(image.id);
      onImagesChange(removeAndReindexProjectImageNumbers(images, image));
    } catch (err) {
      onError(err instanceof Error ? err.message : "删除图片失败");
    } finally {
      setBusyImageId(null);
    }
  }

  async function replaceImage(image: EvidenceImage, event: ChangeEvent<HTMLInputElement>) {
    const replacement = event.target.files?.[0];
    event.target.value = "";
    if (!replacement) {
      return;
    }

    setBusyImageId(image.id);
    try {
      const updated = await replaceEvidenceImageFile(image.id, replacement);
      onImagesChange(images.map((item) => (item.id === updated.id ? updated : item)));
    } catch (err) {
      onError(err instanceof Error ? err.message : "替换图片失败");
    } finally {
      setBusyImageId(null);
    }
  }

  async function persistImageOrder(nextImages: EvidenceImage[]) {
    const reordered = await reorderEvidenceImages(
      projectId,
      sectionCode,
      nextImages.map((item) => item.id)
    );
    onImagesChange(reordered);
  }

  async function moveImage(image: EvidenceImage, direction: -1 | 1) {
    if (filterActive) {
      return;
    }
    const index = images.findIndex((item) => item.id === image.id);
    const targetIndex = index + direction;
    if (targetIndex < 0 || targetIndex >= images.length) {
      return;
    }

    const next = [...images];
    [next[index], next[targetIndex]] = [next[targetIndex], next[index]];
    try {
      await persistImageOrder(next);
    } catch (err) {
      onError(err instanceof Error ? err.message : "排序失败");
    }
  }

  async function handleDrop(event: DragEvent<HTMLElement>, targetImage: EvidenceImage) {
    event.preventDefault();
    if (filterActive) {
      setDraggingImageId(null);
      return;
    }
    if (!draggingImageId || draggingImageId === targetImage.id) {
      setDraggingImageId(null);
      return;
    }

    const sourceIndex = images.findIndex((item) => item.id === draggingImageId);
    const targetIndex = images.findIndex((item) => item.id === targetImage.id);
    if (sourceIndex < 0 || targetIndex < 0) {
      setDraggingImageId(null);
      return;
    }

    const next = [...images];
    const [dragged] = next.splice(sourceIndex, 1);
    next.splice(targetIndex, 0, dragged);

    setDraggingImageId(null);
    try {
      await persistImageOrder(next);
    } catch (err) {
      onError(err instanceof Error ? err.message : "排序失败");
    }
  }

  function updateDraft(imageId: number, patch: Partial<ImageDraft>) {
    setDrafts((current) => ({
      ...current,
      [imageId]: {
        caption: current[imageId]?.caption ?? "",
        ...patch
      }
    }));
  }

  return (
    <div className="evidence-panel">
      <div className="editor-toolbar">
        <div className="editor-toolbar-main">
          <p className="eyebrow">证据图片</p>
          <h3>上传、题注与排序</h3>
          <div className="editor-toolbar-meta">
            <span className="status-chip">图片 {displayImages.length}</span>
            {filterActive ? <span className="status-chip">共 {images.length}</span> : null}
            <span className={warningCount > 0 ? "dirty-chip" : "clean-chip"}>提示 {warningCount}</span>
          </div>
        </div>
      </div>

      <div className="upload-panel">
        <label className="upload-field">
          <span>图片文件</span>
          <input key={fileInputKey} type="file" accept="image/png,image/jpeg" multiple onChange={handleFileChange} />
        </label>
        <label className="upload-field">
          <span>题注</span>
          <input value={caption} onChange={(event) => setCaption(event.target.value)} placeholder="例如：机房门禁照片，可上传后逐张调整" />
        </label>
        <div className="upload-actions">
          {files.length > 0 ? <span className="status-chip">{selectedFileSummary(files)}</span> : null}
          <button type="button" onClick={handleUpload} disabled={isUploading || files.length === 0}>
            {isUploading ? "上传中..." : uploadButtonText(files.length)}
          </button>
        </div>
      </div>

      {displayImages.length === 0 ? (
        <p className="evidence-empty">
          {filterActive ? "当前筛选条件下没有被引用的证据图片。" : "当前章节还没有证据图片。"}
        </p>
      ) : (
        <div className="evidence-grid">
          {displayImages.map((image, index) => {
            const draft = drafts[image.id] ?? { caption: image.caption };
            const visibleWarnings = image.warnings.filter((warning) => !isAltTextWarning(warning));
            const globalIndex = images.findIndex((item) => item.id === image.id);
            return (
              <article
                className={`evidence-card${draggingImageId === image.id ? " dragging" : ""}`}
                draggable={!filterActive}
                key={image.id}
                onDragStart={() => setDraggingImageId(image.id)}
                onDragOver={(event) => event.preventDefault()}
                onDrop={(event) => handleDrop(event, image)}
                onDragEnd={() => setDraggingImageId(null)}
              >
                <div className="evidence-card-header">
                  <div>
                    <strong>{image.figure_label ?? `${sectionCode}-${index + 1}`}</strong>
                    <span>{image.original_name}</span>
                  </div>
                  <span className="status-chip">项目图片 {image.project_image_no ?? globalIndex + 1}</span>
                </div>
                <div className="image-preview">
                  <button
                    type="button"
                    className="image-preview-button"
                    aria-label={`放大预览 ${image.figure_label ?? image.original_name}`}
                    onClick={() => setSelectedPreviewImage(image)}
                  >
                    <img
                      src={resolveFileUrl(image.file_url)}
                      alt={image.alt_text || image.caption || image.original_name}
                      draggable={false}
                    />
                  </button>
                </div>
                <dl className="image-meta">
                  <div>
                    <dt>尺寸</dt>
                    <dd>{image.pixel_width} x {image.pixel_height}px</dd>
                  </div>
                  <div>
                    <dt>DPI</dt>
                    <dd>{image.dpi_x ?? "未知"} / {image.dpi_y ?? "未知"}</dd>
                  </div>
                  <div>
                    <dt>显示</dt>
                    <dd>{image.display_width_in ?? "-"}in x {image.display_height_in ?? "-"}in</dd>
                  </div>
                </dl>
                <div className="quality-row">
                  {visibleWarnings.length > 0 ? (
                    visibleWarnings.map((warning) => (
                      <span className="dirty-chip" key={warning}>{warning}</span>
                    ))
                  ) : (
                    <span className="clean-chip">质量正常</span>
                  )}
                </div>
                <div className="image-edit-grid">
                  <label>
                    题注
                    <textarea
                      value={draft.caption}
                      onChange={(event) => updateDraft(image.id, { caption: event.target.value })}
                      rows={2}
                    />
                  </label>
                </div>
                <div className="image-actions">
                  <button type="button" onClick={() => moveImage(image, -1)} disabled={filterActive || index === 0}>
                    上移
                  </button>
                  <button type="button" onClick={() => moveImage(image, 1)} disabled={filterActive || index === displayImages.length - 1}>
                    下移
                  </button>
                  <button type="button" onClick={() => saveImage(image)} disabled={busyImageId === image.id}>
                    保存
                  </button>
                  <label className={`replace-file-button${busyImageId === image.id ? " disabled" : ""}`}>
                    替换图片
                    <input
                      type="file"
                      accept="image/png,image/jpeg"
                      disabled={busyImageId === image.id}
                      onChange={(event) => replaceImage(image, event)}
                    />
                  </label>
                  <button type="button" className="danger-button" onClick={() => removeImage(image)} disabled={busyImageId === image.id}>
                    删除
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      )}
      {selectedPreviewImage ? (
        <div
          className="image-lightbox"
          role="dialog"
          aria-modal="true"
          aria-label={`${selectedPreviewImage.figure_label ?? selectedPreviewImage.original_name} 全屏预览`}
          onClick={() => setSelectedPreviewImage(null)}
        >
          <div className="image-lightbox-content" onClick={(event) => event.stopPropagation()}>
            <div className="image-lightbox-header">
              <div>
                <strong>{selectedPreviewImage.figure_label ?? "证据图片"}</strong>
                <span>{selectedPreviewImage.original_name}</span>
              </div>
              <button type="button" className="image-lightbox-close" onClick={() => setSelectedPreviewImage(null)}>
                关闭
              </button>
            </div>
            <div className="image-lightbox-image-shell">
              <img
                className="image-lightbox-image"
                src={resolveFileUrl(selectedPreviewImage.file_url)}
                alt={selectedPreviewImage.alt_text || selectedPreviewImage.caption || selectedPreviewImage.original_name}
              />
            </div>
            {selectedPreviewImage.caption ? (
              <p className="image-lightbox-caption">{selectedPreviewImage.caption}</p>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function selectedFileSummary(files: File[]): string {
  if (files.length === 1) {
    return files[0].name;
  }
  return `${files.length} 张图片：${files[0].name} 等`;
}

function uploadButtonText(count: number): string {
  if (count <= 1) {
    return "上传图片";
  }
  return `上传 ${count} 张图片`;
}
