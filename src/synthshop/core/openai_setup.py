"""OpenAI-only, atomic updates to the launch directory's protected dotenv file."""

import io
import os
import re
import stat
import tempfile
from pathlib import Path

from dotenv.parser import parse_stream


def read_env(path: Path) -> bytes | None:
    """Refuse special/shared files; never follow a credential-file symlink."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
        raise ValueError(
            "Use a regular .env file owned only by your OS account, not a link. No changes made."
        )
    return path.read_bytes()


def env_parts(content: bytes | None) -> tuple[list[str], bool]:
    """Preserve unrelated dotenv entries verbatim, including multiline values/comments."""
    try:
        text = (content or b"").decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(
            "Existing .env is not valid UTF-8. No changes made; repair it first."
        ) from None
    parts = []
    found = False
    for entry in parse_stream(io.StringIO(text)):
        if entry.error:
            raise ValueError("Existing .env could not be parsed. No changes made; repair it first.")
        if entry.key and entry.key.lower() == "openai_api_key":
            found = found or bool(entry.value)
        else:
            parts.append(entry.original.string)
    return parts, found


def save_openai_key(path: Path, previous: bytes | None, key: str) -> None:
    """Replace only the OpenAI entry, committing a private file only after full write."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", key):
        raise ValueError(
            "Key must be a single token: letters, numbers, hyphens or underscores. "
            "No changes made."
        )
    parts, _ = env_parts(previous)
    content = "".join(parts)
    if content and not content.endswith("\n"):
        content += "\n"
    content += f"OPENAI_API_KEY='{key}'\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".env-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        if read_env(path) != previous:
            raise ValueError(
                "The .env file changed during setup. No changes made; run setup again."
            )
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
