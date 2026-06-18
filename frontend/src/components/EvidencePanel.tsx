import { ChangeEvent, DragEvent, useEffect, useState } from "react";
import {
  deleteEvidenceImage,
  reorderEvidenceImages,
  resolveFileUrl,
  updateEvidenceImage,
  uploadEvidenceImage,
  type EvidenceImage
} from "../api/client";

type EvidencePanelProps = {
  projectId: number;
  sectionCode: string;
  images: EvidenceImage[];
  onImagesChange: (images: EvidenceImage[]) => void;
  onError: (message: string) => void;
};

type ImageDraft = {
  caption: string;
};

function isAltTextWarning(warning: string): boolean {
  return warning.toLowerCase().includes("alt");
}

export function EvidencePanel({ projectId, sectionCode, images, onImagesChange, onError }: EvidencePanelProps) {
  const [file, setFile] = useState<File | null>(null);
  const [caption, setCaption] = useState("");
  const [drafts, setDrafts] = useState<Record<number, ImageDraft>>({});
  const [isUploading, setIsUploading] = useState(false);
  const [busyImageId, setBusyImageId] = useState<number | null>(null);
  const [draggingImageId, setDraggingImageId] = useState<number | null>(null);
  const warningCount = images.reduce(
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

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    setFile(event.target.files?.[0] ?? null);
  }

  async function handleUpload() {
    if (!file) {
      onError("请选择一张 PNG 或 JPEG 图片。");
      return;
    }

    setIsUploading(true);
    try {
      const uploaded = await uploadEvidenceImage(projectId, {
        section_code: sectionCode,
        file,
        caption
      });
      onImagesChange([...images, uploaded]);
      setFile(null);
      setCaption("");
    } catch (err) {
      onError(err instanceof Error ? err.message : "上传图片失败");
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
      onImagesChange(images.filter((item) => item.id !== image.id));
    } catch (err) {
      onError(err instanceof Error ? err.message : "删除图片失败");
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
            <span className="status-chip">图片 {images.length}</span>
            <span className={warningCount > 0 ? "dirty-chip" : "clean-chip"}>提示 {warningCount}</span>
          </div>
        </div>
      </div>

      <div className="upload-panel">
        <label className="upload-field">
          <span>图片文件</span>
          <input type="file" accept="image/png,image/jpeg" onChange={handleFileChange} />
        </label>
        <label className="upload-field">
          <span>题注</span>
          <input value={caption} onChange={(event) => setCaption(event.target.value)} placeholder="例如：机房门禁照片" />
        </label>
        <div className="upload-actions">
          {file ? <span className="status-chip">{file.name}</span> : null}
          <button type="button" onClick={handleUpload} disabled={isUploading}>
            {isUploading ? "上传中..." : "上传图片"}
          </button>
        </div>
      </div>

      {images.length === 0 ? (
        <p className="evidence-empty">当前章节还没有证据图片。</p>
      ) : (
        <div className="evidence-grid">
          {images.map((image, index) => {
            const draft = drafts[image.id] ?? { caption: image.caption };
            const visibleWarnings = image.warnings.filter((warning) => !isAltTextWarning(warning));
            return (
              <article
                className={`evidence-card${draggingImageId === image.id ? " dragging" : ""}`}
                draggable
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
                  <span className="status-chip">排序 {index + 1}</span>
                </div>
                <div className="image-preview">
                  <img src={resolveFileUrl(image.file_url)} alt={image.alt_text || image.caption || image.original_name} />
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
                  <button type="button" onClick={() => moveImage(image, -1)} disabled={index === 0}>
                    上移
                  </button>
                  <button type="button" onClick={() => moveImage(image, 1)} disabled={index === images.length - 1}>
                    下移
                  </button>
                  <button type="button" onClick={() => saveImage(image)} disabled={busyImageId === image.id}>
                    保存
                  </button>
                  <button type="button" className="danger-button" onClick={() => removeImage(image)} disabled={busyImageId === image.id}>
                    删除
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}
