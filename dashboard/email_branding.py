from __future__ import annotations

import html
import re

DEFAULT_EMAIL_SUBJECT = "Alshival Notification"
DEFAULT_EMAIL_BODY = "No message body provided."


def normalize_email_subject(subject: str) -> str:
    normalized = str(subject or "").strip()
    return normalized or DEFAULT_EMAIL_SUBJECT


def normalize_email_body_text(body_text: str) -> str:
    normalized = str(body_text or "").replace("\r\n", "\n").strip()
    return normalized or DEFAULT_EMAIL_BODY


def sanitize_email_html_fragment(body_html: str) -> str:
    normalized = str(body_html or "").strip()
    if not normalized:
        return ""
    normalized = re.sub(r"(?is)<script[^>]*>.*?</script>", "", normalized)
    normalized = re.sub(r"(?is)<style[^>]*>.*?</style>", "", normalized)
    normalized = re.sub(r"(?i)\s+on[a-z]+\s*=\s*(['\"]).*?\1", "", normalized)
    normalized = re.sub(r"(?i)\s+on[a-z]+\s*=\s*[^\s>]+", "", normalized)
    normalized = re.sub(r"(?i)(href|src)\s*=\s*(['\"])\s*javascript:[^'\"]*\2", r"\1=\"#\"", normalized)
    return normalized


def _render_alshival_email_shell(subject: str, body_html: str) -> str:
    resolved_subject = normalize_email_subject(subject)
    safe_subject = html.escape(resolved_subject)
    resolved_body_html = str(body_html or "").strip()
    if not resolved_body_html:
        resolved_body_html = '<p style="margin:0;font-size:15px;line-height:1.7;color:#334155;">No message body provided.</p>'
    return f"""
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>{safe_subject}</title>
  </head>
  <body style="margin:0;padding:0;background-color:#f5f7fb;color:#0f172a;font-family:Arial,Helvetica,sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f5f7fb;padding:32px 16px;">
      <tr>
        <td align="center">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border-radius:16px;box-shadow:0 10px 30px rgba(15,23,42,0.12);overflow:hidden;">
            <tr>
              <td style="padding:28px 32px 8px;">
                <img src="https://alshival.ai/static/img/logos/brain1_transparent.png" width="48" alt="Alshival logo" style="display:block;border:0;height:auto;">
              </td>
            </tr>
            <tr>
              <td style="padding:0 32px 12px;">
                <h1 style="margin:0;font-size:22px;line-height:1.3;color:#0f172a;">{safe_subject}</h1>
              </td>
            </tr>
            <tr>
              <td style="padding:0 32px 24px;">
                {resolved_body_html}
              </td>
            </tr>
            <tr>
              <td style="padding:18px 32px 26px;background:#f8fafc;border-top:1px solid #e2e8f0;">
                <p style="margin:0;font-size:12px;line-height:1.6;color:#94a3b8;">This message was sent by Alshival.</p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
""".strip()


def render_alshival_branded_email_html(subject: str, body_text: str) -> str:
    normalized = normalize_email_body_text(body_text)

    paragraphs: list[str] = []
    for block in normalized.split("\n\n"):
        lines = [html.escape(line.strip()) for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        paragraphs.append("<br>".join(lines))
    body_html = "".join(
        f'<p style="margin:0 0 14px;font-size:15px;line-height:1.7;color:#334155;">{paragraph}</p>'
        for paragraph in paragraphs
    ) or '<p style="margin:0;font-size:15px;line-height:1.7;color:#334155;">No message body provided.</p>'
    return _render_alshival_email_shell(subject, body_html)

def render_alshival_branded_email_html_from_fragment(subject: str, body_html: str) -> str:
    sanitized_fragment = sanitize_email_html_fragment(body_html)
    if not sanitized_fragment:
        sanitized_fragment = '<p style="margin:0;font-size:15px;line-height:1.7;color:#334155;">No message body provided.</p>'
    return _render_alshival_email_shell(subject, sanitized_fragment)


def build_alshival_branded_email(subject: str, body_text: str) -> tuple[str, str, str]:
    resolved_subject = normalize_email_subject(subject)
    resolved_body = normalize_email_body_text(body_text)
    html_body = render_alshival_branded_email_html(resolved_subject, resolved_body)
    return resolved_subject, resolved_body, html_body


def build_alshival_branded_email_from_html(subject: str, body_text: str, body_html: str) -> tuple[str, str, str]:
    resolved_subject = normalize_email_subject(subject)
    resolved_body = normalize_email_body_text(body_text)
    html_body = render_alshival_branded_email_html_from_fragment(resolved_subject, body_html)
    return resolved_subject, resolved_body, html_body
