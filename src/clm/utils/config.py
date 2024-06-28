from pathlib import Path

from configurator import Config
from configurator.node import ConfigNode
from platformdirs import user_config_dir

_course_layout_defaults = {
    "skip_dirs": (
        "__pycache__",
        ".git",
        ".ipynb_checkpoints",
        ".mypy_cache",
        ".pytest_cache",
        ".tox",
        ".venv",
        ".vs",
        ".vscode",
        ".idea",
        "build",
        "dist",
        ".cargo",
        ".idea",
        ".vscode",
        "target",
        "out",
    ),
    "kept_file": ("__init__.py", "__main__.py"),
    "ignore_file_regex": r"^[_.](.*)(\.*)?",
    "ignore_path_regex": r"(.*\.egg-info.*|.*cmake-build-.*)",
}

_course_layouts = [
    {
        "name": "python",
        "default_directory_kind": "GeneralDirectory",
        "directory_patterns": [
            ("examples", "ExampleDirectory"),
            ("slides/*", "NotebookDirectory"),
            ("slides/*/img/**", "GeneralDirectory"),
            ("slides/*/data/**", "GeneralDirectory"),
        ],
        "notebook_subdir_regex": r"^(?!(\..*|pu$|drawio$))",
    },
    {
        "name": "legacy_python",
        "default_directory_kind": "GeneralDirectory",
        "directory_patterns": [
            ("examples", "LegacyExampleDirectory"),
            ("metadata", "GeneralDirectory"),
            ("python_courses/slides/*", "NotebookDirectory"),
            ("python_courses/slides/*/img/**", "GeneralDirectory"),
            ("python_courses/slides/*/data/**", "GeneralDirectory"),
            ("python_courses/workshops", "NotebookDirectory"),
        ],
        "notebook_subdir_regex": r"^(?!(\..*|pu$|drawio$))",
    },
    {
        "name": "cpp",
        "default_directory_kind": "GeneralDirectory",
        "directory_patterns": [
            ("code/", "GeneralDirectory"),
            ("code/.devcontainer", "GeneralDirectory"),
            ("code/completed", "LegacyExampleDirectory"),
            ("code/starter_kits", "LegacyExampleDirectory"),
            ("code/external", "LegacyExampleDirectory"),
            ("slides/*", "NotebookDirectory"),
            ("slides/*/img/**", "GeneralDirectory"),
        ],
    },
    {
        "name": "java",
        "default_directory_kind": "GeneralDirectory",
        "directory_patterns": [
            ("examples", "LegacyExampleDirectory"),
            ("slides/*", "NotebookDirectory"),
            ("slides/*/img/**", "GeneralDirectory"),
        ],
    },
]

_cpp_config = {
    "file_extensions": ["cpp"],
    "jinja_prefix": "// j2",
    "jupytext_format": "cpp:percent",
    "language_info": {
        "codemirror_mode": "text/x-c++src",
        "file_extension": ".cpp",
        "mimetype": "text/x-c++src",
        "name": "c++",
        "version": "17",
    },
    "kernelspec": {"display_name": "C++17", "language": "C++17", "name": "xcpp17"},
}

_java_config = {
    "file_extensions": ["java"],
    "jinja_prefix": "// j2",
    "jupytext_format": "java:percent",
    "language_info": {
        "codemirror_mode": "java",
        "file_extension": ".java",
        "mimetype": "text/java",
        "name": "Java",
        "pygments_lexer": "java",
        "version": "",
    },
    "kernelspec": {"display_name": "Java", "language": "java", "name": "java"},
}

_python_config = {
    "file_extensions": ["py"],
    "jinja_prefix": "# j2",
    "jupytext_format": "py:percent",
    "language_info": {
        "codemirror_mode": {"name": "ipython", "version": 3},
        "file_extension": ".py",
        "mimetype": "text/x-python",
        "name": "python",
        "nbconvert_exporter": "python",
        "pygments_lexer": "ipython3",
    },
    "kernelspec": {
        "display_name": "Python 3 (ipykernel)",
        "language": "python",
        "name": "python3",
    },
}

_rust_config = {
    "file_extensions": ["rs"],
    "jinja_prefix": "# j2",
    "jupytext_format": "md",
    "language_info": {
        "codemirror_mode": "rust",
        "file_extension": ".rs",
        "mimetype": "text/rust",
        "name": "Rust",
        "pygment_lexer": "rust",
        "version": "",
    },
    "kernelspec": {"display_name": "Rust", "language": "rust", "name": "rust"},
}

_default_config = Config(
    {
        "num_retries_for_html": 5,
        "num_win_workers": 32,
        "num_non_worker_cores": 2,
        "parens_to_replace": "{}[]",
        "chars_to_replace": "/\\$#%&<>*=^€|",
        "chars_to_delete": ";!?\"'`.:",
        "prog_lang": {
            "cpp": _cpp_config,
            "java": _java_config,
            "python": _python_config,
            "rust": _rust_config,
        },
        "course_layout_defaults": _course_layout_defaults,
        "course_layouts": _course_layouts,
    }
)

user_config_file = Path(user_config_dir("clm", "CodingAcademy")) / "config.toml"
_user_config = Config.from_path(user_config_file, optional=True)

config = _default_config + _user_config


def config_to_python(config_node):
    if not isinstance(config_node, ConfigNode):
        return config_node
    data = config_node.data
    if isinstance(data, dict):
        return {key: config_to_python(value) for key, value in data.items()}
    elif isinstance(data, list):
        return [config_to_python(item) for item in data]
