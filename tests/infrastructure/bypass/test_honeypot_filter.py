from job_ftch.infrastructure.sources.monitors.dom import filter_visible_links


def test_filter_visible_links():
    html = """
    <html>
        <body>
            <a href="https://example.com/job1">Job 1 (Visible)</a>
            <a href="https://example.com/job2">Job 2 (Visible)</a>
            <a href="https://example.com/job3">Job 3 (Visible)</a>

            <a href="https://example.com/job4" style="display:none">Job 4 (Hidden inline)</a>
            <a href="https://example.com/job5" style="visibility:hidden">Job 5 (Hidden inline)</a>
            <a href="https://example.com/job6" style="opacity:0">Job 6 (Hidden inline)</a>
            <a href="https://example.com/job7" aria-hidden="true">Job 7 (Hidden aria)</a>
            <a href="https://example.com/job8" style="left:-9999px">Job 8 (Hidden left)</a>

            <div style="display:none">
                <a href="https://example.com/job9">Job 9 (Hidden div)</a>
            </div>
            <span aria-hidden="true">
                <a href="https://example.com/job10">Job 10 (Hidden span)</a>
            </span>
        </body>
    </html>
    """
    links = [
        "https://example.com/job1",
        "https://example.com/job2",
        "https://example.com/job3",
        "https://example.com/job4",
        "https://example.com/job5",
        "https://example.com/job6",
        "https://example.com/job7",
        "https://example.com/job8",
        "https://example.com/job9",
        "https://example.com/job10",
    ]

    visible = filter_visible_links(html, links)

    assert "https://example.com/job1" in visible
    assert "https://example.com/job2" in visible
    assert "https://example.com/job3" in visible

    assert "https://example.com/job4" not in visible
    assert "https://example.com/job5" not in visible
    assert "https://example.com/job6" not in visible
    assert "https://example.com/job7" not in visible
    assert "https://example.com/job8" not in visible
    assert "https://example.com/job9" not in visible
    assert "https://example.com/job10" not in visible
