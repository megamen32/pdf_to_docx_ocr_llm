.PHONY: build convert shell clean

PDF ?= /data/input/input.pdf
OUT ?= /data/output/result.docx

build:
	docker compose build

convert:
	docker compose run --rm pdf2docx --input $(PDF) --output $(OUT)

shell:
	docker compose run --rm --entrypoint bash pdf2docx

clean:
	rm -rf output/* work/*
