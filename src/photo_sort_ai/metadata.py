from __future__ import annotations

import io
import json
import shutil
import subprocess
import tempfile
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from .config import Config

XMP = "http://ns.adobe.com/xap/1.0/"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
X = "adobe:ns:meta/"
EMBEDDABLE = {".dng", ".jpg", ".jpeg", ".tif", ".tiff"}
ET.register_namespace("x", X)
ET.register_namespace("rdf", RDF)
ET.register_namespace("xmp", XMP)


def sidecar_path(path: Path) -> Path:
    # Traditional RAW sidecars use the stem; other types keep their suffix to
    # avoid DNG/JPEG pairs sharing one sidecar. Never claim DNG sidecars auto-import.
    return path.with_suffix(".xmp") if path.suffix.lower() not in EMBEDDABLE | {".png", ".webp"} else Path(str(path) + ".xmp")


def read_sidecar_rating(path: Path) -> int | None:
    if not path.is_file():
        return None
    root = parse_xmp(path)
    for element in root.iter():
        text = element.get(f"{{{XMP}}}Rating")
        if element.tag == f"{{{XMP}}}Rating":
            text = element.text
        if text is not None:
            return int(float(text))
    return None


def parse_xmp(path: Path) -> ET.Element:
    if path.stat().st_size > 10_000_000:
        raise ValueError(f"XMPが大きすぎます: {path}")
    data = path.read_bytes()
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("DTD・外部エンティティを含むXMPには対応しません。")
    for _, (prefix, uri) in ET.iterparse(io.BytesIO(data), events=("start-ns",)):
        if not prefix.startswith("ns"):
            ET.register_namespace(prefix, uri)
    return ET.fromstring(data)


class ExifTool:
    def __init__(self, executable: str):
        self.executable = executable

    def run(self, *args: str) -> bytes:
        flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        try:
            proc = subprocess.run([self.executable, *args], stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, timeout=120, creationflags=flags)
        except FileNotFoundError as error:
            raise RuntimeError("ExifToolがありません。インストールするかexiftool_pathにexeの場所を設定してください。") from error
        if proc.returncode:
            raise RuntimeError(proc.stderr.decode("utf-8", errors="replace").strip() or "ExifToolの実行に失敗しました。")
        return proc.stdout

    def metadata(self, path: Path) -> dict:
        data = self.run("-j", "-n", "-charset", "filename=UTF8", "-XMP-xmp:Rating", "-Orientation", str(path.resolve()))
        return json.loads(data)[0]

    def preview(self, path: Path, tag: str) -> bytes:
        return self.run("-b", f"-{tag}", "-charset", "filename=UTF8", str(path.resolve()))

    def set_rating(self, path: Path, rating: int) -> None:
        self.run("-overwrite_original", "-P", "-charset", "filename=UTF8",
                 f"-XMP-xmp:Rating={rating}", str(path.resolve()))
        if self.metadata(path).get("Rating") != rating:
            raise RuntimeError("ExifTool書き込み後の星評価を確認できませんでした。")


class MetadataWriter:
    def __init__(self, config: Config):
        self.config = config
        self.exiftool = ExifTool(config.exiftool_path)

    def effective_mode(self, path: Path) -> str:
        if self.config.output == "embedded" and path.suffix.lower() not in EMBEDDABLE:
            return "sidecar"
        return self.config.output

    def current_rating(self, path: Path) -> int | None:
        if self.effective_mode(path) == "report":
            return None
        # Preserve embedded manual ratings too when producing a sidecar.
        embedded = None
        if path.suffix.lower() in EMBEDDABLE:
            embedded = self.exiftool.metadata(path).get("Rating")
        sidecar = read_sidecar_rating(sidecar_path(path)) if self.effective_mode(path) == "sidecar" else None
        if embedded is not None and embedded != 0:
            return int(embedded)
        return sidecar if sidecar is not None else embedded

    def apply(self, path: Path, rating: int) -> str:
        if type(rating) is not int or not 1 <= rating <= 5:
            raise ValueError("星は1〜5の整数である必要があります。")
        mode = self.effective_mode(path)
        if mode == "report":
            return "report"
        current = self.current_rating(path)
        if self.config.existing_policy == "preserve" and current is not None and current != 0:
            return "protected"
        target = path if mode == "embedded" else sidecar_path(path)
        if self.config.backup and target.exists():
            directory = Path(self.config.state_dir) / "backups"
            directory.mkdir(parents=True, exist_ok=True)
            backup = directory / (uuid.uuid4().hex + target.suffix)
            shutil.copy2(target, backup)
            # Keep the original absolute path so a backup is reviewable/restorable.
            backup.with_suffix(backup.suffix + ".json").write_text(
                json.dumps({"original": str(target.resolve())}, ensure_ascii=False), encoding="utf-8")
        if mode == "embedded":
            self.exiftool.set_rating(target, rating)
            return "written"
        root = parse_xmp(target) if target.exists() else ET.Element(f"{{{X}}}xmpmeta")
        rdf = root if root.tag == f"{{{RDF}}}RDF" else root.find(f".//{{{RDF}}}RDF")
        if rdf is None:
            rdf = ET.SubElement(root, f"{{{RDF}}}RDF")
        description = rdf.find(f"{{{RDF}}}Description")
        if description is None:
            description = ET.SubElement(rdf, f"{{{RDF}}}Description", {f"{{{RDF}}}about": ""})
        # Remove both attribute and element representations to avoid conflicting values.
        for element in root.iter():
            element.attrib.pop(f"{{{XMP}}}Rating", None)
            for child in list(element):
                if child.tag == f"{{{XMP}}}Rating":
                    element.remove(child)
        description.set(f"{{{XMP}}}Rating", str(rating))
        with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".xmp.tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(ET.tostring(root, encoding="utf-8", xml_declaration=True))
        try:
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        if read_sidecar_rating(target) != rating:
            raise RuntimeError("XMP書き込み結果を確認できませんでした。")
        return "sidecar"
