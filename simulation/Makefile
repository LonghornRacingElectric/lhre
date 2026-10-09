.DEFAULT_GOAL := help

COMPOSE ?= docker compose
RUN := $(COMPOSE) run --rm -T
BOBSIM_SHA = $(shell git -C bobsim rev-parse HEAD)
STUDY_WORKERS ?=
ARGS ?=
V ?=
STUDY_OUT = $(S)$(if $(V),--$(V))

GIT_LF := git -c core.autocrlf=false -c core.longpaths=true

.PHONY: help init build shell test records records-undo bobsim fmu realtime-bench study bump-bobsim clean

help:
	@echo "make init                  Get submodules and build the image"
	@echo "make build                 Rebuild the image"
	@echo "make shell                 Open a shell in the container"
	@echo "make test                  Check that BobSim loads //vehicle"
	@echo "make records               Write //vehicle into BobLib's Modelica records"
	@echo "make records-undo          Put back only the files make records wrote"
	@echo "make bobsim T=<target>     Run a BobSim make target"
	@echo "make fmu                   Build //vehicle as a VehicleSim FMU in out/fmu/"
	@echo "make realtime-bench        Time closed-loop stepping of the FMU and dyn_py"
	@echo "make study S=<name>        Run studies/<name>/run.py (STUDY_WORKERS=n limits CPUs)"
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

BOBLIB := bobsim/_0_Utils/external/BobLib
VEHICLE_NAME = $(shell sed -n 's/^  name: *//p' ../vehicle/vehicle.yml | head -n 1)
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
	$(RUN) sim sh -c 'cd bobsim && python -m _5_App.modelica_generator "$$BOBSIM_VEHICLE" --write'

records-undo:
	cd $(BOBLIB) && git checkout -- $$(git ls-files -- $(RECORD_FILES)) && git clean -f -- $(RECORD_FILES)

bobsim:
	@test -n "$(T)" || { echo "Set T=<bobsim target>. Run 'make bobsim T=help' to list them."; exit 1; }
	$(RUN) sim make -C bobsim $(T)

FMU_BUILD := bobsim/_3_StandardSim/BuildBobLib/VehicleFMU/VehicleSim.fmu

fmu:
	$(MAKE) records
	$(RUN) sim make -C bobsim standard-build-fmu; status=$$?; $(MAKE) records-undo; exit $$status
	mkdir -p out/fmu && cp $(FMU_BUILD) out/fmu/VehicleSim.fmu
	$(RUN) -e STUDY=fmu -e OUT_DIR=out/fmu -e BOBSIM_SHA=$(BOBSIM_SHA) sim python tools/provenance.py

realtime-bench:
	@test -f out/fmu/VehicleSim.fmu || { echo "Run 'make fmu' first."; exit 1; }
	$(RUN) sim python -m _3_StandardSim.RealtimeBench.realtime_bench \
		--vehicle /lhre/vehicle/vehicle.yml --fmu /lhre/simulation/out/fmu/VehicleSim.fmu \
		--output /lhre/simulation/out/realtime-bench/realtime_bench.json $(ARGS)

study:
	@test -n "$(S)" || { echo "Set S=<study name>."; exit 1; }
	@test -f studies/$(S)/run.py || { echo "studies/$(S)/run.py does not exist."; exit 1; }
	@test -z "$(ARGS)" || test -n "$(V)" || { echo "Set V=<variant name> with ARGS."; exit 1; }
	$(RUN) -e STUDY=$(S) -e OUT_DIR=out/.staging/$(STUDY_OUT) -e BOBSIM_SHA=$(BOBSIM_SHA) -e STUDY_WORKERS=$(STUDY_WORKERS) sim \
		sh -c 'rm -rf out/.staging/$(STUDY_OUT) && python tools/provenance.py && python studies/$(S)/run.py $(ARGS) \
			&& rm -rf out/$(STUDY_OUT) && mv out/.staging/$(STUDY_OUT) out/$(STUDY_OUT) \
			$(if $(V),,&& python tools/readme.py studies/$(S)/README.md out/$(S))'

bump-bobsim:
	@test -n "$(REF)" || { echo "Set REF=<BobSim commit, tag or branch>."; exit 1; }
	git -C bobsim fetch origin
	git -C bobsim checkout --detach $(REF)
	$(GIT_LF) -C bobsim submodule update --init --recursive
	@echo "Pinned BobSim at $$(git -C bobsim rev-parse --short HEAD). Run 'make build test', then commit simulation/bobsim."

clean:
	rm -rf out
