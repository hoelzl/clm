import pytest
from clm.utils.in_memory_filesystem import (
    InMemoryFilesystem,
    convert_to_in_memory_filesystem,
)


def _file_header(title: str) -> str:
    return f'# j2 from \'macros.j2\' import header\n# {{{{ header("{title}", "{title}") }}}}\n\n'


def _markdown_slide(title: str, tags: list[str] = None) -> str:
    if tags is None:
        tags = []
    return (
        f'# %%[markdown] lang="en" tags={tags}\n'
        "#\n"
        f"# # {title}\n"
        "#\n"
        "# - First bullet\n"
        "# - Second bullet\n"
        "\n"
        f'# %%[markdown] lang="de" tags={tags}\n'
        "#\n"
        f"# # {title}\n"
        "#\n"
        "# - Erster Punkt\n"
        "# - Zweiter Punkt\n"
        "\n"
    )


def _code_slide(fun_name: str, tags: list[str] = None) -> str:
    header_line = f"\n# %% tags={tags}\n" if tags else "\n# %%\n"
    function_def = f'def {fun_name}(name):\n    print(f"Hello, {{name}}!")\n\n'
    return header_line + function_def


def _py_file_contents(title: str) -> str:
    return (
        _file_header(title)
        + _markdown_slide(title + " 1", ["slide"])
        + _code_slide("my_fun")
        + _code_slide("your_fun", ["keep"])
        + _markdown_slide(title + " 2", ["alt"])
        + _code_slide("their_fun", ["alt"])
    )


@pytest.fixture
def python_course_file_system() -> InMemoryFilesystem:
    return convert_to_in_memory_filesystem(
        {
            "examples": {
                "README.md": "# Examples\n",
                "EmployeeStarterKit": {
                    "employee.py": "class Employee:\n    pass\n",
                    "README.md": "# Employee Starter Kit\n",
                },
                "Employee": {
                    "employee.py": "class Employee:\n    pass\n",
                    "README.md": "# Employee\n",
                },
            },
            "slides": {
                "module_100_intro": {
                    "topic_100_intro.py": _py_file_contents("Intro"),
                    "topic_110_python.py": _py_file_contents("Python"),
                    "ws_100_python.py": _py_file_contents("Python WS"),
                    "python_file.py": "def foo(x): return x\n",
                    "img": {
                        "my_img.png": "image data",
                    },
                },
                "module_290_grasp": {
                    "topic_100_grasp.py": _py_file_contents("Grasp"),
                    "img": {
                        "adv-design-01.png": "image data",
                    },
                },
                "module_120_data_types": {
                    "topic_100_ints.py": _py_file_contents("Ints"),
                    "ws_100_ints.py": _py_file_contents("Ints WS"),
                    "topic_120_floats.py": _py_file_contents("Floats"),
                    "ws_120_floats.py": _py_file_contents("Floats WS"),
                    "topic_130_lists.py": _py_file_contents("Lists"),
                    "ws_130_lists.py": _py_file_contents("Lists WS"),
                },
            },
            "templates": {
                "macros.j2": (
                    "{% macro header(title_de, title_en) -%}\n"
                    '%% [markdown] lang="de" tags=["slide"]\n'
                    "#  <b>{{ title_de }}</b>\n\n"
                    '%% [markdown] lang="en" tags=["slide"]\n'
                    "#  <b>{{ title_en }}</b>\n\n"
                    "{% endmacro -%}\n"
                ),
            },
        }
    )
