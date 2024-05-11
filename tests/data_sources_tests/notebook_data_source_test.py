from clm.data_sinks.notebook_data_sink import NotebookDataSink
from clm.data_sources.notebook_data_source import MARKDOWN_IMG_REGEX, HTML_IMG_REGEX


def test_notebook_data_source_target_name(
    notebook_data_source, python_course, completed_output_spec
):
    assert (
        notebook_data_source.get_target_name(python_course, completed_output_spec)
        == "01 Intro.ipynb"
    )


def test_notebook_data_source_process(
    notebook_data_source, python_course, completed_output_spec
):
    data_sink = notebook_data_source.process(python_course, completed_output_spec)
    assert isinstance(data_sink, NotebookDataSink)
    assert data_sink.data_source == notebook_data_source
    assert data_sink.target_loc == (
        python_course.target_loc / "public/Notebooks/Folien/Intro/01 Intro.ipynb"
    )
    assert not data_sink.target_loc.exists() or data_sink.target_loc.is_dir()


def test_markdown_img_regex_for_single_valid_tag():
    match = MARKDOWN_IMG_REGEX.findall("Before ![alt text](img/my_img.png) after")
    assert match == [("alt text", "img/my_img.png")]


def test_markdown_img_regex_for_multiple_valid_tags():
    match = MARKDOWN_IMG_REGEX.findall(
        "Before ![alt text](img/my_img.png) between ![other alt text](img/my_img_2.png) after"
    )
    assert match == [
        ("alt text", "img/my_img.png"),
        ("other alt text", "img/my_img_2.png"),
    ]


def test_markdown_img_regex_for_multiline_text():
    match = MARKDOWN_IMG_REGEX.findall(
        """Before ![alt text](img/my_img.png) between
        second line ![other alt text](img/my_img_2.png) after"""
    )
    assert match == [
        ("alt text", "img/my_img.png"),
        ("other alt text", "img/my_img_2.png"),
    ]


def test_markdown_img_regex_for_link_tag():
    match = MARKDOWN_IMG_REGEX.findall("Before [normal link](img/my_img.png) after")
    assert match == []


def test_html_img_regex_for_single_valid_tag():
    match = HTML_IMG_REGEX.findall(
        'Before <img src="img/my_img.png" alt="alt text", style="my-style"/> after'
    )
    assert match == ["img/my_img.png"]


def test_html_img_regex_for_single_valid_tag_with_linebreak():
    match = HTML_IMG_REGEX.findall(
        """Before <img src="img/my_img.png"
        alt="alt text", style="my-style"/> after""",
    )
    assert match == ["img/my_img.png"]


def test_html_img_regex_for_multiple_valid_tags():
    match = HTML_IMG_REGEX.findall(
        'Before <img src="img/my_img.png"/> between '
        '<img src="img/my_img_2.png" alt="alt text", style="my-style"/> after'
    )
    assert match == ["img/my_img.png", "img/my_img_2.png"]


def test_html_img_regex_for_multiline_text():
    match = HTML_IMG_REGEX.findall(
        """Before <img src="img/my_img.png"/> between
        second line <img src="img/my_img_2.png" alt="alt text", style="my-style"/> after"""
    )
    assert match == ["img/my_img.png", "img/my_img_2.png"]


def test_notebook_data_source_dependencies(notebook_data_source):
    loc = notebook_data_source.source_loc
    parent = loc.parent
    assert set(notebook_data_source.dependencies) == {
        (parent / "img/my_img.svg", loc),
        (parent / "img/my_img_a.svg", loc),
        (parent / "img/my_img_b.png", loc),
        (parent / "img/my_img_c.svg", loc),
    }
