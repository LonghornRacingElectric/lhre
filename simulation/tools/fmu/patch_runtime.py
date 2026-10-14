import sys
from pathlib import Path

PATCHES = {
    'fmi-export/fmu2_model_interface.c': [
        (
            '      overwriteOldSimulationData(comp->fmuData);\n'
            '      comp->fmuData->callback->functionAlgebraics(',
            '      if (!isCoSimulation(comp)) overwriteOldSimulationData(comp->fmuData);\n'
            '      comp->fmuData->callback->functionAlgebraics(',
        ),
        (
            '        flag = cvode_solver_fmi_step(comp, tNext, states);\n',
            '        {\n'
            '          MemPoolState mem_pool_state = omc_util_get_pool_state();\n'
            '          flag = cvode_solver_fmi_step(comp, tNext, states);\n'
            '          omc_util_restore_pool_state(mem_pool_state);\n'
            '        }\n',
        ),
        (
            '  fmi2Status status = fmi2OK;\n'
            '  eventInfo->newDiscreteStatesNeeded = fmi2True;\n'
            '  eventInfo->terminateSimulation     = fmi2False;\n'
            '  while (eventInfo->newDiscreteStatesNeeded && !eventInfo->terminateSimulation'
            ' && status != fmi2Error) {\n'
            '    status = internalEventUpdate((ModelInstance *)c, eventInfo);\n'
            '  }\n'
            '  return status;\n',
            '  fmi2Status status = fmi2OK;\n'
            '  MemPoolState mem_pool_state = omc_util_get_pool_state();\n'
            '  eventInfo->newDiscreteStatesNeeded = fmi2True;\n'
            '  eventInfo->terminateSimulation     = fmi2False;\n'
            '  while (eventInfo->newDiscreteStatesNeeded && !eventInfo->terminateSimulation'
            ' && status != fmi2Error) {\n'
            '    status = internalEventUpdate((ModelInstance *)c, eventInfo);\n'
            '  }\n'
            '  omc_util_restore_pool_state(mem_pool_state);\n'
            '  return status;\n',
        ),
    ],
    'simulation/solver/cvode_solver.c': [
        (
            '  if (!success)\n  {\n    retVal = -1;\n  }\n',
            '  if (!success)\n  {\n    retVal = 1;\n  }\n',
        ),
    ],
}


def patch(sources: Path) -> None:
    for relative, edits in PATCHES.items():
        path = sources / relative
        original = text = path.read_text(encoding='utf-8')
        for old, new in edits:
            if new in text:
                continue
            if text.count(old) != 1:
                raise SystemExit(f'{path}: no longer matches; check the OpenModelica runtime '
                                 'before building the FMU without this patch')
            text = text.replace(old, new)
        if text != original:
            path.write_text(text, encoding='utf-8')
            print(f'patched {relative}')


if __name__ == '__main__':
    patch(Path(sys.argv[1]))
