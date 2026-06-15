"""
Slock CLI integration layer for Scene 1 cowork.

Trigger pattern (locked by @太上老君 2026-05-23):
  Input:  web app → DM @DentistWang with structured markdown + attachment IDs
  Output: DentistWang → DM thread reply with [Scene 1 cowork completed v1] block
          DentistWang also mirrors sanitized Layer 1 to #wt-clinic (pending admin #1 lock)
  Polling: Streamlit polls DM thread every 20s — #wt-clinic not needed for Streamlit path

Message schema (from @DentistWang 2026-05-23):
  Submission body: markdown, first line = tag, [立即处理] skips 60s wait rule
  Receipt: [Scene 1 cowork completed v1] block + full Layer 1+2 markdown
  Sufficiency: [Scene 1 sufficiency check v1] block
  Layer split: heading markers ### Layer 1: / ### Layer 2:
"""

import re
import subprocess
from pathlib import Path

import config

DENTIST_HANDLE = config.DENTIST_HANDLE
SUBMISSION_TAG = config.SUBMISSION_TAG
SUPPLEMENT_TAG = config.SUPPLEMENT_TAG
COMPLETED_TAG = config.COMPLETED_TAG
SUFFICIENCY_TAG = config.SUFFICIENCY_TAG
RECEIVED_TAG = config.RECEIVED_TAG

MAX_FILE_SIZE_BYTES = config.MAX_FILE_SIZE_BYTES
MAX_ATTACHMENTS = config.MAX_ATTACHMENTS
MAX_IMAGE_SIDE = config.MAX_IMAGE_SIDE


def _run(args: list, stdin_text: str = None) -> tuple:
    kwargs = dict(capture_output=True, text=True, timeout=60)
    if stdin_text is not None:
        kwargs["input"] = stdin_text
    result = subprocess.run(["slock"] + args, **kwargs)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _parse_msg_short_id(slock_output: str) -> str:
    """Extract 8-char short message ID from slock send stdout."""
    m = re.search(r"Message ID:\s*([0-9a-f]{8})", slock_output, re.IGNORECASE)
    if m:
        return m.group(1)
    # Full UUID — take first 8 chars
    m = re.search(r"Message ID:\s*([0-9a-f-]{36})", slock_output, re.IGNORECASE)
    if m:
        return m.group(1).replace("-", "")[:8]
    raise ValueError(f"Cannot parse message ID from slock output: {slock_output!r}")


def compress_image(file_path: str) -> str:
    """Compress image long side to MAX_IMAGE_SIDE px. Returns path to use."""
    try:
        from PIL import Image
        img = Image.open(file_path)
        if max(img.size) > MAX_IMAGE_SIDE:
            img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.LANCZOS)
            out_path = file_path + ".compressed.jpg"
            img.convert("RGB").save(out_path, "JPEG", quality=85)
            return out_path
    except Exception:
        pass
    return file_path


def upload_attachment(file_path: str, channel: str = None) -> str:
    """Upload a file via slock, return attachment ID."""
    target = channel or f"dm:{DENTIST_HANDLE}"
    rc, out, err = _run(["attachment", "upload", "--path", file_path, "--channel", target])
    if rc != 0:
        raise RuntimeError(f"attachment upload failed for {file_path}: {err}")
    for line in out.splitlines():
        lower = line.lower()
        if "attachment id:" in lower or "attachment_id" in lower:
            candidate = line.split(":", 1)[-1].strip()
            if candidate:
                return candidate
    # Fallback: last non-empty line that looks like a UUID
    import re as _re
    for line in reversed(out.splitlines()):
        line = line.strip()
        if _re.match(r"[0-9a-f-]{36}$", line):
            return line
    raise ValueError(f"Cannot parse attachment ID from: {out!r}")


def _format_submission(job_id: str, age: int, gender: str, chief_complaint: str) -> str:
    return (
        f"{SUBMISSION_TAG}\n\n"
        f"job_id: {job_id}\n"
        f"年龄: {age}\n"
        f"性别: {gender}\n"
        f"主诉:\n{chief_complaint}\n"
    )


def _format_supplement(job_id: str, supplement_text: str) -> str:
    return (
        f"{SUPPLEMENT_TAG}\n\n"
        f"job_id: {job_id}\n"
        f"补充信息:\n{supplement_text}\n"
    )


def _find_submission_msg_id(job_id: str) -> str:
    """Read DM channel and find msg short ID for a submission by job_id."""
    rc, out, _ = _run(["message", "read", "--channel", f"dm:{DENTIST_HANDLE}"])
    if rc != 0:
        return None
    _HEADER_RE = re.compile(r"\[seq=\d+ msg=([0-9a-f-]{36})")
    last_msg_id = None
    for line in out.splitlines():
        m = _HEADER_RE.search(line)
        if m:
            last_msg_id = m.group(1)
        if f"job_id: {job_id}" in line and last_msg_id:
            return last_msg_id.replace("-", "")[:8]
    return None


def submit_cowork(job_id: str, age: int, gender: str,
                  chief_complaint: str, attachment_paths: list) -> str:
    """
    Send Scene 1 cowork request to DentistWang via DM.
    Uploads and compresses attachments, sends structured markdown.
    Returns short msg ID (8 chars) for DM thread polling.
    """
    if len(attachment_paths) > MAX_ATTACHMENTS:
        raise ValueError(f"Too many attachments: {len(attachment_paths)} > {MAX_ATTACHMENTS}")

    attachment_ids = []
    for path in attachment_paths:
        size = Path(path).stat().st_size
        if size > MAX_FILE_SIZE_BYTES:
            raise ValueError(f"File too large ({size // 1024}KB > 10MB): {path}")
        compressed = compress_image(path)
        att_id = upload_attachment(compressed)
        attachment_ids.append(att_id)

    body = _format_submission(job_id, age, gender, chief_complaint)
    args = ["message", "send", "--target", f"dm:{DENTIST_HANDLE}"]
    for att_id in attachment_ids:
        args += ["--attachment-id", att_id]

    rc, out, err = _run(args, stdin_text=body)

    # Freshness hold: draft saved, auto-send with --anyway bypass
    if rc == 0 and "saved as a draft" in out.lower():
        rc, out, err = _run(["message", "send", "--send-draft", "--anyway",
                              "--target", f"dm:{DENTIST_HANDLE}"])

    if rc != 0:
        # SERVER_5XX: server error but message may have arrived — verify
        if "SERVER_5XX" in err:
            found_id = _find_submission_msg_id(job_id)
            if found_id:
                _notify_channel(job_id, found_id)
                return found_id
        raise RuntimeError(f"slock DM to DentistWang failed: {err}")

    short_id = _parse_msg_short_id(out)
    _notify_channel(job_id, short_id)
    return short_id


def _notify_channel(job_id: str, dm_short_id: str):
    """Ping DentistWang in #wt-webapp so they don't miss the DM."""
    notify_channel = config.NOTIFY_CHANNEL
    if not notify_channel:
        return
    body = f"{DENTIST_HANDLE} 新案例 #{job_id} 已发送至 DM（msg `{dm_short_id}`），请查收处理。"
    _run(["message", "send", "--target", notify_channel], stdin_text=body)


def submit_supplement(job_id: str, dm_short_id: str, supplement_text: str):
    """Send supplement info as a reply to the existing DM thread."""
    body = _format_supplement(job_id, supplement_text)
    target = f"dm:{DENTIST_HANDLE}:{dm_short_id}"
    rc, out, err = _run(["message", "send", "--target", target], stdin_text=body)
    if rc != 0:
        raise RuntimeError(f"slock supplement reply failed: {err}")


def read_dm_channel() -> str:
    """
    Read all messages in the main DM channel with DentistWang.
    Returns raw slock output.

    Architecture note (discovered 2026-05-23): Slock DM threads are not
    replyable by the receiver — only top-level DM replies are possible.
    Polling uses the main DM channel and matches responses by job_id.
    """
    rc, out, err = _run(["message", "read", "--channel", f"dm:{DENTIST_HANDLE}"])
    return out if rc == 0 else ""


_LAYER1_RE = re.compile(
    r"(### Layer 1:.*?)(?=### Layer 2:|$)", re.DOTALL | re.IGNORECASE
)
_LAYER2_RE = re.compile(r"(### Layer 2:.*?)$", re.DOTALL | re.IGNORECASE)


def _split_layers(text: str) -> tuple:
    m1 = _LAYER1_RE.search(text)
    m2 = _LAYER2_RE.search(text)
    return (m1.group(1).strip() if m1 else ""), (m2.group(1).strip() if m2 else "")


def _field(block: str, name: str) -> str:
    m = re.search(rf"^{re.escape(name)}:\s*(.+)$", block, re.MULTILINE)
    return m.group(1).strip() if m else ""


def parse_dm_for_job(raw_output: str, job_id: str):
    """
    Scan DM channel output for a completed or sufficiency-check message
    that matches the given job_id.

    Matches blocks where job_id appears near the marker tag.
    Returns dict with 'type' key, or None if no matching message found.
    """
    # Slock message header pattern — matches both channel (target=) and DM (seq=) formats
    _MSG_HEADER = re.compile(r"^\[(target=|seq=\d)", re.MULTILINE)

    for tag, handler in [
        (COMPLETED_TAG, _parse_completed),
        (SUFFICIENCY_TAG, _parse_sufficiency),
        (RECEIVED_TAG, _parse_received),
    ]:
        search_start = 0
        while True:
            idx = raw_output.find(tag, search_start)
            if idx < 0:
                break
            # Find the next slock message header after the tag
            rest = raw_output[idx + 1:]
            next_header = _MSG_HEADER.search(rest)
            block_end = idx + 1 + next_header.start() if next_header else len(raw_output)
            block = raw_output[idx:block_end]
            if f"job_id: {job_id}" in block or f"job_id:{job_id}" in block:
                return handler(block)
            search_start = idx + len(tag)
    return None


def _parse_completed(block: str) -> dict:
    md_start = block.find("### Layer 1:")
    if md_start < 0:
        md_start = block.find("### layer 1:")
    full_md = block[md_start:] if md_start >= 0 else ""
    layer1, layer2 = _split_layers(full_md)
    return {
        "type": "done",
        "cowork_id":       _field(block, "cowork_id"),
        "clinic_msg_id":   _field(block, "clinic_msg_id"),
        "confidence":      _field(block, "confidence"),
        "sufficiency":     _field(block, "sufficiency"),
        "emergency_level": _field(block, "emergency_level"),
        "layer1": layer1,
        "layer2": layer2,
    }

def _parse_received(block: str) -> dict:
    path = _field(block, "estimated_path") or "full_pipeline"
    return {
        "type": "received",
        "estimated_path": path,
        "cowork_id": _field(block, "cowork_id"),
    }


def _parse_sufficiency(block: str) -> dict:
    # YAML body starts on the line after the tag line
    tag_line_end = block.index("\n") + 1
    yaml_text = block[tag_line_end:tag_line_end + 6000]
    # Truncate at first marker that ends the structured YAML section
    for stopper in (
        re.compile(r"^\[seq=", re.MULTILINE),
        re.compile(r"^\[target=", re.MULTILINE),
        re.compile(r"^---\s*$", re.MULTILINE),
        re.compile(r"^### Layer [12]", re.MULTILINE | re.IGNORECASE),
        re.compile(r"^next_round_target_role:", re.MULTILINE),
        re.compile(r"^note_", re.MULTILINE),
    ):
        m = stopper.search(yaml_text)
        if m:
            yaml_text = yaml_text[: m.start()]
    import yaml
    try:
        data = yaml.safe_load(yaml_text) or {}
    except Exception:
        # Fallback: extract just the list fields with regex
        data = {}
        for key in ("cowork_id", "status", "confidence", "emergency_level"):
            fm = re.search(rf"^{key}:\s*(.+)$", yaml_text, re.MULTILINE)
            if fm:
                data[key] = fm.group(1).strip()
        for key in ("sufficient_fields", "missing_fields", "priority_order"):
            fm = re.search(rf"^{key}:\s*\n((?:  .+\n?)*)", yaml_text, re.MULTILINE)
            if fm:
                try:
                    data[key] = yaml.safe_load(key + ":\n" + fm.group(1)) or {key: []}
                    data[key] = data[key].get(key, [])
                except Exception:
                    pass
    return {
        "type": "sufficiency",
        "cowork_id":        data.get("cowork_id", ""),
        "sufficient_fields": data.get("sufficient_fields", []),
        "missing_fields":   data.get("missing_fields", []),
        "priority_order":   data.get("priority_order", []),
        "raw_yaml": yaml_text,
    }
