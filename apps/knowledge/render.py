"""Markdown a HTML seguro: lo escribe gente del equipo, pero se limpia igual antes de mostrarlo."""
import markdown
import nh3

TAGS = {"h1", "h2", "h3", "h4", "h5", "p", "br", "hr", "strong", "em", "del", "code", "pre", "blockquote", "ul", "ol", "li", "a",
        "table", "thead", "tbody", "tr", "th", "td"}
ATTRIBUTES = {"a": {"href", "title"}, "th": {"align"}, "td": {"align"}}


def to_html(text):
    html = markdown.markdown(text or "", extensions=["tables", "fenced_code", "sane_lists", "nl2br"], output_format="html")
    return nh3.clean(html, tags=TAGS, attributes=ATTRIBUTES, link_rel="noopener noreferrer", url_schemes={"http", "https", "mailto", "tel"})
