from backend.app.markdown import render_markdown


def test_markdown_preview_escapes_raw_html_and_keeps_safe_links() -> None:
    html = render_markdown(
        "# Hello\n\n<script>alert('x')</script>\n\n[RelayNorth](https://relaynorth.example)"
    )

    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert 'href="https://relaynorth.example"' in html


def test_markdown_supports_lists_and_emphasis() -> None:
    html = render_markdown("- **Fast**\n- *Private by design*")

    assert "<ul>" in html
    assert "<strong>Fast</strong>" in html
    assert "<em>Private by design</em>" in html
