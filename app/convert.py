#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import uuid
import json
import urllib.request
from pathlib import Path
from typing import Iterable

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
import uvicorn
from openai import OpenAI


SYSTEM_PROMPT = """Ты аккуратный редактор OCR-текста.
Твоя задача — НЕ переписывать смысл, НЕ добавлять факты и НЕ сокращать документ.
Нужно только привести Markdown после OCR к нормальному редактируемому виду:
- убрать мусор OCR, если он явно технический;
- склеить переносы слов внутри строки;
- восстановить абзацы;
- сохранить заголовки, списки, таблицы и нумерацию;
- раскрыть объединённые ячейки таблиц по покрываемым строкам, сохранив настоящие пустые ячейки;
- сохранить все строки, столбцы, единицы измерения и многоуровневые заголовки;
- не выдумывать отсутствующие фрагменты;
- если текст сомнителен, оставь как есть.
Верни только исправленный Markdown без комментариев.
"""


def run(cmd: list[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    print(f"$ {' '.join(cmd)}", flush=True)
    try:
        proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Command not found: {cmd[0]}. Please install it and ensure it is on PATH.") from exc
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
    text = re.sub(r"(?<=\w)-\n(?=\w)", "", text)  # пере-\nнос -> перенос
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
    api_style = os.getenv("LLM_API_STYLE", "auto")
    if api_style not in {"auto", "openai", "anthropic"}:
        raise ValueError("LLM_API_STYLE must be auto, openai, or anthropic")
    if api_style == "auto":
        api_style = "anthropic" if "/anthropic" in base_url else "openai"
    max_tokens = int(os.getenv("LLM_MAX_TOKENS", "16384"))
    client = OpenAI(base_url=base_url, api_key=api_key) if api_style == "openai" else None
    chunks = chunk_markdown(markdown)
    cleaned: list[str] = []
    for i, chunk in enumerate(chunks, start=1):
        print(f"LLM cleanup chunk {i}/{len(chunks)} with model={model}", flush=True)
        if api_style == "anthropic":
            endpoint = base_url.rstrip("/")
            endpoint = endpoint if endpoint.endswith("/v1") else endpoint + "/v1"
            payload = {"model": model, "max_tokens": max_tokens, "system": SYSTEM_PROMPT,
                       "messages": [{"role": "user", "content": chunk}]}
            request = urllib.request.Request(endpoint + "/messages",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=int(os.getenv("LLM_TIMEOUT", "180"))) as response:
                body = json.load(response)
            if body.get("stop_reason") == "max_tokens":
                raise RuntimeError("LLM output truncated; increase LLM_MAX_TOKENS")
            content = "\n".join(block.get("text", "") for block in body.get("content", []) if block.get("type") == "text")
        else:
            resp = client.chat.completions.create(
                model=model, max_tokens=max_tokens,
                messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": chunk}],
                temperature=0,
            )
            if resp.choices[0].finish_reason == "length":
                raise RuntimeError("LLM output truncated; increase LLM_MAX_TOKENS")
            content = resp.choices[0].message.content or ""
        if not content.strip():
            raise RuntimeError("LLM returned empty Markdown")
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


def clear_workdir(workdir: Path) -> None:
    workdir.mkdir(parents=True, exist_ok=True)
    for child in workdir.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def convert_pdf_to_docx(
    *,
    input_pdf: Path,
    output_docx: Path,
    workdir: Path,
    keep_work: bool = False,
    use_llm_override: bool | None = None,
) -> Path:
    input_pdf = input_pdf.resolve()
    output_docx = output_docx.resolve()
    workdir = workdir.resolve()

    require_file(input_pdf)
    workdir.mkdir(parents=True, exist_ok=True)

    if not keep_work:
        clear_workdir(workdir)

    ocr_pdf = workdir / "ocr.pdf"
    marker_dir = workdir / "marker"
    raw_md = workdir / "raw.md"
    final_md = workdir / "final.md"

    lang = os.getenv("OCR_LANG", "rus+eng")
    use_llm = os.getenv("USE_LLM", "1").lower() not in {"0", "false", "no", "off"}
    if use_llm_override is not None:
        use_llm = use_llm_override

    print("Step 1/4: OCR PDF", flush=True)
    run_ocr(input_pdf, ocr_pdf, lang)

    print("Step 2/4: Extract structure with Marker", flush=True)
    marker_md = run_marker(ocr_pdf, marker_dir)
    raw_text = clean_markdown_basic(marker_md.read_text(encoding="utf-8", errors="replace"))
    raw_md.write_text(raw_text, encoding="utf-8")

    if use_llm:
        print("Step 3/4: Clean Markdown with LLM", flush=True)
        base_url = os.getenv("OPENAI_BASE_URL", "https://api.z.ai/api/anthropic")
        api_key = os.getenv("OPENAI_API_KEY", "")
        model = os.getenv("LLM_MODEL", "glm-5.3-flash")
        try:
            cleaned = llm_cleanup(raw_text, base_url=base_url, api_key=api_key, model=model)
        except Exception as exc:
            if os.getenv("LLM_FAIL_FAST", "0") == "1":
                raise
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
    return output_docx


def print_api_help(host: str, port: int) -> None:
    print("""
PDF → DOCX API режим запущен.

Откройте браузер:
  http://localhost:%d/

Куда отправлять файл:
  POST /convert
  поле multipart/form-data: file=@document.pdf

Пример с хоста:
  curl -f -X POST -F "file=@input/input.pdf" http://localhost:%d/convert --output output/result.docx

Проверка:
  curl http://localhost:%d/health

Информация:
  GET /info

В docker-compose порт проброшен как:
  http://localhost:%d
""" % (port, port, port, port), flush=True)


def get_api_work_root() -> Path:
    api_work_root = Path(os.getenv("API_WORKDIR", "/data/work/api"))
    try:
        api_work_root.mkdir(parents=True, exist_ok=True)
        return api_work_root
    except OSError:
        fallback = Path.cwd() / "work" / "api"
        fallback.mkdir(parents=True, exist_ok=True)
        print(f"Warning: cannot use {api_work_root}, falling back to {fallback}", flush=True)
        return fallback


def create_app(port: int) -> FastAPI:
    app = FastAPI(title="PDF scan to DOCX OCR API")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>PDF → DOCX OCR UI</title>
  <style>
    body { font-family: Arial, sans-serif; max-width: 720px; margin: 2rem auto; line-height: 1.5; color: #111; }
    h1 { margin-bottom: 0.5rem; }
    .card { background: #f8f9fb; border: 1px solid #dfe3ea; border-radius: 12px; padding: 20px; box-shadow: 0 4px 18px rgba(0,0,0,0.04); }
    input[type=file] { width: 100%; }
    button { background: #1778f2; color: white; border: none; border-radius: 8px; padding: 12px 18px; cursor: pointer; font-size: 1rem; }
    button:hover { background: #145ecb; }
    pre { background: #212121; color: #f8f8f2; padding: 12px; overflow-x: auto; border-radius: 8px; }
    .note { margin-top: 1rem; color: #555; }
  </style>
</head>
<body>
  <div class="card">
    <h1>PDF → DOCX OCR</h1>
    <p>Загрузите отсканированный PDF, и сервер вернёт DOCX-файл.</p>
    <form id="upload-form" action="/convert" method="post" enctype="multipart/form-data">
      <label for="file">Выберите PDF-файл:</label>
      <input id="file" type="file" name="file" accept="application/pdf" required>
      <div style="margin-top:1rem;"><button type="submit">Конвертировать</button></div>
    </form>
    <p id="status" class="note">Файл будет скачан автоматически после завершения.</p>
    <h2>API</h2>
    <pre>POST /convert
multipart/form-data поле: file
GET /health
GET /info</pre>
    <div class="note">Если нужно, используйте этот URL из curl или другого клиента.</div>
  </div>
  <script>
    const form = document.getElementById('upload-form');
    const status = document.getElementById('status');

    form.addEventListener('submit', async event => {
      event.preventDefault();
      const fileInput = document.getElementById('file');
      const file = fileInput.files[0];
      if (!file) {
        status.textContent = 'Выберите PDF-файл.';
        return;
      }
      status.textContent = 'Загружаю и конвертирую...';

      const data = new FormData();
      data.append('file', file);

      try {
        const response = await fetch('/convert', { method: 'POST', body: data });
        if (!response.ok) {
          const errorText = await response.text();
          status.textContent = `Ошибка ${response.status}: ${errorText}`;
          return;
        }

        const blob = await response.blob();
        const contentDisposition = response.headers.get('content-disposition');
        let filename = 'result.docx';
        if (contentDisposition) {
          const match = /filename\*=UTF-8''(.+)|filename="?([^";]+)"?/.exec(contentDisposition);
          if (match) filename = decodeURIComponent(match[1] || match[2]);
        }

        const url = window.URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = filename;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        window.URL.revokeObjectURL(url);
        status.textContent = 'Готово! Файл загружен.';
      } catch (err) {
        status.textContent = `Ошибка: ${err}`;
      }
    });
  </script>
</body>
</html>"""

    @app.get("/info")
    def info() -> dict[str, object]:
        return {
            "ok": True,
            "service": "pdf-to-docx-ocr-llm",
            "upload_endpoint": "POST /convert",
            "form_field": "file",
            "curl": f"curl -f -X POST -F 'file=@input/input.pdf' http://localhost:{port}/convert --output output/result.docx",
        }

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/convert")
    async def convert_api(file: UploadFile = File(...)) -> FileResponse:
        filename = file.filename or "input.pdf"
        if not filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="Upload a PDF file in multipart field named 'file'.")

        base_work_root = get_api_work_root()
        job_id = uuid.uuid4().hex
        job_dir = base_work_root / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        input_pdf = job_dir / "input.pdf"
        output_docx = job_dir / "result.docx"
        workdir = job_dir / "work"

        try:
            content = await file.read()
            input_pdf.write_bytes(content)
            convert_pdf_to_docx(input_pdf=input_pdf, output_docx=output_docx, workdir=workdir, keep_work=True)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        return FileResponse(
            path=str(output_docx),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=Path(filename).with_suffix(".docx").name,
        )

    return app


def run_api(host: str, port: int) -> None:
    app = create_app(port)
    print_api_help(host, port)
    uvicorn.run(app, host=host, port=port)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert scanned PDF to editable DOCX using OCRmyPDF + Marker + optional LLM cleanup.")
    subparsers = parser.add_subparsers(dest="command")

    convert_parser = subparsers.add_parser("convert", help="CLI mode: convert one PDF file to DOCX")
    convert_parser.add_argument("--input", required=True, help="Input scanned PDF path inside container, e.g. /data/input/input.pdf")
    convert_parser.add_argument("--output", required=True, help="Output DOCX path inside container, e.g. /data/output/result.docx")
    convert_parser.add_argument("--workdir", default="/data/work", help="Working directory")
    convert_parser.add_argument("--no-llm", action="store_true", help="Disable LLM cleanup")
    convert_parser.add_argument("--keep-work", action="store_true", help="Do not delete intermediate files")

    api_parser = subparsers.add_parser("api", help="API mode: upload PDF via HTTP and receive DOCX")
    api_parser.add_argument("--host", default="0.0.0.0", help="API bind host")
    api_parser.add_argument("--port", type=int, default=int(os.getenv("API_PORT", "8000")), help="API bind port")

    # Backward compatibility: allow old syntax without explicit `convert` command.
    parser.add_argument("--input", help=argparse.SUPPRESS)
    parser.add_argument("--output", help=argparse.SUPPRESS)
    parser.add_argument("--workdir", default="/data/work", help=argparse.SUPPRESS)
    parser.add_argument("--no-llm", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--keep-work", action="store_true", help=argparse.SUPPRESS)

    args = parser.parse_args()
    if args.command is None and args.input and args.output:
        args.command = "convert"
    return args


def main() -> int:
    args = parse_args()

    if args.command == "api":
        run_api(host=args.host, port=args.port)
        return 0

    if args.command != "convert":
        print("Use one of modes:\n  convert --input /data/input/input.pdf --output /data/output/result.docx\n  api", file=sys.stderr)
        return 2

    convert_pdf_to_docx(
        input_pdf=Path(args.input),
        output_docx=Path(args.output),
        workdir=Path(args.workdir),
        keep_work=args.keep_work,
        use_llm_override=False if args.no_llm else None,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)
