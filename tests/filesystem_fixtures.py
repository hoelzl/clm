import pytest
from clm.utils.in_memory_filesystem import (
    InMemoryFilesystem,
    convert_to_in_memory_filesystem,
)


@pytest.fixture
def small_python_course_file_system() -> InMemoryFilesystem:
    return convert_to_in_memory_filesystem(
        {
            "python_courses": {
                "examples": {
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
                        "100_intro.py": "# header('Intro', 'Intro')\n",
                        "110_python_intro.py": "# header('Python', 'Python')\n",
                    },
                    "module_290_grasp": {
                        "100_grasp.py": "# header('Grasp', 'Grasp')\n",
                        "img": {
                            "adv-design-01.png": "image data",
                        },
                    },
                },
            },
        }
    )


def _file_header(title: str) -> str:
    return f'# header("{title}", "{title}")\n\n'


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


def _file_contents(title: str) -> str:
    return (
        _file_header(title)
        + _markdown_slide(title + " 1", ["slide"])
        + _code_slide("my_fun")
        + _code_slide("your_fun", ["keep"])
        + _markdown_slide(title + " 2", ["alt"])
        + _code_slide("their_fun", ["alt"])
    )


@pytest.fixture
def large_python_course_file_system() -> InMemoryFilesystem:
    return convert_to_in_memory_filesystem(
        {
            "python_courses": {
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
                        "topic_10_python.py": "# header('Python', 'Python')\n",
                        "ws_10_python.py": "# header('Python WS', 'Python WS')\n",
                        "python_file.py": "def foo(x): return x\n",
                        "img": {
                            "my_img.png": "image data",
                        },
                    },
                    "module_120_data_types": {
                        "topic_10_ints.py": "# header('Ints', 'Ints')\n",
                        "ws_10_ints.py": "# header('Ints WS', 'Ints WS')\n",
                        "topic_20_floats.py": "# header('Floats', 'Floats')\n",
                        "ws_20_floats.py": "# header('Floats WS', 'Floats WS')\n",
                        "topic_30_lists.py": "# header('Lists', 'Lists')\n",
                        "ws_30_lists.py": "# header('Lists WS', 'Lists WS')\n",
                    },
                },
            }
        }
    )
