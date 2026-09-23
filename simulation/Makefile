.DEFAULT_GOAL := help

COMPOSE ?= docker compose
RUN := $(COMPOSE) run --rm -T
BOBSIM_SHA = $(shell git -C bobsim rev-parse HEAD)

# The container reads these files, so Windows checkouts must stay LF. BobLib
# paths also pass the 260-character Windows limit.
GIT_LF := git -c core.autocrlf=false -c core.longpaths=true

.PHONY: help init build shell test records records-undo bobsim study bump-bobsim clean

help:
	@echo "make init                  Get submodules and build the image"
	@echo "make build                 Rebuild the image"
	@echo "make shell                 Open a shell in the container"
	@echo "make test                  Check that BobSim loads //vehicle"
	@echo "make records               Write //vehicle into BobLib's Modelica records"
	@echo "make records-undo          Put BobLib's records back"
	@echo "make bobsim T=<target>     Run a BobSim make target"
	@echo "make study S=<name>        Run studies/<name>/run.py"
	@echo "make bump-bobsim REF=<ref> Move the BobSim pin"
	@echo "make clean                 Delete out/"

init:
	$(GIT_LF) -C .. submodule update --init --recursive simulation/bobsim
	git -C bobsim config core.autocrlf false
	git -C bobsim config core.longpaths true
	git -C bobsim submodule foreach --recursive 'git config core.autocrlf false && git config core.longpaths true'
	$(COMPOSE) build

build:
	$(COMPOSE) build

shell:
	$(COMPOSE) run --rm sim bash

test:
	$(RUN) sim python tools/check_vehicle.py

# Rewrites generated files inside the bobsim/ submodule. Do not commit them.
records:
	$(RUN) sim sh -c 'cd bobsim && python -m _5_App.modelica_generator "$$BOBSIM_VEHICLE" --write'

records-undo:
	git -C bobsim submodule foreach --recursive 'git checkout -- . && git clean -fd'

bobsim:
	@test -n "$(T)" || { echo "Set T=<bobsim target>. Run 'make bobsim T=help' to list them."; exit 1; }
	$(RUN) sim make -C bobsim $(T)

study:
	@test -n "$(S)" || { echo "Set S=<study name>."; exit 1; }
	@test -f studies/$(S)/run.py || { echo "studies/$(S)/run.py does not exist."; exit 1; }
	$(RUN) -e STUDY=$(S) -e OUT_DIR=out/$(S) -e BOBSIM_SHA=$(BOBSIM_SHA) sim \
		sh -c 'python tools/provenance.py && python studies/$(S)/run.py'

bump-bobsim:
	@test -n "$(REF)" || { echo "Set REF=<BobSim commit, tag or branch>."; exit 1; }
	git -C bobsim fetch origin
	git -C bobsim checkout --detach $(REF)
	$(GIT_LF) -C bobsim submodule update --init --recursive
	@echo "Pinned BobSim at $$(git -C bobsim rev-parse --short HEAD). Run 'make build test', then commit simulation/bobsim."

clean:
	rm -rf out
