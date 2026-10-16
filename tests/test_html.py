"""HTML -> Markdown conversion: tables, bold headings, page furniture."""

from filing_downloader.corpus.html import html_to_markdown

SAMPLE = """
<html><body>
<span style="font-weight:700">Item 7. Management's Discussion</span>
<p>Revenue grew.</p>
<p>Table of Contents</p>
<p>42</p>
<table>
  <tr><td></td><td></td><td>2025</td><td>2024</td></tr>
  <tr><td>Revenue</td><td>$</td><td>130,497</td><td>60,922</td></tr>
  <tr><td>Net income</td><td>$</td><td>72,880</td><td>29,760</td></tr>
</table>
</body></html>
"""


def test_bold_span_becomes_markdown_heading():
    text = html_to_markdown(SAMPLE)
    assert "**Item 7. Management's Discussion**" in text


def test_table_becomes_pipe_table_with_spacer_columns_removed():
    text = html_to_markdown(SAMPLE)
    assert "| Revenue | 130,497 | 60,922 |" in text
    assert "| --- |" in text
    # The "$" spacer column must not survive as its own column
    assert "| $ |" not in text


def test_page_furniture_is_removed():
    text = html_to_markdown(SAMPLE)
    assert "Table of Contents" not in text
    assert "\n42\n" not in text
