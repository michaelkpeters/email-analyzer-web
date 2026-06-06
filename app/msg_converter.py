"""Convert or parse email files into RFC 5322 .eml bytes and metadata."""

import io
import logging
import re
from email import message_from_bytes
from email.message import EmailMessage
from email.policy import default as default_policy
from email.utils import format_datetime, getaddresses, parseaddr, parsedate_to_datetime
from typing import Optional, Tuple

import extract_msg

logger = logging.getLogger(__name__)


def _strip_bom(data: bytes) -> bytes:
    """Remove common byte-order marks from the start of a file."""
    # UTF-8 BOM
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:]
    # UTF-16 BE / LE
    if data.startswith(b"\xfe\xff") or data.startswith(b"\xff\xfe"):
        return data[2:]
    # UTF-32 BE / LE
    if data.startswith(b"\x00\x00\xfe\xff") or data.startswith(b"\xff\xfe\x00\x00"):
        return data[4:]
    return data


def _format_addr(name: str, email: str) -> str:
    """Return ``email <name>`` if both pieces are present, else the non-empty part."""
    name = name.strip()
    email = email.strip()
    if email and name:
        return f"{email} <{name}>"
    return email or name or ""


def _extract_eml_metadata(eml: EmailMessage) -> dict:
    """Pull human-readable metadata out of an email.message.EmailMessage."""
    sender = eml.get("From", "")
    to_addrs = eml.get_all("To", []) + eml.get_all("Cc", [])
    recipients = []
    for name, email in getaddresses(to_addrs):
        formatted = _format_addr(name, email)
        if formatted:
            recipients.append(formatted)

    # Try to find attachment names
    attachment_names = []
    for part in eml.walk():
        if part.get_content_disposition() == "attachment":
            filename = part.get_filename()
            if filename:
                attachment_names.append(filename)

    date_raw = eml.get("Date")
    date_iso = None
    if date_raw:
        try:
            dt = parsedate_to_datetime(str(date_raw))
            date_iso = dt.isoformat()
        except Exception:
            date_iso = str(date_raw)

    return {
        "sender": _format_addr(*parseaddr(str(sender))),
        "recipients": recipients,
        "subject": eml.get("Subject", ""),
        "date": date_iso,
        "attachment_names": attachment_names,
    }


def _is_eml_format(data: bytes) -> bool:
    """Heuristic: does this look like a plain-text RFC 5322 message?"""
    if len(data) < 20:
        return False

    data = _strip_bom(data)
    header = data[:8192]

    # Must contain at least one typical email header
    header_patterns = (
        b"from:", b"to:", b"subject:", b"date:",
        b"mime-version:", b"received:", b"content-type:",
        b"message-id:", b"x-mailer:", b"x-originating-ip:",
    )
    return any(
        re.search(rb"(?mi)^\s*" + pat, header)
        for pat in header_patterns
    )


def _try_parse_eml(raw_bytes: bytes) -> Tuple[bytes, dict]:
    """Attempt to parse bytes as an RFC 5322 message."""
    eml = message_from_bytes(raw_bytes, policy=default_policy)
    if isinstance(eml, EmailMessage):
        return raw_bytes, _extract_eml_metadata(eml)
    raise ValueError("Parsed object is not an EmailMessage")


def _try_olefile_fallback(raw_bytes: bytes) -> Tuple[bytes, dict]:
    """
    Minimal fallback parser using olefile directly.

    Some .msg files (e.g. security/phishing simulation exports) have OLE
    structures that break extract-msg but can still be read at the stream
    level by olefile.  We pull out the most common header/body streams
    and build a minimal RFC-5322 message from them.
    """
    try:
        import olefile
    except ImportError:
        raise RuntimeError("olefile is not available") from None

    ole = olefile.OleFileIO(io.BytesIO(raw_bytes))

    def _read_stream(name: str) -> bytes:
        if ole.exists(name):
            return ole.openstream(name).read()
        return b""

    def _read_string(prop_id: str) -> str:
        # Unicode (001F) preferred, then ASCII (001E)
        for typ in ("001F", "001E"):
            data = _read_stream(f"__substg1.0_{prop_id}{typ}")
            if data:
                if typ == "001F":
                    return data.decode("utf-16-le", errors="replace").rstrip("\x00")
                return data.decode("ascii", errors="replace").rstrip("\x00")
        return ""

    subject = _read_string("0037")
    sender_addr = _read_string("0C1F")
    sender_name = _read_string("0E1D")
    to_line = _read_string("0E04")
    cc_line = _read_string("0E02")
    body_text = _read_string("1000")
    body_html = _read_string("1013")

    # If we got nothing useful, this fallback failed
    if not any((subject, sender_addr, sender_name, to_line, body_text, body_html)):
        raise ValueError("olefile fallback: no recognizable MSG streams found")

    sender = _format_addr(sender_name, sender_addr)

    def _safe_set_header(eml_msg: EmailMessage, name: str, value: str) -> None:
        if not value:
            return
        try:
            eml_msg[name] = value
        except Exception:
            eml_msg._headers.append((name, value))

    eml = EmailMessage(policy=default_policy)
    _safe_set_header(eml, "From", sender)
    _safe_set_header(eml, "To", to_line)
    _safe_set_header(eml, "Cc", cc_line)
    _safe_set_header(eml, "Subject", subject)

    # olefile fallback may also return bytes for body streams
    if isinstance(body_text, bytes):
        body_text = body_text.decode("utf-8", errors="replace")
    if isinstance(body_html, bytes):
        body_html = body_html.decode("utf-8", errors="replace")

    if body_html:
        eml.make_mixed()
        alt = EmailMessage(policy=default_policy)
        alt.make_alternative()
        text_part = EmailMessage(policy=default_policy)
        text_part.set_content(body_text or "", subtype="plain")
        html_part = EmailMessage(policy=default_policy)
        html_part.set_content(body_html, subtype="html")
        alt.attach(text_part)
        alt.attach(html_part)
        eml.attach(alt)
    else:
        eml.set_content(body_text or "", subtype="plain")

    ole.close()

    # olefile fallback: to_line/cc_line use semicolons as separators.
    raw_recipient_lines = []
    if to_line:
        raw_recipient_lines.extend([a.strip() for a in to_line.split(";") if a.strip()])
    if cc_line:
        raw_recipient_lines.extend([a.strip() for a in cc_line.split(";") if a.strip()])
    recipients = []
    for addr in raw_recipient_lines:
        name, email = parseaddr(addr)
        formatted = _format_addr(name, email)
        if formatted:
            recipients.append(formatted)

    metadata = {
        "sender": sender,
        "recipients": recipients,
        "subject": subject,
        "date": None,
        "attachment_names": [],
    }

    return eml.as_bytes(), metadata


def convert_or_parse_email(raw_bytes: bytes) -> Tuple[bytes, dict]:
    """
    Accept either a `.msg` (Outlook OLE2) or `.eml` (RFC 5322) file and return
    (eml_bytes, metadata).

    If the file is already an `.eml`, it is passed through unchanged.
    If it is a `.msg`, it is converted using `extract-msg`.

    Raises:
        ValueError: If the file cannot be parsed in either format.
    """
    if len(raw_bytes) == 0:
        raise ValueError("File is empty.")

    # Log first bytes for debugging
    preview = raw_bytes[:80]
    logger.debug("First 80 bytes: %r", preview)

    # 1. Quick heuristic: does it look like plain .eml text?
    looks_like_eml = _is_eml_format(raw_bytes)
    if looks_like_eml:
        logger.debug("Heuristic says .eml format")
        try:
            return _try_parse_eml(raw_bytes)
        except Exception as exc:
            logger.debug("EML parse failed (heuristic match): %s", exc)

    # 2. Try to parse it as an Outlook .msg (OLE2 binary)
    try:
        msg_file = extract_msg.Message(io.BytesIO(raw_bytes))
    except Exception as exc:
        logger.debug(".msg (OLE) parse failed: %s", exc)
        ole_error = exc
    else:
        # Successfully parsed as .msg
        eml = EmailMessage(policy=default_policy)

        def _safe_set_header(eml_msg: EmailMessage, name: str, value: str) -> None:
            """Set an email header, falling back to raw if the parser chokes."""
            if not value:
                return
            try:
                eml_msg[name] = value
            except Exception:
                # Python's email header parser can't handle this address format.
                # Store it as a raw string so we don't lose the data.
                eml_msg._headers.append((name, value))

        if msg_file.sender:
            _safe_set_header(eml, "From", msg_file.sender)
        if msg_file.to:
            to_val = msg_file.to if isinstance(msg_file.to, str) else ", ".join(msg_file.to)
            _safe_set_header(eml, "To", to_val)
        if msg_file.cc:
            cc_val = msg_file.cc if isinstance(msg_file.cc, str) else ", ".join(msg_file.cc)
            _safe_set_header(eml, "Cc", cc_val)
        if msg_file.subject:
            _safe_set_header(eml, "Subject", msg_file.subject)
        if msg_file.date:
            _safe_set_header(eml, "Date", format_datetime(msg_file.date))

        body_text = msg_file.body or ""
        body_html = msg_file.htmlBody or ""

        # extract-msg may return body content as bytes; decode for set_content
        if isinstance(body_text, bytes):
            body_text = body_text.decode("utf-8", errors="replace")
        if isinstance(body_html, bytes):
            body_html = body_html.decode("utf-8", errors="replace")

        if body_html:
            # Standard structure: multipart/mixed containing multipart/alternative
            # (text + html) plus any attachments.
            eml.make_mixed()
            alt = EmailMessage(policy=default_policy)
            alt.make_alternative()
            text_part = EmailMessage(policy=default_policy)
            text_part.set_content(body_text or "", subtype="plain")
            html_part = EmailMessage(policy=default_policy)
            html_part.set_content(body_html, subtype="html")
            alt.attach(text_part)
            alt.attach(html_part)
            eml.attach(alt)
        else:
            eml.set_content(body_text or "", subtype="plain")

        attachment_names = []
        for attachment in msg_file.attachments:
            try:
                name = attachment.getFilename() or "unnamed"
                attachment_names.append(name)
                mime_type = attachment.mimetype or "application/octet-stream"
                maintype, _, subtype = mime_type.partition("/")
                eml.add_attachment(
                    attachment.data,
                    maintype=maintype or "application",
                    subtype=subtype or "octet-stream",
                    filename=name,
                )
            except Exception:
                continue

        sender_name, sender_email = parseaddr(msg_file.sender) if msg_file.sender else ("", "")
        # If the header only had an email, try to get the display name from MSG properties.
        if sender_email and not sender_name:
            sender_name = msg_file.getStringStream('__substg1.0_0C1A') or ""
        sender = _format_addr(sender_name, sender_email)

        # extract-msg joins multiple recipients with semicolons, but
        # email.utils.getaddresses only understands commas. Split first.
        def _split_recipients(raw: Optional[str]) -> list:
            if not raw:
                return []
            return [a.strip() for a in raw.split(";") if a.strip()]

        all_addrs = _split_recipients(msg_file.to) + _split_recipients(msg_file.cc)
        recipients = []
        for addr in all_addrs:
            name, email = parseaddr(addr)
            formatted = _format_addr(name, email)
            if formatted:
                recipients.append(formatted)

        # Fallback: if nothing was parsed, try the raw Recipient objects.
        if not recipients and hasattr(msg_file, "recipients") and msg_file.recipients:
            for recipient in msg_file.recipients:
                name = getattr(recipient, "name", None) or ""
                email = getattr(recipient, "email", None) or ""
                formatted = _format_addr(name, email)
                if formatted:
                    recipients.append(formatted)

        metadata = {
            "sender": sender,
            "recipients": recipients,
            "subject": msg_file.subject,
            "date": msg_file.date.isoformat() if msg_file.date else None,
            "attachment_names": attachment_names,
        }

        return eml.as_bytes(), metadata

    # 3. Fallback: olefile-based minimal parser for malformed .msg files
    #    extract-msg depends on olefile, so it should already be installed.
    try:
        return _try_olefile_fallback(raw_bytes)
    except Exception as exc:
        logger.debug("olefile fallback also failed: %s", exc)

    # 4. Fallback: even if the heuristic failed, try parsing as .eml anyway.
    #    This catches unusual .eml files (e.g. with uncommon headers).
    if not looks_like_eml:
        logger.debug("Heuristic said not .eml, but trying EML parse as fallback")
        try:
            return _try_parse_eml(raw_bytes)
        except Exception as exc:
            logger.debug("EML fallback parse also failed: %s", exc)

    # 5. Last resort: if the file starts with printable ASCII/UTF-8 text,
    #    treat it as a raw .eml and submit it anyway (metadata will be sparse).
    stripped = _strip_bom(raw_bytes)
    text_preview = stripped[:200]
    if text_preview.decode("utf-8", errors="replace").count("\n") > 2:
        logger.warning(
            "Could not parse file as .msg or structured .eml; "
            "treating as raw text .eml and submitting anyway."
        )
        return raw_bytes, {
            "sender": None,
            "recipients": [],
            "subject": None,
            "date": None,
            "attachment_names": [],
        }

    raise ValueError(
        f"Failed to parse email file. It doesn't look like a valid .eml or .msg: {ole_error}"
    )
