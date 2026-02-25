import html
import re


_FENCE_RE = re.compile(r"```([a-zA-Z0-9_-]+)?\n([\s\S]*?)\n```", re.MULTILINE)


def render_markdown_fallback(md: str) -> str:
    """
    Safe, dependency-free fallback renderer.

    This is used when client-side markdown libraries are unavailable.
    """
    text = (md or "").replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        return ""

    out: list[str] = []
    last = 0
    for match in _FENCE_RE.finditer(text):
        before = text[last : match.start()]
        if before:
            out.append(html.escape(before).replace("\n", "<br>"))

        lang = (match.group(1) or "").strip().lower()
        code = html.escape(match.group(2) or "")
        if lang == "mermaid":
            out.append(f'<pre class="mermaid">{code}</pre>')
        else:
            cls = f"language-{lang}" if lang else ""
            out.append(f'<pre><code class="{cls}">{code}</code></pre>')

        last = match.end()

    rest = text[last:]
    if rest:
        out.append(html.escape(rest).replace("\n", "<br>"))

    return "".join(out)
