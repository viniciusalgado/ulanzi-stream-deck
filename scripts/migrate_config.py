"""Convert a deck.toml from before layouts moved into it (its [pages.fun] / [pages.ai] / [pages.print] tables) to
the [[pages]] layout of deck.example.toml, keeping every value. The old file is kept next to it as deck.toml.old.

    .venv\\Scripts\\python scripts\\migrate_config.py [path-to-deck.toml]
    .venv\\Scripts\\python -m deck check

Every other section ([deck], [headphones], [[t3.environments]], [printer], [kvm], ...) is copied as it is,
comments included.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def q(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, list):
        return "[" + ", ".join(q(x) for x in v) + "]"
    s = str(v)
    return f"'{s}'" if ("\\" in s and "'" not in s and "\n" not in s) else json.dumps(s, ensure_ascii=False)


def key(slot, widget: str, **options) -> str:
    where = f"slots = {q(list(slot))}" if isinstance(slot, (list, tuple)) else f"slot = {slot}"
    lines = ["  [[pages.keys]]", f"  {where}", f"  widget = {q(widget)}"]
    lines += [f"  {k} = {q(v)}" for k, v in options.items() if v not in (None, "")]
    return "\n".join(lines) + "\n"


def page(name, label, icon, color, badge, keys) -> str:
    head = f'[[pages]]\nname = {q(name)}\nlabel = {q(label)}\nicon = {q(icon)}\ncolor = {q(color)}\nbadge = {q(badge)}\n'
    return head + "\n" + "\n".join(keys)


def layout_text(old: dict) -> str:
    fun, prt = old["pages"].get("fun", {}), old["pages"].get("print", {})
    apps = fun.get("apps", [])
    fun_keys = []
    for slot, app in zip((0, 1, 6), apps):
        fun_keys.append(key(slot, "launch", label=app.get("name"), icon=app.get("icon"), color=app.get("color"),
                            open=app.get("open")))
    fun_keys += [key([2, 3, 4], "media.playlists"), key(5, "headphones", label=fun.get("headphones_label")),
                 key(7, "media.prev"), key(8, "media.now_playing"), key(9, "media.next")]
    fun_keys.sort(key=lambda k: int(re.search(r"slots? = \[?(\d+)", k).group(1)))
    project = prt.get("project")
    print_keys = [key(0, "t3.new_thread", project=project),
                  key([1, 2, 3], "t3.threads", project=project, empty_header="3D threads", empty_lines=["none active"]),
                  key(4, "printer.camera", stream=prt.get("camera_page")),
                  key(5, "printer.status", open=prt.get("mainsail")),
                  key(6, "printer.filament"), key(7, "printer.pause_resume"), key(8, "printer.cancel", hold=2.0)]
    if prt.get("cura"):
        print_keys.append(key(9, "launch", label="Cura", icon="app:cura", open=prt["cura"]))
    ai_keys = [key(0, "t3.open_app"), key(1, "t3.dictate"), key(2, "t3.new_thread"),
               key([3, 4, 5, 6, 7, 8, 9], "t3.threads")]
    parts = ["# ================= pages (moved from [pages.*] by scripts/migrate_config.py) =================\n",
             page("fun", "Fun", "lucide:gamepad-2", "#f472b6", "media", fun_keys),
             page("ai", "AI", "lucide:bot", "#a78bfa", "t3", ai_keys),
             page("print", "3D Print", "lucide:printer", "#fb923c", "printer", print_keys),
             '[[bottom]]\nslots = [10, 11, 12]\nwidget = "nav"\n\n[[bottom]]\nslot = 13\nwidget = "kvm.switch"\n',
             "[media]\n" + f"playlists = {q(fun.get('playlists', []))}\n"
             + (f"spotify_app = {q(fun['spotify_app'])}\n" if fun.get("spotify_app") else "")]
    return "\n".join(parts) + "\n"


def sections(text: str) -> list[tuple[str, str]]:
    """(header, text) for each top-level table, the text before the first header under header ''."""
    out, header, buf = [], "", []
    for line in text.splitlines(keepends=True):
        m = re.match(r"\s*(\[\[?[^\]]+\]\]?)", line)
        if m and not line.lstrip().startswith("#"):
            out.append((header, "".join(buf)))
            header, buf = m.group(1), []
        buf.append(line)
    out.append((header, "".join(buf)))
    return out


def migrate(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    old = tomllib.loads(text)
    if isinstance(old.get("pages"), list):
        raise SystemExit(f"{path} already has [[pages]]: nothing to do")
    kept = [(h, t) for h, t in sections(text) if not h.startswith("[pages.")]
    out = []
    for h, t in kept:
        out.append(t)
        if h == "[deck]":
            out.append("\n" + layout_text(old))
    return "".join(out)


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "deck.toml"
    new = migrate(path)
    tomllib.loads(new)                    # never write something that isn't TOML
    shutil.copy2(path, path.with_suffix(".toml.old"))
    path.write_text(new, encoding="utf-8")
    print(f"{path} migrated (old one: {path.with_suffix('.toml.old').name}). Now run: python -m deck check")


if __name__ == "__main__":
    main()
