.PHONY: build convert api shell clean

PDF ?= /data/input/input.pdf
OUT ?= /data/output/result.docx
API_PORT ?= 8000

build:
	docker compose build

convert:
	docker compose run --rm pdf2docx convert --input $(PDF) --output $(OUT)

api:
	API_PORT=$(API_PORT) docker compose run --rm --service-ports pdf2docx api --port 8000

shell:
	docker compose run --rm --entrypoint bash pdf2docx

clean:
	rm -rf output/* work/*
