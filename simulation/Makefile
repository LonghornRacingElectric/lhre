.DEFAULT_GOAL := help

COMPOSE ?= docker compose
RUN := $(COMPOSE) run --rm -T
BOBSIM_SHA = $(shell git -C bobsim rev-parse HEAD)
STUDY_WORKERS ?=
ARGS ?=
V ?=
STUDY_OUT = $(S)$(if $(V),--$(V))
STUDY_TEMPLATE := tools/study_template
BUNDLE_ID = $(shell sed -n 's/^bundle_id: *//p' ../vehicle/bundle.yml 2>/dev/null)

GIT_LF := git -c core.autocrlf=false -c core.longpaths=true

.PHONY: help init build shell test smoke unit records records-undo bobsim car four-post-acceptance new-study study stale bump-bobsim clean

help:
	@echo "make init                  Get submodules and build the image"
	@echo "make build                 Rebuild the image"
	@echo "make shell                 Open a shell in the container"
	@echo "make test                  Run make smoke, then make unit"
	@echo "make smoke                 Check that BobSim loads //vehicle"
	@echo "make unit                  Run tests/, then check that bobsim/ is clean"
	@echo "make records               Write //vehicle into BobLib's Modelica records"
	@echo "make records-undo          Put back only the files make records wrote"
	@echo "make bobsim T=<target>     Run a BobSim make target"
	@echo "make car T=<target>        Run a BobSim target on //vehicle, then restore bobsim/"
	@echo "                           VEHICLE=out/<name>/vehicle.yml runs a candidate copy instead"
	@echo "make four-post-acceptance  Check the FourPost outputs of make car (T=<target>)"
	@echo "make new-study S=<name>    Copy the study template to studies/<name>/"
	@echo "make study S=<name>        Run studies/<name>/run.py (STUDY_WORKERS=n limits CPUs)"
	@echo "make stale [S=<name>]      Check if a committed study result is stale (no S: every study)"
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


test: smoke unit

smoke:
	$(RUN) sim python tools/check_vehicle.py

unit:
	$(RUN) -e LHRE_REQUIRE_GIT=$(LHRE_REQUIRE_GIT) sim sh -c 'python -m pytest -q -ra -p no:cacheprovider tests'
	@test -z "$$(git -C bobsim status --porcelain --ignore-submodules=none)" || { echo "bobsim/ has local changes after the tests. Tests must not write into BobSim. If make records wrote them, run make records-undo."; git -C bobsim status --short --ignore-submodules=none; exit 1; }

BOBLIB := bobsim/engines/boblib
COMMITTED_VEHICLE := ../vehicle/vehicle.yml
CAR_VEHICLE = $(or $(VEHICLE),$(COMMITTED_VEHICLE))
CAR_ENV = $(if $(VEHICLE),-e BOBSIM_VEHICLE=$(VEHICLE))
car_out = out/car/$(1)$(if $(filter-out $(COMMITTED_VEHICLE),$(CAR_VEHICLE)),--$(patsubst out/%/,%,$(dir $(CAR_VEHICLE))))
VEHICLE_NAME = $(shell sed -n 's/^  name: *//p' $(CAR_VEHICLE) 2>/dev/null | head -n 1)
RECORD_FILES = \
	BobLib/Records/VehicleDefn/$(VEHICLE_NAME)Record.mo \
	BobLib/Records/VehicleDefn/package.order \
	BobLib/Experiments/Standards/Templates/Vehicle/VehicleSim_$(VEHICLE_NAME).mo \
	BobLib/Experiments/Standards/Templates/Vehicle/package.order \
	BobLib/Experiments/Standards/Templates/FourPost/FourPostSim_$(VEHICLE_NAME).mo \
	BobLib/Experiments/Standards/Templates/FourPost/package.order \
	BobLib/Experiments/Standards/VehicleSim.mo \
	BobLib/Experiments/Standards/FourPostSim.mo

records:
	$(RUN) $(CAR_ENV) sim sh -c 'v=$$(realpath "$$BOBSIM_VEHICLE") && cd bobsim && python -m app.modelica_generator "$$v" --write'

records-undo:
	cd $(BOBLIB) && git checkout -- $$(git ls-files -- $(RECORD_FILES)) && git clean -f -- $(RECORD_FILES)

bobsim:
	@test -n "$(T)" || { echo "Set T=<bobsim target>. Run 'make bobsim T=help' to list them."; exit 1; }
	$(RUN) sim make -C bobsim $(T)

car:
	@test -n "$(T)" || { echo "Set T=<bobsim target>, for example T=standard-eval-four-post."; exit 1; }
	COMPOSE='$(COMPOSE)' RUN='$(RUN)' CAR_BOBLIB='$(BOBLIB)' CAR_VEHICLE='$(CAR_VEHICLE)' CAR_OUT='$(call car_out,$(T))' CAR_VEHICLE_NAME='$(VEHICLE_NAME)' CAR_RECORD_FILES='$(RECORD_FILES)' sh tools/car.sh '$(T)'

four-post-acceptance:
	$(RUN) $(CAR_ENV) sim python tools/four_post_acceptance.py $(call car_out,$(or $(T),standard-eval-four-post))

new-study:
	@echo '$(S)' | grep -Eqx '[a-z0-9]+(-[a-z0-9]+)*' || { echo "Set S=<name>, a kebab-case name such as rear-stabar-rate."; exit 1; }
	@test ! -e studies/$(S) || { echo "studies/$(S) exists. Pick another name, or run make study S=$(S)."; exit 1; }
	mkdir studies/$(S)
	for f in $(notdir $(wildcard $(STUDY_TEMPLATE)/*)); do sed -e 's/STUDY_NAME/$(S)/g' -e 's/BUNDLE_ID/$(BUNDLE_ID)/g' $(STUDY_TEMPLATE)/$$f > studies/$(S)/$$f; done
	@echo "Made studies/$(S)/. Fill study.yml, run.py and README.md, then run make study S=$(S)."

study:
	@test -n "$(S)" || { echo "Set S=<study name>."; exit 1; }
	@test -f studies/$(S)/run.py || { echo "studies/$(S)/run.py does not exist."; exit 1; }
	@test -z "$(ARGS)" || test -n "$(V)" || { echo "Set V=<variant name> with ARGS."; exit 1; }
	$(RUN) -e STUDY=$(S) -e STUDY_VARIANT=$(V) -e OUT_DIR=out/.staging/$(STUDY_OUT) -e STUDY_WORKERS=$(STUDY_WORKERS) \
		-e BOBSIM_SHA=$(BOBSIM_SHA) -e LHRE_SHA=$$(git -C .. rev-parse HEAD 2>/dev/null) -e LHRE_REQUIRE_GIT=$(LHRE_REQUIRE_GIT) \
		-e LHRE_IMAGE_ID=$$(docker image inspect --format '{{.Id}}' $$($(COMPOSE) config --images sim 2>/dev/null) 2>/dev/null) sim \
		sh -c 'rm -rf out/.staging/$(STUDY_OUT) && python tools/provenance.py studies/$(S)/run.py $(ARGS) && python studies/$(S)/run.py $(ARGS) \
			&& rm -rf out/$(STUDY_OUT) && mv out/.staging/$(STUDY_OUT) out/$(STUDY_OUT) \
			$(if $(V),,&& python tools/readme.py studies/$(S)/README.md out/$(S))'

stale:
	$(RUN) sim python tools/invalidation.py stale $(S)

bump-bobsim:
	@test -n "$(REF)" || { echo "Set REF=<BobSim commit, tag or branch>."; exit 1; }
	@test -z "$$(git -C bobsim status --short)" || { echo "bobsim has local changes. Run 'make records-undo' first."; exit 1; }
	git -C bobsim fetch origin
	$(GIT_LF) -C bobsim submodule deinit -f --all
	$(GIT_LF) -C bobsim checkout --detach $(REF)
	$(GIT_LF) -C bobsim submodule update --init --recursive
	git -C bobsim submodule foreach --recursive 'git config core.autocrlf false && git config core.longpaths true'
	@echo "Pinned BobSim at $$(git -C bobsim rev-parse --short HEAD). Run 'make build test', then commit simulation/bobsim."

clean:
	rm -rf out

.PHONY: check-vehicle
check-vehicle:
	$(RUN) sim python tools/bundle.py check $(if $(BUNDLE),--bundle $(BUNDLE)) $(if $(VEHICLE),--vehicle $(VEHICLE)) $(if $(BASE),--base $(BASE))
