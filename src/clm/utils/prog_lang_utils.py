def suffix_for(prog_lang: str) -> str:
    if prog_lang == "python":
        return "py"
    elif prog_lang == "rust":
        return "rs"
    else:
        raise ValueError(f"Unsupported language: {prog_lang}")


def language_info(prog_lang: str) -> dict:
    if prog_lang == "python":
        return {
            "codemirror_mode": {"name": "ipython", "version": 3},
            "file_extension": ".py",
            "mimetype": "text/x-python",
            "name": "python",
            "nbconvert_exporter": "python",
            "pygments_lexer": "ipython3",
        }
    elif prog_lang == "rust":
        return {
            "codemirror_mode": "rust",
            "file_extension": ".rs",
            "mimetype": "text/rust",
            "name": "Rust",
            "pygment_lexer": "rust",
            "version": "",
        }
    else:
        raise ValueError(f"Unsupported language: {prog_lang}")


def kernelspec_for(prog_lang: str) -> dict:
    if prog_lang == "python":
        return {
            "display_name": "Python 3 (ipykernel)",
            "language": "python",
            "name": "python3",
        }
    elif prog_lang == "rust":
        return {"display_name": "Rust", "language": "rust", "name": "rust"}
    else:
        raise ValueError(f"Unsupported language: {prog_lang}")
