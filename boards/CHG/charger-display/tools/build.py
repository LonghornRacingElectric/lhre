#!/usr/bin/env python3
"""Build all firmware modes using the Mac's existing ESP32 Arduino core."""
import argparse, hashlib, json, pathlib, shutil, subprocess, sys, zipfile
import urllib.request
root = pathlib.Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--mode', choices=('demo','monitor','control'), default='demo')
p.add_argument('--arduino-data', type=pathlib.Path, default=pathlib.Path.home()/'Library/Arduino15')
a = p.parse_args()
cli = shutil.which('arduino-cli')
if not cli: sys.exit('Install arduino-cli and ESP32 Arduino core 3.3.8 first (see README).')
if not (a.arduino_data/'packages/esp32/hardware/esp32/3.3.8').is_dir():
    sys.exit('ESP32 core 3.3.8 missing. Install it with arduino-cli, or pass --arduino-data.')
for name in ('.arduino-data','.arduino-downloads','.arduino-user','build'):
    (root/name).mkdir(exist_ok=True)
for source in a.arduino_data.glob('*index.json*'):
    shutil.copy2(source,root/'.arduino-data'/source.name)
packages=root/'.arduino-data/packages'
if not packages.exists(): packages.symlink_to(a.arduino_data/'packages', target_is_directory=True)
if packages.resolve()!=(a.arduino_data/'packages').resolve(): sys.exit('Existing local packages link points elsewhere; review it first.')
archive=root/'.arduino-downloads/TFT_eSPI-2.5.43.zip'
if not archive.exists():
    url='https://downloads.arduino.cc/libraries/github.com/Bodmer/TFT_eSPI-2.5.43.zip'
    print('Fetching pinned display library',flush=True)
    urllib.request.urlretrieve(url,archive)
expected='277a05b523f839e37309fc4de4dfcf0910d4eb0895b7335d10f4940aaf8e17fc'
if hashlib.sha256(archive.read_bytes()).hexdigest()!=expected: sys.exit('Display dependency checksum mismatch.')
libdir=root/'.arduino-user/libraries'
libdir.mkdir(exist_ok=True)
if not (libdir/'TFT_eSPI-2.5.43').is_dir():
    with zipfile.ZipFile(archive) as z:
        for name in z.namelist():
            if not (libdir/name).resolve().is_relative_to(libdir.resolve()): sys.exit('Invalid archive path.')
        z.extractall(libdir)
config=root/'arduino-cli.yaml'
config.write_text('directories:\n'+''.join(f'  {k}: {json.dumps(str(root/v))}\n' for k,v in [('data','.arduino-data'),('downloads','.arduino-downloads'),('user','.arduino-user')]))
flags=f'-include "{root / "include/DisplaySetup.h"}" -DCHARGER_DEMO={int(a.mode=="demo")} -DCHARGER_ENABLE_CONTROLS={int(a.mode=="control")}'
subprocess.run([cli,'compile','--config-file',str(config),'--fqbn','esp32:esp32:esp32:FlashSize=4M,FlashMode=dio,FlashFreq=40,PartitionScheme=default','--build-path',str(root/'build'/a.mode),'--library',str(root.parent/'lib'),'--build-property','build.extra_flags='+flags,str(root/'firmware/ChargerDisplay')],check=True)
shutil.copy2(a.arduino_data/'packages/esp32/hardware/esp32/3.3.8/tools/partitions/boot_app0.bin',root/'build'/a.mode/'boot_app0.bin')
print(f'Built {a.mode}: {root / "build" / a.mode}')
print('Build only. This script does not flash a device.')
