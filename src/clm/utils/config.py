from pathlib import Path

from platformdirs import user_config_dir, site_config_dir
from configurator import Config

_course_layouts = [
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
    }
]

_python_config = {
    "file_extensions": ["py"],
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

_cpp_config = {
    "file_extensions": ["cpp"],
    "language_info": {
        "codemirror_mode": "text/x-c++src",
        "file_extension": ".cpp",
        "mimetype": "text/x-c++src",
        "name": "c++",
        "version": "17",
    },
    "kernelspec": {"display_name": "C++17", "language": "C++17", "name": "xcpp17"},
}

_rust_config = {
    "file_extensions": ["rs"],
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
        "num_win_workers": 32,
        "num_non_worker_cores": 2,
        "parens_to_replace": "{}[]",
        "chars_to_replace": "/\\$#%&<>*+=^€|",
        "chars_to_delete": ";!?\"'`.:",
        "prog_lang": {
            "python": _python_config,
            "cpp": _cpp_config,
            "rust": _rust_config,
        },
        "course_layouts": _course_layouts,
    }
)

site_config_file = Path(site_config_dir("clm", "CodingAcademy")) / "config.toml"
_site_config = Config.from_path(site_config_file, optional=True)

user_config_file = Path(user_config_dir("clm", "CodingAcademy")) / "config.toml"
_user_config = Config.from_path(user_config_file, optional=True)

config = _default_config + _site_config + _user_config
