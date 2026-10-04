"""MkDocs hooks for the EMIR documentation (`mkdocs.yml` names this file).

The pages under `docs/` are written to read well on GitHub too, so they link to
repository files outside `docs/` with relative paths (`../agent/README.md`,
`../examples/05_chip_level/`). Those targets are not documentation pages, so
MkDocs cannot serve them. Here they become links into the repository on its
host when `repo_url` is set in `mkdocs.yml`, and plain text naming the path
when it is not, so `mkdocs build --strict` passes either way.

It also registers the `mermaid` fence with `pymdownx.superfences`; the YAML
form of that setting needs a Python tag, which the pre-commit YAML check (and
any safe YAML loader) refuses.
"""
from __future__ import annotations

import posixpath
import re

#: an inline link or image: [text](target) or ![alt](target), no nested brackets
_LINK = re.compile(r"(!?)\[([^\]\n]*)\]\(([^)\s]+)\)")
#: a fenced code block opener or closer
_FENCE = re.compile(r"^\s*(```|~~~)")


def on_config(config, **_):
    from pymdownx.superfences import fence_code_format
    sf = config["mdx_configs"].setdefault("pymdownx.superfences", {})
    fences = sf.setdefault("custom_fences", [])
    if not any(f.get("name") == "mermaid" for f in fences):
        fences.append({"name": "mermaid", "class": "mermaid", "format": fence_code_format})
    return config


def _outside(page_uri: str, target: str):
    """The repository path `target` points at when it leaves `docs/`, else None."""
    if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith(("#", "/")):
        return None
    path = target.split("#", 1)[0]
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(page_uri), path))
    if not joined.startswith("../"):
        return None
    rel = joined[3:]
    if path.endswith("/"):
        rel += "/"
    return rel


def on_page_markdown(markdown, page, config, **_):
    repo = (config.get("repo_url") or "").rstrip("/")
    branch = (config.get("extra") or {}).get("repo_branch", "main")

    def fix(m):
        bang, text, target = m.groups()
        rel = _outside(page.file.src_uri, target)
        if rel is None:
            return m.group(0)
        if repo:
            kind = "tree" if rel.endswith("/") else ("raw" if bang else "blob")
            return f"{bang}[{text}]({repo}/{kind}/{branch}/{rel})"
        if bang:                       # an image with nowhere to come from: its alt text
            return text
        shown = rel.rstrip("/")
        return text if shown in text else f"{text} (`{shown}`)"

    out, fenced = [], False
    for line in markdown.split("\n"):
        if _FENCE.match(line):
            fenced = not fenced
            out.append(line)
            continue
        out.append(line if fenced else _LINK.sub(fix, line))
    return "\n".join(out)
