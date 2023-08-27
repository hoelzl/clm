from clm.utils.config import config


def suffix_for(prog_lang: str) -> str:
    try:
        return config.prog_lang[prog_lang].file_extensions[0]
    except KeyError:
        raise ValueError(f"Unsupported language: {prog_lang}")


def language_info(prog_lang: str) -> dict:
    try:
        return config.prog_lang[prog_lang].language_info.data
    except KeyError:
        raise ValueError(f"Unsupported language: {prog_lang}")


def kernelspec_for(prog_lang: str) -> dict:
    try:
        return config.prog_lang[prog_lang].kernelspec.data
    except KeyError:
        raise ValueError(f"Unsupported language: {prog_lang}")
