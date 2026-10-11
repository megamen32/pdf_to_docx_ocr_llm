# PDF scan → editable DOCX через OCR + Marker + GLM 5.3 Flash

Контейнер делает пайплайн:

```text
сканированный PDF
  → OCRmyPDF/Tesseract: текстовый слой, выравнивание, поворот страниц
  → Marker: восстановление структуры в Markdown
  → GLM 5.3 Flash: чистка переносов, абзацев, заголовков и таблиц
  → Pandoc/LibreOffice: DOCX
```

Есть два режима:

1. `convert` — обычный CLI: на вход PDF-файл в volume, на выход DOCX-файл в volume.
2. `api` — HTTP API: отправляешь PDF через `curl`/браузер/клиент, получаешь DOCX в ответ.

## Быстрый старт: CLI

Положи PDF сюда:

```bash
mkdir -p input output work
cp /path/to/file.pdf input/input.pdf
```

Собери контейнер:

```bash
docker compose build
```

Запусти конвертацию:

```bash
docker compose run --rm pdf2docx convert   --input /data/input/input.pdf   --output /data/output/result.docx
```

Или через Makefile:

```bash
make build
make convert
```

Готовый файл появится тут:

```text
output/result.docx
```

Старый синтаксис тоже оставлен рабочим:

```bash
docker compose run --rm pdf2docx   --input /data/input/input.pdf   --output /data/output/result.docx
```

## API-режим

Запусти сервер:

```bash
make api
```

Или напрямую:

```bash
docker compose run --rm --service-ports pdf2docx api --port 8000
```

При запуске контейнер напишет, куда слать файл. В браузере откройте:

```text
http://localhost:8000/
```

Основной endpoint:

```text
POST http://localhost:8000/convert
multipart/form-data поле: file
```

Пример:

```bash
curl -f -X POST   -F "file=@input/input.pdf"   http://localhost:8000/convert   --output output/result.docx
```

Проверка, что API живой:

```bash
curl http://localhost:8000/health
```

Если нужен другой порт на хосте:

```bash
API_PORT=8010 make api
```

Тогда запрос:

```bash
curl -f -X POST   -F "file=@input/input.pdf"   http://localhost:8010/convert   --output output/result.docx
```

## Настройки LLM

По умолчанию используется OpenAI-compatible endpoint:

```text
https://api.z.ai/api/anthropic
```

Модель по умолчанию:

```text
glm-5.3-flash
```

Можно поменять через `.env`:

```bash
cp .env.example .env
nano .env
```

Или одной командой:

```bash
OPENAI_BASE_URL=https://api.z.ai/api/anthropic OPENAI_API_KEY=ollama LLM_MODEL=glm-5.3-flash docker compose run --rm pdf2docx convert --input /data/input/input.pdf --output /data/output/result.docx
```

Если модель в Ollama называется иначе, например `qwen2.5:32b` или `qwen3:32b`, поменяй `LLM_MODEL`.

## Без LLM

```bash
USE_LLM=0 docker compose run --rm pdf2docx convert   --input /data/input/input.pdf   --output /data/output/result.docx
```

## Важное

- Для таблиц качество зависит от исходного скана.
- OCRmyPDF лучше работает, если страницы не сильно смазаны.
- LLM здесь не распознаёт изображение напрямую, а чистит уже извлечённый текст/Markdown.
- Для секретных документов лучше запускать только локальную LLM/локальный endpoint.

## Z.ai subscription

The default model is `glm-5.3-flash`. Set `OPENAI_API_KEY` to your own Z.ai
Coding Plan key in the protected environment. The Anthropic API is detected
from the base URL; set `LLM_API_STYLE=anthropic` to select it explicitly.
`LLM_API_STYLE=openai` preserves compatibility with other providers.
Use `LLM_FAIL_FAST=1` for evaluation so a failed model call cannot silently
be replaced with uncleaned Markdown. Truncated or empty output fails clearly.
