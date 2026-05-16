# PDF scan → editable DOCX через OCR + Marker + Qwen/Ollama

Контейнер делает пайплайн:

```text
сканированный PDF
  → OCRmyPDF/Tesseract: текстовый слой, выравнивание, поворот страниц
  → Marker: восстановление структуры в Markdown
  → Qwen/Ollama: чистка переносов, абзацев, заголовков и таблиц
  → Pandoc/LibreOffice: DOCX
```

## Быстрый старт

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
docker compose run --rm pdf2docx \
  --input /data/input/input.pdf \
  --output /data/output/result.docx
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

## Настройки LLM

По умолчанию используется OpenAI-compatible endpoint:

```text
https://llm.bezrabotnyi.com/v1
```

Модель по умолчанию:

```text
qwen3.5
```

Можно поменять через `.env`:

```bash
cp .env.example .env
nano .env
```

Или одной командой:

```bash
OPENAI_BASE_URL=https://llm.bezrabotnyi.com/v1 \
OPENAI_API_KEY=ollama \
LLM_MODEL=qwen3.5 \
docker compose run --rm pdf2docx --input /data/input/input.pdf --output /data/output/result.docx
```

Если модель в Ollama называется иначе, например `qwen2.5:32b` или `qwen3:32b`, поменяй `LLM_MODEL`.

## Без LLM

```bash
USE_LLM=0 docker compose run --rm pdf2docx \
  --input /data/input/input.pdf \
  --output /data/output/result.docx
```

## Важное

- Для таблиц качество зависит от исходного скана.
- OCRmyPDF лучше работает, если страницы не сильно смазаны.
- LLM здесь не распознаёт изображение напрямую, а чистит уже извлечённый текст/Markdown.
- Для секретных документов лучше запускать только локальную LLM/локальный endpoint.
