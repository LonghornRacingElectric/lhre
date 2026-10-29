import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile

from app.modelica_generator import plan_modelica_stack
from common.vehicle_io import load_yaml
from tools.invalidation import git, sha256_file


BOBLIB_SHA = '4741a5ec6201635051031ed356a1bb55932df89c'


def git_sha(path):
    return git(path, 'rev-parse', 'HEAD').strip()


def require_matching_models(source, bobsim_root):
    def tree(root):
        return git(root, 'rev-parse', 'HEAD:BobLib').strip()
    if tree(source) != tree(bobsim_root/'engines/boblib'):
        raise ValueError('FMU source and pinned BobLib model trees differ; review the dependency update')
    if git(bobsim_root, 'status', '--porcelain', '--ignore-submodules=none'):
        raise ValueError('BobSim has local changes; restore it before building the FMU')


def model_text(record):
    return f'''model OrionFMI
  extends BobLib.Experiments.Standards.VehicleFMI(
    redeclare record VehicleRecord = BobLib.Records.VehicleDefn.{record},
    chassis(redeclare record VehicleRecord = BobLib.Records.VehicleDefn.{record}),
    headless = true);
  output Real rearX(unit="m");
  output Real rearY(unit="m");
  output Real rearYaw(unit="rad");
  output Real rearU(unit="m/s");
  output Real rearV(unit="m/s");
  output Real rearYawRate(unit="rad/s");
  output Real rearReferenceX(unit="m");
  output Real rearReferenceY(unit="m");
  output Real rearReferenceZ(unit="m");
protected
  Real rearPosition[3];
  Real forwardAxis[3];
equation
  rearReferenceX = pVehicle.pRrDW.wheelCenter[1];
  rearReferenceY = 0;
  rearReferenceZ = pVehicle.pRrDW.wheelCenter[3];
  rearPosition = chassis.chassisFrame.r_0 + Modelica.Mechanics.MultiBody.Frames.resolve1(
    chassis.chassisFrame.R,
    {{rearReferenceX, rearReferenceY, rearReferenceZ}} - chassis.chassisReferencePosition);
  forwardAxis = Modelica.Mechanics.MultiBody.Frames.resolve1(chassis.chassisFrame.R, {{1,0,0}});
  rearX = rearPosition[1];
  rearY = rearPosition[2];
  rearYaw = Modelica.Math.atan2(forwardAxis[2], forwardAxis[1]);
  rearU = cos(rearYaw)*der(rearX) + sin(rearYaw)*der(rearY);
  rearV = -sin(rearYaw)*der(rearX) + cos(rearYaw)*der(rearY);
  rearYawRate = yawVel;
end OrionFMI;
'''


def build(source, output, vehicle_path, bobsim_root):
    if git_sha(source) != BOBLIB_SHA:
        raise ValueError('FMU source does not match the reviewed BobLib export pin')
    if git(source, 'status', '--porcelain'):
        raise ValueError('FMU source checkout has local changes')
    require_matching_models(source, bobsim_root)
    vehicle = load_yaml(vehicle_path)
    if any(vehicle['architecture'][side] != 'bellcrank_stabar' for side in ('front', 'rear')):
        raise ValueError('This VehicleFMI export supports Orion bellcrank/stabar axles only')
    plan = plan_modelica_stack(vehicle_path, root=bobsim_root)
    if plan.initialization_error:
        raise ValueError(plan.initialization_error)
    original = bobsim_root / 'engines/boblib/BobLib'
    output.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location('boblib_fmu_export', source/'Tests/modelica_fmu_export.py')
    exporter = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = exporter
    spec.loader.exec_module(exporter)
    text = model_text(plan.result.record_name)
    with tempfile.TemporaryDirectory(prefix='orion-', dir=output) as raw:
        stage = Path(raw)/'BobLib'
        shutil.copytree(source/'BobLib', stage)
        for path, content in plan.contents:
            target = stage/path.relative_to(original)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        for path, name in zip(plan.result.package_orders, (
                plan.result.record_name, plan.result.vehicle_template_name,
                plan.result.four_post_template_name)):
            target = stage/path.relative_to(original)
            entries = target.read_text().splitlines()
            if name not in entries:
                target.write_text('\n'.join([*entries, name])+'\n')
        artifact = exporter.export_fmu(stage, output, timeout_s=3600, name='OrionFMI', model=text)
    manifest = {
        'schema': 'lhre.fmu.v1', 'fmu_sha256': sha256_file(artifact),
        'boblib_sha': BOBLIB_SHA, 'bobsim_sha': git_sha(bobsim_root),
        'vehicle_sha256': sha256_file(vehicle_path),
        'model_sha256': hashlib.sha256(text.encode()).hexdigest(),
        'record_sha256': hashlib.sha256(plan.contents[0][1].encode()).hexdigest(),
        'tires': {str(path.relative_to(vehicle_path.parent)): sha256_file(path)
                  for path in sorted((vehicle_path.parent/'tires').glob('*.tir'))},
    }
    (output/'provenance.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(f'Built {artifact}; provenance.json records the car and upstream commits', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=Path('out/fmu/boblib-source'))
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    build(args.source.resolve(), repo/'simulation/out/fmu', repo/'vehicle/vehicle.yml',
          repo/'simulation/bobsim')
