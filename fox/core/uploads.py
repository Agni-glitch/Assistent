"""Files attached in chat: documents become text for the model, images go to models that can see.

Uploads live in data/uploads, which tools can't read or write. Stored messages refer to an image by
its id rather than holding its bytes, and the bytes are put back only when the model is called.
"""

from __future__ import annotations

import base64
import io
import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage

from fox.config import Settings
from fox.core.storage import now
from fox.core.tooling import ToolError

UPLOAD_SCHEME = "fox-upload://"
UPLOAD_ID = re.compile(r"^[0-9a-f]{32}$")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
MAX_IMAGE_SIDE = 2048
MAX_ATTACHMENTS = 10


class UploadError(ValueError):
    pass


def uploads_dir(s: Settings) -> Path:
    return s.fox_data_dir / "uploads"


def safe_name(name: str) -> str:
    """A file name that's safe on disk and inside the tags the model reads."""
    name = Path(name.replace("\\", "/")).name
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*&]', "_", name).strip(" .")
    return name[:120] or "file"


def _kind(name: str) -> str:
    suffix = Path(name).suffix.lower()
    return "image" if suffix in IMAGE_SUFFIXES else "har" if suffix == ".har" else "document"


def _prepare_image(data: bytes) -> tuple[bytes, str, str]:
    """Check it really is an image, and shrink big ones so they don't cost a fortune in tokens."""
    from PIL import Image

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as exc:
        raise UploadError("This file isn't an image Fox can read.") from exc
    if max(image.size) <= MAX_IMAGE_SIDE and image.format in ("PNG", "JPEG", "WEBP", "GIF"):
        return data, f"image/{image.format.lower()}", image.format.lower().replace("jpeg", "jpg")
    image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    out = io.BytesIO()
    if image.mode in ("RGBA", "LA", "P"):
        image.save(out, "PNG", optimize=True)
        return out.getvalue(), "image/png", "png"
    image.convert("RGB").save(out, "JPEG", quality=85)
    return out.getvalue(), "image/jpeg", "jpg"


def save_upload(s: Settings, name: str, data: bytes) -> dict[str, Any]:
    from fox.tools.files import _read_text

    limit = int(s.fox_max_upload_mb * 1024 * 1024)
    if len(data) > limit:
        raise UploadError(f"Files can be up to {s.fox_max_upload_mb:g} MB.")
    if not data:
        raise UploadError("The file is empty.")
    name = safe_name(name)
    kind = _kind(name)
    upload_id = uuid.uuid4().hex
    folder = uploads_dir(s) / upload_id
    folder.mkdir(parents=True)
    try:
        meta: dict[str, Any] = {"id": upload_id, "name": name, "kind": kind, "size": len(data), "created": now()}
        if kind == "image":
            data, meta["mime"], suffix = _prepare_image(data)
            (folder / f"image.{suffix}").write_bytes(data)
        else:
            original = folder / f"original{Path(name).suffix.lower()}"
            original.write_bytes(data)
            if kind == "document":
                # Read it now, so a file Fox can't read is refused here rather than mid-conversation.
                try:
                    text = _read_text(original)
                except ToolError as exc:
                    raise UploadError(f"{name}: {exc}") from exc
                except Exception as exc:
                    raise UploadError(f"Fox couldn't read {name}: {exc}") from exc
                if not text.strip():
                    raise UploadError(f"{name} has no text Fox can read. A scanned PDF needs OCR first.")
                (folder / "text.txt").write_text(text, encoding="utf-8")
                meta["chars"] = len(text)
        (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        return meta
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def load_upload(s: Settings, upload_id: str) -> dict[str, Any] | None:
    if not UPLOAD_ID.match(upload_id or ""):
        return None
    try:
        return json.loads((uploads_dir(s) / upload_id / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def upload_file(s: Settings, upload_id: str) -> Path | None:
    """The stored image or original file."""
    if not UPLOAD_ID.match(upload_id or ""):
        return None
    folder = uploads_dir(s) / upload_id
    found = sorted(folder.glob("image.*")) or sorted(folder.glob("original*"))
    return found[0] if found else None


def upload_text(s: Settings, upload_id: str) -> str:
    meta = load_upload(s, upload_id)
    if meta is None or meta["kind"] != "document":
        raise ToolError(f"No attached document has id {upload_id}.")
    return (uploads_dir(s) / upload_id / "text.txt").read_text(encoding="utf-8")


def delete_upload(s: Settings, upload_id: str) -> bool:
    if not UPLOAD_ID.match(upload_id or ""):
        return False
    folder = uploads_dir(s) / upload_id
    existed = folder.is_dir()
    shutil.rmtree(folder, ignore_errors=True)
    return existed


def clear_uploads(s: Settings) -> None:
    shutil.rmtree(uploads_dir(s), ignore_errors=True)


def attachment_ids(messages: list[BaseMessage]) -> list[str]:
    return [a["id"] for m in messages for a in m.additional_kwargs.get("fox_attachments", [])]


def _untrusted(name: str, upload_id: str, body: str) -> str:
    # A document can't close the tag early and have the rest read as instructions.
    body = re.sub(r"</?\s*untrusted_data", "[tag removed]", body, flags=re.IGNORECASE)
    return f'<untrusted_data source="attachment" name="{name}" id="{upload_id}">\n{body}\n</untrusted_data>'


def build_message(s: Settings, text: str, ids: list[str]) -> HumanMessage:
    """The user's message with its attachments, as the model will see it."""
    if not ids:
        return HumanMessage(text)
    if len(ids) > MAX_ATTACHMENTS:
        raise UploadError(f"Attach up to {MAX_ATTACHMENTS} files at a time.")
    blocks: list[dict[str, Any]] = [{"type": "text", "text": text or "Have a look at the attached files."}]
    attached = []
    budget = s.fox_max_attachment_chars
    for upload_id in dict.fromkeys(ids):
        meta = load_upload(s, upload_id)
        if meta is None:
            raise UploadError("An attachment is no longer available. Attach it again.")
        name = meta["name"]
        if meta["kind"] == "image":
            blocks.append({"type": "text", "text": f"[Attached image: {name}]"})
            blocks.append({"type": "image_url", "image_url": {"url": UPLOAD_SCHEME + upload_id}})
        elif meta["kind"] == "har":
            from fox.tools.network import import_har

            path = upload_file(s, upload_id)
            stored = import_har(path.read_bytes(), f"har:{name}") if path else 0
            blocks.append({
                "type": "text",
                "text": f"[Attached HAR file: {name}. Its {stored} requests were imported into the captured network "
                "traffic. Use summarize_network_traffic, list_network_requests and get_network_request to analyse it.]",
            })  # fmt: skip
        else:
            body = upload_text(s, upload_id)
            shown = body[: max(budget, 2000)]
            budget -= len(shown)
            if len(shown) < len(body):
                shown += (
                    f"\n[Showing the first {len(shown)} of {len(body)} characters. "
                    f'To read on, call read_attachment with attachment_id="{upload_id}" and start={len(shown)}.]'
                )
            blocks.append({"type": "text", "text": _untrusted(name, upload_id, shown)})
        attached.append({k: meta[k] for k in ("id", "name", "kind", "size")})

    kwargs = {"fox_text": text, "fox_attachments": attached}
    if not any(b["type"] == "image_url" for b in blocks):
        # Plain text works with every provider; a list of parts doesn't.
        return HumanMessage("\n\n".join(b["text"] for b in blocks), additional_kwargs=kwargs)
    return HumanMessage(blocks, additional_kwargs=kwargs)


def has_images(message: BaseMessage) -> bool:
    return isinstance(message.content, list) and any(
        isinstance(b, dict) and b.get("type") == "image_url" for b in message.content
    )


def hydrate(s: Settings, messages: list[BaseMessage]) -> list[BaseMessage]:
    """Swap image references for the image data, just before the model is called."""
    out = []
    for message in messages:
        if not has_images(message):
            out.append(message)
            continue
        blocks = []
        for block in message.content:
            url = block.get("image_url", {}).get("url", "") if isinstance(block, dict) else ""
            if url.startswith(UPLOAD_SCHEME):
                upload_id = url[len(UPLOAD_SCHEME) :]
                meta, path = load_upload(s, upload_id), upload_file(s, upload_id)
                if meta and path:
                    data = base64.b64encode(path.read_bytes()).decode()
                    block = {"type": "image_url", "image_url": {"url": f"data:{meta['mime']};base64,{data}"}}
                else:
                    block = {"type": "text", "text": "[This image was deleted.]"}
            blocks.append(block)
        out.append(message.model_copy(update={"content": blocks}))
    return out
