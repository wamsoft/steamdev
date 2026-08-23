"""Stage engine: compose the deploy folder from multiple sources.

krkrz_android (app/build.gradle の runCopyRules) / krkrz_web (tools/stage_web.py)
の assetPack.sources と同一書式・同一セマンティクスの移植:

- sources = [{type: mirror|flatten, from, to?, include?, exclude?, comment?}, ...]
- ``${VAR}`` 展開 (PROJECT_DIR + 環境変数)
- Ant 風グロブ (``**/`` = 階層跨ぎ / ``*`` = '/' 以外の 0+ / ``?`` = '/' 以外の 1 文字)
- ``to`` 省略時の既定: mirror+相対 from → from のパス / mirror+絶対 from → 末尾名 /
  flatten → ルート
- 重複配置先は先勝ち (オーバーレイ)
- 期待に無い既存ファイルは剪定、size + mtime(秒) 一致はスキップ (増分)
- 転送は同一ボリュームならハードリンク、失敗したら実コピーにフォールバック
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def expand_vars(s: str, ctx: dict) -> str:
    def repl(m):
        k = m.group(1)
        if k in ctx:
            return str(ctx[k])
        return os.environ.get(k, m.group(0))
    return _VAR_RE.sub(repl, s)


def glob_to_regex(glob: str) -> re.Pattern:
    glob = glob.replace("\\", "/")
    out, i, n = [], 0, len(glob)
    while i < n:
        if glob.startswith("**/", i):
            out.append("(?:.*/)?"); i += 3
        elif glob.startswith("**", i):
            out.append(".*"); i += 2
        elif glob[i] == "*":
            out.append("[^/]*"); i += 1
        elif glob[i] == "?":
            out.append("[^/]"); i += 1
        else:
            out.append(re.escape(glob[i])); i += 1
    return re.compile("^" + "".join(out) + "$")


def _matches_any(rel: str, regexes: list) -> bool:
    return any(r.match(rel) for r in regexes)


def link_or_copy(src: Path, dest: Path, stats: dict) -> None:
    """同一ボリュームなら os.link (実体複製なし・即時)、失敗したら copy2。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_symlink() or dest.exists():
        dest.unlink()
    try:
        os.link(src, dest)
        stats["linked"] += 1
    except OSError:
        shutil.copy2(src, dest)
        stats["copied"] += 1


def build_expected(sources: list, base_folder: Path, ctx: dict) -> dict:
    """sources を評価して {配置先相対パス: 元ファイル Path} を返す (重複は先勝ち)。"""
    expected: dict = {}
    dups: list = []
    for src in sources:
        typ = str(src.get("type", "mirror"))
        if typ not in ("mirror", "flatten"):
            raise ValueError(f"stage source: unknown type {typ!r} (mirror/flatten)")
        raw_from = src.get("from")
        if not raw_from:
            raise ValueError("stage source: 'from' is required")
        expanded_from = expand_vars(str(raw_from), ctx)
        from_is_abs = Path(expanded_from).is_absolute()
        from_dir = (Path(expanded_from) if from_is_abs
                    else Path(base_folder) / expanded_from).resolve()

        if "to" in src:
            to_path = src["to"] or ""
        elif typ == "mirror":
            to_path = from_dir.name if from_is_abs else expanded_from
        else:
            to_path = ""
        to_path = expand_vars(str(to_path), ctx).replace("\\", "/").strip("/")

        inc = [glob_to_regex(p) for p in src.get("include", ["**/*"])]
        exc = [glob_to_regex(p) for p in src.get("exclude", [])]

        if not from_dir.is_dir():
            logger.warning("stage source folder not found, skipped: %s", from_dir)
            continue

        for root, _dirs, files in os.walk(from_dir):
            for name in files:
                fp = Path(root) / name
                rel = fp.relative_to(from_dir).as_posix()
                if not _matches_any(rel, inc):
                    continue
                if exc and _matches_any(rel, exc):
                    continue
                if typ == "mirror":
                    dest_rel = f"{to_path}/{rel}" if to_path else rel
                else:
                    dest_rel = f"{to_path}/{name}" if to_path else name
                dest_rel = dest_rel.lstrip("/")
                if dest_rel in expected:
                    dups.append(dest_rel)   # 先勝ちオーバーレイは仕様
                    continue
                expected[dest_rel] = fp
    if dups:
        head = ", ".join(dups[:3])
        more = f" ほか {len(dups) - 3} 件" if len(dups) > 3 else ""
        logger.info("stage: %d duplicate(s) skipped (first wins): %s%s",
                    len(dups), head, more)
    return expected


def run_copy_rules(sources: list, out_dir: Path, base_folder: Path,
                   ctx: dict) -> dict:
    """sources を out_dir にマージ配置する (剪定 + 増分 + ハードリンク自動判定)。

    返り値は stats: {linked, copied, unchanged, removed, total}。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stats = {"linked": 0, "copied": 0, "unchanged": 0, "removed": 0, "total": 0}
    expected = build_expected(sources, base_folder, ctx)
    stats["total"] = len(expected)

    # 期待に無い古いファイルを剪定 (script 生成物は script 再実行で戻る前提)
    for root, _dirs, files in os.walk(out_dir):
        for name in files:
            fp = Path(root) / name
            rel = fp.relative_to(out_dir).as_posix()
            if rel not in expected:
                fp.unlink()
                stats["removed"] += 1
    for root, dirs, _files in os.walk(out_dir, topdown=False):
        for d in dirs:
            try:
                (Path(root) / d).rmdir()
            except OSError:
                pass

    # 変更 (サイズ / mtime 秒) のあるファイルだけ転送
    for dest_rel, src in expected.items():
        dest = out_dir / dest_rel
        if dest.exists():
            ss, ds = src.stat(), dest.stat()
            if ds.st_size == ss.st_size and int(ds.st_mtime) == int(ss.st_mtime):
                stats["unchanged"] += 1
                continue
        link_or_copy(src, dest, stats)
    return stats
