from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET


REL_NS = {"rel": "http://schemas.openxmlformats.org/package/2006/relationships"}


class DocxImportPackageError(RuntimeError):
    """DOCX 导入包读取失败。"""


@dataclass(frozen=True)
class DocxPackageParts:
    path: Path
    document: ET.Element
    relationships: dict[str, str]
    media_paths: list[str]


def read_docx_package(path: str | Path) -> DocxPackageParts:
    docx_path = Path(path)
    if not docx_path.exists():
        raise DocxImportPackageError(f"DOCX 文件不存在：{docx_path}")
    if docx_path.suffix.lower() != ".docx":
        raise DocxImportPackageError("仅支持 .docx 文件导入。")
    if not zipfile.is_zipfile(docx_path):
        raise DocxImportPackageError("DOCX 文件损坏或不是有效的 Word OpenXML 包。")

    try:
        with zipfile.ZipFile(docx_path) as package:
            document = _read_required_xml(package, "word/document.xml")
            relationships = _read_relationships(package)
            media_paths = sorted(
                name for name in package.namelist()
                if name.startswith("word/media/") and not name.endswith("/")
            )
    except zipfile.BadZipFile as exc:
        raise DocxImportPackageError("DOCX 文件损坏或无法读取。") from exc

    return DocxPackageParts(
        path=docx_path,
        document=document,
        relationships=relationships,
        media_paths=media_paths,
    )


def _read_required_xml(package: zipfile.ZipFile, name: str) -> ET.Element:
    try:
        return ET.fromstring(package.read(name))
    except KeyError as exc:
        raise DocxImportPackageError(f"DOCX 缺少必要部件：{name}") from exc
    except ET.ParseError as exc:
        raise DocxImportPackageError(f"DOCX XML 无法解析：{name}") from exc


def _read_relationships(package: zipfile.ZipFile) -> dict[str, str]:
    try:
        rels = ET.fromstring(package.read("word/_rels/document.xml.rels"))
    except KeyError:
        return {}
    except ET.ParseError as exc:
        raise DocxImportPackageError("DOCX 关系文件无法解析：word/_rels/document.xml.rels") from exc

    relationships: dict[str, str] = {}
    for relationship in rels.findall("rel:Relationship", REL_NS):
        rel_id = relationship.get("Id")
        target = relationship.get("Target")
        if rel_id and target:
            relationships[rel_id] = target
    return relationships
