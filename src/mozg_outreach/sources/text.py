import html
from html.parser import HTMLParser

_BLOCK_TAGS = {"p", "div", "br", "ul", "ol", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}
_SKIPPED_TAGS = {"script", "style"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIPPED_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n- " if tag == "li" else "\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._parts.append(data)

    def text(self) -> str:
        lines = (" ".join(line.split()) for line in "".join(self._parts).splitlines())
        return "\n".join(line for line in lines if line)


def html_to_text(markup: str) -> str:
    extractor = _TextExtractor()
    extractor.feed(markup)
    extractor.close()
    return extractor.text()


def unescape_html_to_text(markup: str) -> str:
    """For feeds that HTML-escape their HTML (Greenhouse content=true)."""
    return html_to_text(html.unescape(markup))
