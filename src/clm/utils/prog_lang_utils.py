def suffix_for(prog_lang: str) -> str:
    if prog_lang == "python":
        return "py"
    elif prog_lang == "rust":
        return "ru"
    else:
        raise ValueError(f"Unsupported language: {prog_lang}")
