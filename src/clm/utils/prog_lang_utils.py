def suffix_for(prog_lang: str) -> str:
    if prog_lang == "python":
        return "py"
    if prog_lang == "cpp":
        return "cpp"
    elif prog_lang == "rust":
        return "rs"
    elif prog_lang == "java":
        return "java"
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
    elif prog_lang == "cpp":
        return {
            "codemirror_mode": "text/x-c++src",
            "file_extension": ".cpp",
            "mimetype": "text/x-c++src",
            "name": "c++",
            "version": "17",
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
    elif prog_lang == "java":
        return {
            "codemirror_mode": "java",
            "file_extension": ".java",
            "mimetype": "text/java",
            "name": "Java",
            "pygment_lexer": "java",
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
    elif prog_lang == "cpp":
        return {"display_name": "C++17", "language": "C++17", "name": "xcpp17"}
    elif prog_lang == "rust":
        return {"display_name": "Rust", "language": "rust", "name": "rust"}
    elif prog_lang == "java":
        return {"display_name": "Java", "language": "java", "name": "java"}
    else:
        raise ValueError(f"Unsupported language: {prog_lang}")
