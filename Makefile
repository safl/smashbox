VENV     ?= .venv
PYTHON   ?= $(VENV)/bin/python
PIP      ?= $(VENV)/bin/pip
APP      ?= smashbox.py
VARIANT  ?= rpi
IMAGE_DIR ?= $(HOME)/system_imaging/disk
IMAGE     := smashbox-$(VARIANT)-arm64
# Single source of truth for the version is __version__ in smashbox.py;
# extracted here for the help text and post-build banner so make output
# can't drift from what the app reports.
VERSION   := $(shell awk -F'"' '/^__version__/ {print $$2; exit}' $(APP) 2>/dev/null)

.DEFAULT_GOAL := help

.PHONY: help venv run run-fullscreen media-deps image clean clean-image distclean

help:
	@echo "Smashbox v$(VERSION) — a toddler keyboard smash toy"
	@echo
	@echo "Dev:"
	@echo "  make venv             Create $(VENV) and install pygame + numpy"
	@echo "  make run              Run the app locally (windowed — easy to see in WSLg)"
	@echo "  make run-fullscreen   Same, but fullscreen"
	@echo
	@echo "Image (cijoe-driven; needs passwordless sudo + qemu-user-static):"
	@echo "  make media-deps       pipx install cijoe (one-time)"
	@echo "  make image            Build a flashable Raspberry Pi OS image"
	@echo "                          -> $(IMAGE_DIR)/$(IMAGE).img.gz"
	@echo
	@echo "Cleanup:"
	@echo "  make clean            Remove venv"
	@echo "  make clean-image      Remove built image artefacts + cijoe output"
	@echo "  make distclean        Both of the above"

$(VENV):
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install pygame numpy

venv: $(VENV)

run: venv
	SMASHBOX_WINDOWED=1 $(PYTHON) $(APP)

run-fullscreen: venv
	$(PYTHON) $(APP)

# ---------- Image build (cijoe) -----------------------------------------

media-deps:
	pipx install cijoe
	pipx ensurepath

image:
	cijoe tasks/build.yaml --monitor -c configs/$(VARIANT).toml
	@echo
	@echo "=================================================================="
	@echo " Image ready to flash  ·  smashbox v$(VERSION)"
	@echo "=================================================================="
	@if [ -f "$(IMAGE_DIR)/$(IMAGE).img.gz" ]; then \
	    echo "  $(IMAGE_DIR)/$(IMAGE).img.gz"; \
	    echo "  $(IMAGE_DIR)/$(IMAGE).img.gz.sha256"; \
	    echo "  $(IMAGE_DIR)/$(IMAGE).img"; \
	    echo; \
	    sz=$$(du -h "$(IMAGE_DIR)/$(IMAGE).img.gz" | cut -f1); \
	    echo "  compressed size: $$sz"; \
	    echo; \
	    echo "Flash with whichever tool you prefer:"; \
	    echo "  rpi-imager           (point it at the .img.gz file)"; \
	    echo "  bty flash file://$(IMAGE_DIR)/$(IMAGE).img.gz /dev/sdX --yes"; \
	    echo "  sudo dd if=$(IMAGE_DIR)/$(IMAGE).img of=/dev/sdX bs=4M conv=fsync status=progress"; \
	else \
	    echo "  (image not found — check cijoe-output/ for errors)"; \
	fi
	@echo "=================================================================="

clean:
	rm -rf $(VENV)
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

clean-image:
	rm -rf cijoe-output cijoe-archive
	rm -f $(IMAGE_DIR)/$(IMAGE).img \
	      $(IMAGE_DIR)/$(IMAGE).img.gz \
	      $(IMAGE_DIR)/$(IMAGE).img.gz.sha256

distclean: clean clean-image
