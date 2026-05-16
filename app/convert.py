#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable

from openai import OpenAI


SYSTEM_PROMPT = """Ты аккуратный редактор OCR-текста.
Твоя задача — НЕ переписывать смысл, НЕ добавлять факты и НЕ сокращать документ.
Нужно только привести Markdown после OCR к нормальному редактируемому виду:
- убрать мусор OCR, если он явно технический;
- склеить переносы слов внутри строки;
- восстановить абзацы;
- сохранить заголовки, списки, таблицы и нумерацию;
- не выдумывать отсутствующие фрагменты;
- если текст сомнителен, оставь как есть.
Верни только исправленный Markdown без комментариев.
"""


def run(cmd: list[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    print(f"$ {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if proc.stdout:
        print(proc.stdout, flush=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f"Command failed with code {proc.returncode}: {' '.join(cmd)}")
    return proc


def require_file(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if not path.is_file():
        raise ValueError(f"Path is not a file: {path}")


def newest_file(root: Path, suffixes: Iterable[str]) -> Path | None:
    suffixes_l = tuple(s.lower() for s in suffixes)
    files = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in suffixes_l]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def clean_markdown_basic(text: str) -> str:
    # Soft cleanup before LLM or final conversion.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"(?<=\w)-\n(?=\w)", "", text)  # пере-
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip() + "\n"


def chunk_markdown(text: str, max_chars: int = 9000) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    current_len = 0
    blocks = re.split(r"(\n#{1,6} .+\n)", text)

    def flush() -> None:
        nonlocal current, current_len
        if current:
            parts.append("".join(current).strip() + "\n")
            current = []
            current_len = 0

    for block in blocks:
        if not block:
            continue
        if current_len + len(block) > max_chars and current:
            flush()
        if len(block) > max_chars:
            flush()
            for i in range(0, len(block), max_chars):
                parts.append(block[i : i + max_chars].strip() + "\n")
            continue
        current.append(block)
        current_len += len(block)
    flush()
    return parts


def llm_cleanup(markdown: str, *, base_url: str, api_key: str, model: str) -> str:
    client = OpenAI(base_url=base_url, api_key=api_key)
    chunks = chunk_markdown(markdown)
    cleaned: list[str] = []
    for i, chunk in enumerate(chunks, start=1):
        print(f"LLM cleanup chunk {i}/{len(chunks)} with model={model}", flush=True)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": chunk},
            ],
            temperature=0,
        )
        content = resp.choices[0].message.content or ""
        cleaned.append(content.strip())
    return "\n\n".join(cleaned).strip() + "\n"


def run_ocr(input_pdf: Path, ocr_pdf: Path, lang: str) -> None:
    cmd = [
        "ocrmypdf",
        "-l",
        lang,
        "--deskew",
        "--clean",
        "--rotate-pages",
        "--skip-text",
        str(input_pdf),
        str(ocr_pdf),
    ]
    run(cmd)


def run_marker(ocr_pdf: Path, marker_dir: Path) -> Path:
    marker_dir.mkdir(parents=True, exist_ok=True)
    proc = run(["marker_single", str(ocr_pdf), "--output_dir", str(marker_dir)], check=False)
    if proc.returncode != 0:
        print("Marker failed, falling back to pdftotext extraction.", flush=True)
        fallback_md = marker_dir / "fallback.md"
        txt = marker_dir / "fallback.txt"
        run(["pdftotext", "-layout", str(ocr_pdf), str(txt)])
        fallback_md.write_text(clean_markdown_basic(txt.read_text(encoding="utf-8", errors="replace")), encoding="utf-8")
        return fallback_md

    md = newest_file(marker_dir, [".md"])
    if md:
        return md

    html = newest_file(marker_dir, [".html", ".htm"])
    if html:
        converted_md = marker_dir / "converted_from_html.md"
        run(["pandoc", str(html), "-t", "gfm", "-o", str(converted_md)])
        return converted_md

    raise RuntimeError(f"Marker finished, but no Markdown/HTML output found in {marker_dir}")


def markdown_to_docx(md_path: Path, output_docx: Path) -> None:
    output_docx.parent.mkdir(parents=True, exist_ok=True)
    run(["pandoc", str(md_path), "-o", str(output_docx)])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert scanned PDF to editable DOCX using OCRmyPDF + Marker + optional LLM cleanup.")
    parser.add_argument("--input", required=True, help="Input scanned PDF path inside container, e.g. /data/input/input.pdf")
    parser.add_argument("--output", required=True, help="Output DOCX path inside container, e.g. /data/output/result.docx")
    parser.add_argument("--workdir", default="/data/work", help="Working directory")
    parser.add_argument("--no-llm", action="store_true", help="Disable LLM cleanup")
    parser.add_argument("--keep-work", action="store_true", help="Do not delete intermediate files")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_pdf = Path(args.input).resolve()
    output_docx = Path(args.output).resolve()
    workdir = Path(args.workdir).resolve()

    require_file(input_pdf)
    workdir.mkdir(parents=True, exist_ok=True)

    if not args.keep_work:
        for child in workdir.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()

    ocr_pdf = workdir / "ocr.pdf"
    marker_dir = workdir / "marker"
    raw_md = workdir / "raw.md"
    final_md = workdir / "final.md"

    lang = os.getenv("OCR_LANG", "rus+eng")
    use_llm = os.getenv("USE_LLM", "1").lower() not in {"0", "false", "no", "off"}
    use_llm = use_llm and not args.no_llm

    print("Step 1/4: OCR PDF", flush=True)
    run_ocr(input_pdf, ocr_pdf, lang)

    print("Step 2/4: Extract structure with Marker", flush=True)
    marker_md = run_marker(ocr_pdf, marker_dir)
    raw_text = clean_markdown_basic(marker_md.read_text(encoding="utf-8", errors="replace"))
    raw_md.write_text(raw_text, encoding="utf-8")

    if use_llm:
        print("Step 3/4: Clean Markdown with LLM", flush=True)
        base_url = os.getenv("OPENAI_BASE_URL", "https://llm.bezrabotnyi.com/v1")
        api_key = os.getenv("OPENAI_API_KEY", "ollama")
        model = os.getenv("LLM_MODEL", "qwen3.5")
        try:
            cleaned = llm_cleanup(raw_text, base_url=base_url, api_key=api_key, model=model)
        except Exception as exc:
            print(f"LLM cleanup failed, using raw Markdown. Error: {exc}", flush=True)
            cleaned = raw_text
        final_md.write_text(cleaned, encoding="utf-8")
    else:
        print("Step 3/4: LLM cleanup disabled", flush=True)
        final_md.write_text(raw_text, encoding="utf-8")

    print("Step 4/4: Build DOCX", flush=True)
    markdown_to_docx(final_md, output_docx)

    print(f"Done: {output_docx}", flush=True)
    print(f"Intermediate Markdown: {final_md}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)
