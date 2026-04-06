TODAY := $(shell date +%Y-%m-%d)
DATES := $(wildcard 20??-??-??)
PDFS  := $(foreach d,$(DATES),$(d)/$(d).pdf)
PPTXS := $(foreach d,$(DATES),$(d)/$(d).pptx)

.PHONY: all clean $(DATES)

all: $(TODAY)/$(TODAY).pdf $(TODAY)/$(TODAY).pptx

.SECONDEXPANSION:

$(DATES): $$@/$$@.pdf $$@/$$@.pptx

$(DATES:%=%/main.md): %/main.md: %/template.md
	mmdc -i $< -o $@

$(PDFS): $$(dir $$@)main.md
	marp --allow-local-files $< --pdf -o $@

$(PPTXS): $$(dir $$@)main.md
	marp --allow-local-files $< --pptx -o $@

clean:
	rm -f $(TODAY)/main.md $(TODAY)/$(TODAY).pdf $(TODAY)/$(TODAY).pptx
