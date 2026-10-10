from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_EMOJI_FILE = Path(__file__).resolve().parent.parent / "data" / "emoji.json"


@lru_cache(maxsize=1)
def load_emojis() -> dict[str, str]:
    with _EMOJI_FILE.open("r", encoding="utf-8") as fp:
        data = json.load(fp)
    if not isinstance(data, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
        raise ValueError(f"Invalid emoji configuration: {_EMOJI_FILE}")
    return data


EMOJIS = load_emojis()


def emoji(name: str) -> str:
    try:
        return EMOJIS[name]
    except KeyError as exc:
        raise KeyError(f"Emoji '{name}' is not defined in {_EMOJI_FILE}") from exc
