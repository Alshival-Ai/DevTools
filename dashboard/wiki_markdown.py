import html
import re

try:
    import bleach
except Exception:  # pragma: no cover - runtime fallback
    bleach = None

try:
    import markdown
except Exception:  # pragma: no cover - runtime fallback
    markdown = None


_FENCE_RE = re.compile(r"```([a-zA-Z0-9_-]+)?\n([\s\S]*?)\n```", re.MULTILINE)
_MERMAID_BLOCK_RE = re.compile(
    r'<pre><code class="language-mermaid">([\s\S]*?)</code></pre>',
    re.IGNORECASE,
)

_ALLOWED_TAGS = {
    "a",
    "abbr",
    "b",
    "blockquote",
    "br",
    "code",
    "del",
    "details",
    "div",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "img",
    "input",
    "ins",
    "kbd",
    "li",
    "mark",
    "ol",
    "p",
    "pre",
    "s",
    "samp",
    "strong",
    "sub",
    "summary",
    "sup",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "ul",
}

_ALLOWED_ATTRS = {
    "*": ["class", "id", "name", "align"],
    "a": ["href", "title", "target", "rel"],
    "img": ["src", "alt", "title", "width", "height"],
    "input": ["type", "checked", "disabled"],
    "th": ["colspan", "rowspan", "align"],
    "td": ["colspan", "rowspan", "align"],
}


def _markdown_dep_available() -> bool:
    return markdown is not None and bleach is not None


def _promote_mermaid_blocks(value: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        code = match.group(1) or ""
        return f'<pre class="mermaid">{code}</pre>'

    return _MERMAID_BLOCK_RE.sub(_replace, value)


def _external_link_callback(attrs, new=False):
    href = str(attrs.get((None, "href"), "") or "").strip().lower()
    if not href or href.startswith("/") or href.startswith("#"):
        return attrs
    if href.startswith("mailto:") or href.startswith("tel:"):
        return attrs
    attrs[(None, "target")] = "_blank"
    attrs[(None, "rel")] = "noopener noreferrer"
    return attrs


def _render_with_markdown_lib(md: str) -> str:
    source = (md or "").replace("\r\n", "\n").replace("\r", "\n")
    if not source.strip():
        return ""

    rendered = markdown.markdown(
        source,
        extensions=[
            "extra",
            "fenced_code",
            "sane_lists",
            "tables",
            "nl2br",
        ],
        output_format="html5",
    )
    rendered = _promote_mermaid_blocks(rendered)

    cleaned = bleach.clean(
        rendered,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        protocols={"http", "https", "mailto", "tel"},
        strip=True,
    )
    return bleach.linkify(cleaned, callbacks=[_external_link_callback], skip_tags=["pre", "code"])


def render_markdown_fallback(md: str) -> str:
    """
    Safe, dependency-free fallback renderer.

    This is used when client-side markdown libraries are unavailable.
    """
    if _markdown_dep_available():
        return _render_with_markdown_lib(md)

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
