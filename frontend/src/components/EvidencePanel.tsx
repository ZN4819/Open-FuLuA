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
  alt_text: string;
};

export function EvidencePanel({ projectId, sectionCode, images, onImagesChange, onError }: EvidencePanelProps) {
  const [file, setFile] = useState<File | null>(null);
  const [caption, setCaption] = useState("");
  const [altText, setAltText] = useState("");
  const [drafts, setDrafts] = useState<Record<number, ImageDraft>>({});
  const [isUploading, setIsUploading] = useState(false);
  const [busyImageId, setBusyImageId] = useState<number | null>(null);
  const [draggingImageId, setDraggingImageId] = useState<number | null>(null);

  useEffect(() => {
    setDrafts(
      Object.fromEntries(
        images.map((image) => [
          image.id,
          {
            caption: image.caption,
            alt_text: image.alt_text
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
        caption,
        alt_text: altText
      });
      onImagesChange([...images, uploaded]);
      setFile(null);
      setCaption("");
      setAltText("");
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
        caption: draft?.caption ?? "",
        alt_text: draft?.alt_text ?? ""
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
        alt_text: current[imageId]?.alt_text ?? "",
        ...patch
      }
    }));
  }

  return (
    <div className="evidence-panel">
      <div className="editor-toolbar">
        <div>
          <p className="eyebrow">证据图片</p>
          <h3>上传、题注与排序</h3>
        </div>
      </div>

      <div className="upload-panel">
        <input type="file" accept="image/png,image/jpeg" onChange={handleFileChange} />
        <input value={caption} onChange={(event) => setCaption(event.target.value)} placeholder="题注" />
        <input value={altText} onChange={(event) => setAltText(event.target.value)} placeholder="alt 文本" />
        <button type="button" onClick={handleUpload} disabled={isUploading}>
          {isUploading ? "上传中..." : "上传图片"}
        </button>
      </div>

      {images.length === 0 ? (
        <p className="empty-sidebar">当前章节还没有证据图片。</p>
      ) : (
        <div className="evidence-grid">
          {images.map((image, index) => {
            const draft = drafts[image.id] ?? { caption: image.caption, alt_text: image.alt_text };
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
                <div className="image-preview">
                  <img src={resolveFileUrl(image.file_url)} alt={image.alt_text || image.caption || image.original_name} />
                </div>
                <div className="image-meta">
                  <strong>{image.figure_label ?? `${sectionCode}-${index + 1}`}</strong>
                  <span>{image.pixel_width} x {image.pixel_height}px</span>
                  <span>DPI: {image.dpi_x ?? "未知"} / {image.dpi_y ?? "未知"}</span>
                  <span>显示: {image.display_width_in ?? "-"}in x {image.display_height_in ?? "-"}in</span>
                </div>
                {image.warnings.length > 0 ? (
                  <ul className="warning-list">
                    {image.warnings.map((warning) => (
                      <li key={warning}>{warning}</li>
                    ))}
                  </ul>
                ) : null}
                <label>
                  题注
                  <textarea
                    value={draft.caption}
                    onChange={(event) => updateDraft(image.id, { caption: event.target.value })}
                    rows={2}
                  />
                </label>
                <label>
                  alt 文本
                  <textarea
                    value={draft.alt_text}
                    onChange={(event) => updateDraft(image.id, { alt_text: event.target.value })}
                    rows={2}
                  />
                </label>
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
