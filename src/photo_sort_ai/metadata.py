from __future__ import annotations

import copy
import io
import json
import os
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
DC = "http://purl.org/dc/elements/1.1/"
XML = "http://www.w3.org/XML/1998/namespace"
EMBEDDABLE = {".dng", ".jpg", ".jpeg", ".tif", ".tiff"}
ET.register_namespace("x", X)
ET.register_namespace("rdf", RDF)
ET.register_namespace("xmp", XMP)
ET.register_namespace("dc", DC)


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


def read_sidecar_captions(path: Path) -> list[str]:
    if not path.exists():
        return []
    texts = []
    for element in parse_xmp(path).iter():
        attribute = element.get(f"{{{DC}}}description")
        if attribute is not None:
            texts.append(attribute)
        if element.tag == f"{{{DC}}}description":
            entries = element.findall(f".//{{{RDF}}}li")
            texts.extend((item.text or "") for item in entries) if entries else texts.append(element.text or "")
    return texts


def set_sidecar_caption(root: ET.Element, description: ET.Element, caption: str):
    translations = []
    for element in root.iter():
        element.attrib.pop(f"{{{DC}}}description", None)
        for child in list(element):
            if child.tag == f"{{{DC}}}description":
                translations.extend(copy.deepcopy(item) for item in child.findall(f".//{{{RDF}}}li")
                                    if item.get(f"{{{XML}}}lang") not in {None, "x-default"})
                element.remove(child)
    dc_description = ET.SubElement(description, f"{{{DC}}}description")
    alt = ET.SubElement(dc_description, f"{{{RDF}}}Alt")
    ET.SubElement(alt, f"{{{RDF}}}li", {f"{{{XML}}}lang": "x-default"}).text = caption
    alt.extend(translations)


class ExifTool:
    def __init__(self, executable: str, ignore_minor_errors: bool = False):
        self.executable = executable
        self.ignore_minor_errors = ignore_minor_errors

    def run(self, *args: str) -> bytes:
        flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        command, argument_input = [self.executable, *args], None
        if os.name == "nt":
            # Windows EXE command-line decoding can lose Japanese filenames.
            # ExifTool's UTF-8 argument file on stdin avoids the ANSI code page.
            if any("\n" in arg or "\r" in arg for arg in args):
                raise ValueError("ExifToolの引数に改行は使用できません。")
            command = [self.executable, "-charset", "filename=UTF8", "-@", "-"]
            argument_input = ("\n".join(args) + "\n").encode("utf-8")
        try:
            proc = subprocess.run(command, input=argument_input, capture_output=True,
                                  check=False, timeout=120, creationflags=flags)
        except FileNotFoundError as error:
            raise RuntimeError("ExifToolがありません。インストールするかexiftool_pathにexeの場所を設定してください。") from error
        if proc.returncode:
            raise RuntimeError(proc.stderr.decode("utf-8", errors="replace").strip() or "ExifToolの実行に失敗しました。")
        return proc.stdout

    def metadata(self, path: Path) -> dict:
        data = self.run("-j", "-n", "-charset", "filename=UTF8", "-XMP-xmp:Rating",
                        "-XMP-dc:Description*", "-IPTC:Caption-Abstract", "-Orientation", str(path.resolve()))
        return json.loads(data)[0]

    def preview(self, path: Path, tag: str) -> bytes:
        return self.run("-b", f"-{tag}", "-charset", "filename=UTF8", str(path.resolve()))

    def set_rating(self, path: Path, rating: int) -> None:
        self.set_metadata(path, rating)

    def set_metadata(self, path: Path, rating: int | None = None, caption: str | None = None) -> None:
        # -m only downgrades ExifTool "[minor]" errors to warnings; the written
        # rating is still verified below.
        minor = ("-m",) if self.ignore_minor_errors else ()
        with tempfile.TemporaryDirectory(prefix="photo-caption-") as temporary:
            args = [*minor, "-overwrite_original", "-P", "-charset", "filename=UTF8"]
            if rating is not None:
                args.append(f"-XMP-xmp:Rating={rating}")
            if caption is not None:
                text_file = Path(temporary) / "caption.txt"
                text_file.write_bytes(caption.encode("utf-8"))
                # File input allows literal multiline UTF-8 in Windows argument-file mode.
                args.append(f"-XMP-dc:Description-x-default<={text_file}")
            self.run(*args, str(path.resolve()))
        metadata = self.metadata(path)
        if rating is not None and metadata.get("Rating") != rating:
            raise RuntimeError("ExifTool書き込み後の星評価を確認できませんでした。")
        if caption is not None and metadata.get("Description") != caption:
            raise RuntimeError("ExifTool書き込み後のキャプションを確認できませんでした。")


class MetadataWriter:
    def __init__(self, config: Config):
        self.config = config
        self.exiftool = ExifTool(config.exiftool_path, config.exiftool_ignore_minor_errors)

    def effective_mode(self, path: Path) -> str:
        if self.config.output == "embedded" and path.suffix.lower() not in EMBEDDABLE:
            return "sidecar"
        return self.config.output

    def current_rating(self, path: Path, metadata: dict | None = None) -> int | None:
        if self.effective_mode(path) == "report":
            return None
        # Preserve embedded manual ratings too when producing a sidecar.
        embedded = None
        if path.suffix.lower() in EMBEDDABLE:
            embedded = (metadata if metadata is not None else self.exiftool.metadata(path)).get("Rating")
        sidecar = read_sidecar_rating(sidecar_path(path)) if self.effective_mode(path) == "sidecar" else None
        if embedded is not None and embedded != 0:
            return int(embedded)
        return sidecar if sidecar is not None else embedded

    def apply(self, path: Path, rating: int, caption: str | None = None) -> str:
        if type(rating) is not int or not 1 <= rating <= 5:
            raise ValueError("星は1〜5の整数である必要があります。")
        mode = self.effective_mode(path)
        if mode == "report":
            return "report"
        metadata = self.exiftool.metadata(path) if path.suffix.lower() in EMBEDDABLE else {}
        current = self.current_rating(path, metadata)
        protect_rating = self.config.existing_policy == "preserve" and current is not None and current != 0
        if self.config.caption_policy == "off":
            caption = None
        if caption is not None:
            if not isinstance(caption, str):
                raise ValueError("キャプションは文字列にしてください。")
            from .captions import xml_text
            caption = xml_text(caption).strip() or None
        existing_captions = [str(value) for key, value in metadata.items()
                             if key.startswith("Description") or key == "Caption-Abstract"]
        if mode == "sidecar" and caption is not None:
            existing_captions += read_sidecar_captions(sidecar_path(path))
        if caption is not None and self.config.caption_policy == "preserve" and any(t.strip() for t in existing_captions):
            caption = None
        if caption is not None and mode == "embedded" and metadata.get("Description") == caption:
            caption = None
        if protect_rating and caption is None:
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
            self.exiftool.set_metadata(target, None if protect_rating else rating, caption)
            return "caption" if protect_rating else "written"
        root = parse_xmp(target) if target.exists() else ET.Element(f"{{{X}}}xmpmeta")
        rdf = root if root.tag == f"{{{RDF}}}RDF" else root.find(f".//{{{RDF}}}RDF")
        if rdf is None:
            rdf = ET.SubElement(root, f"{{{RDF}}}RDF")
        description = rdf.find(f"{{{RDF}}}Description")
        if description is None:
            description = ET.SubElement(rdf, f"{{{RDF}}}Description", {f"{{{RDF}}}about": ""})
        # Remove both attribute and element representations to avoid conflicting values.
        if not protect_rating:
            for element in root.iter():
                element.attrib.pop(f"{{{XMP}}}Rating", None)
                for child in list(element):
                    if child.tag == f"{{{XMP}}}Rating":
                        element.remove(child)
            description.set(f"{{{XMP}}}Rating", str(rating))
        if caption is not None:
            set_sidecar_caption(root, description, caption)
        with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".xmp.tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(ET.tostring(root, encoding="utf-8", xml_declaration=True))
        try:
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        if not protect_rating and read_sidecar_rating(target) != rating:
            raise RuntimeError("XMP書き込み結果を確認できませんでした。")
        if caption is not None and caption not in read_sidecar_captions(target):
            raise RuntimeError("XMPキャプションの書き込み結果を確認できませんでした。")
        return "caption" if protect_rating else "sidecar"
