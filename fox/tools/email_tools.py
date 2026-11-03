"""Send and read email over SMTP and IMAP. Works with Gmail, Outlook and most other providers."""

from __future__ import annotations

import email
import imaplib
import mimetypes
import smtplib
import ssl
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import getaddresses

from fox.config import get_settings
from fox.core.permissions import Risk
from fox.core.tooling import ToolError, fox_tool
from fox.tools.files import check_readable, resolve


def _credentials():
    s = get_settings()
    if not (s.email_address and s.email_password):
        raise ToolError("Email isn't set up. Add EMAIL_ADDRESS and EMAIL_PASSWORD to .env.")
    return s


def _attachments(paths: list[str]):
    files = [check_readable(resolve(p)) for p in paths]
    for path in files:
        if not path.is_file():
            raise ToolError(f"Attachment not found: {path}")
    return files


def _describe_send(args: dict) -> str:
    _credentials()
    lines = [f"Send email to {args['to']}: {args['subject']}", f"To: {args['to']}"]
    if args["cc"]:
        lines.append(f"Cc: {args['cc']}")
    lines.append(f"Subject: {args['subject']}")
    if args["attachments"]:
        lines += [f"Attachment: {path}" for path in _attachments(args["attachments"])]
    return "\n".join(lines) + f"\n\n{args['body']}"


@fox_tool(risk=Risk.CRITICAL, scope=lambda a: a["to"], describe=_describe_send, timeout=60)
def send_email(to: str, subject: str, body: str, cc: str = "", attachments: list[str] = []) -> str:
    """Send an email from the user's account. `to` and `cc` are comma-separated addresses.

    Never guess a recipient's address: ask the user if you don't have it.
    """
    s = _credentials()
    message = EmailMessage()
    message["From"] = s.email_address
    message["To"] = to
    if cc:
        message["Cc"] = cc
    message["Subject"] = subject
    message.set_content(body)
    for path in _attachments(attachments):
        kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        main, _, sub = kind.partition("/")
        message.add_attachment(path.read_bytes(), maintype=main, subtype=sub, filename=path.name)

    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(s.email_address, s.email_password)
        smtp.send_message(message)
    recipients = [addr for _, addr in getaddresses([to, cc]) if addr]
    return f"Email sent to {', '.join(recipients)}."


def _decode(value: str | None) -> str:
    return str(make_header(decode_header(value))) if value else ""


def _plain_body(message: email.message.Message) -> str:
    html = ""
    for part in message.walk():
        if part.get_content_disposition() == "attachment":
            continue
        kind = part.get_content_type()
        if kind not in ("text/plain", "text/html"):
            continue
        text = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
        if kind == "text/plain":
            return text
        html = html or text
    if html:
        from bs4 import BeautifulSoup

        return BeautifulSoup(html, "html.parser").get_text("\n")
    return ""


def _imap() -> imaplib.IMAP4_SSL:
    s = _credentials()
    client = imaplib.IMAP4_SSL(s.imap_host, s.imap_port, ssl_context=ssl.create_default_context(), timeout=30)
    client.login(s.email_address, s.email_password)
    return client


def _quoted(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


@fox_tool(
    scope=lambda a: a["folder"],
    describe=lambda a: f"Read your email list ({a['folder']}, latest {a['limit']})",
    untrusted=True,
    timeout=60,
)
def list_emails(folder: str = "INBOX", limit: int = 10, unread_only: bool = False, sender: str = "", subject: str = "") -> str:
    """List recent emails with their IDs. Doesn't mark anything as read. Filter by sender or subject."""
    with _imap() as client:
        status, _ = client.select(_quoted(folder), readonly=True)
        if status != "OK":
            raise ToolError(f"Could not open mail folder '{folder}'.")
        criteria = ["UNSEEN"] if unread_only else ["ALL"]
        if sender:
            criteria += ["FROM", _quoted(sender)]
        if subject:
            criteria += ["SUBJECT", _quoted(subject)]
        _, data = client.uid("search", None, *criteria)
        uids = data[0].split()[-min(limit, 50):][::-1]
        rows = []
        for uid in uids:
            _, parts = client.uid("fetch", uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            header = email.message_from_bytes(parts[0][1])
            rows.append(
                f"[id {uid.decode()}] {_decode(header['Date'])}\nFrom: {_decode(header['From'])}\n"
                f"Subject: {_decode(header['Subject'])}"
            )
    return "\n\n".join(rows) or "No emails found."


@fox_tool(
    scope=lambda a: a["folder"],
    describe=lambda a: f"Read email id {a['email_id']} ({a['folder']})",
    untrusted=True,
    timeout=60,
)
def read_email(email_id: str, folder: str = "INBOX") -> str:
    """Read one email by the ID from list_emails. Doesn't mark it as read."""
    if not email_id.isdigit():
        raise ToolError("email_id must be a number from list_emails.")
    with _imap() as client:
        status, _ = client.select(_quoted(folder), readonly=True)
        if status != "OK":
            raise ToolError(f"Could not open mail folder '{folder}'.")
        _, parts = client.uid("fetch", email_id, "(BODY.PEEK[])")
        if not parts or not isinstance(parts[0], tuple):
            raise ToolError(f"No email with id {email_id}.")
        message = email.message_from_bytes(parts[0][1])
    names = [p.get_filename() for p in message.walk() if p.get_content_disposition() == "attachment"]
    return (
        f"From: {_decode(message['From'])}\nTo: {_decode(message['To'])}\nDate: {_decode(message['Date'])}\n"
        f"Subject: {_decode(message['Subject'])}\nAttachments: {', '.join(map(str, names)) or 'none'}\n\n"
        f"{_plain_body(message)}"
    )
