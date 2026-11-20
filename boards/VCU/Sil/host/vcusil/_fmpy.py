"""fmpy, imported quietly.

On aarch64 fmpy prints that its x86-only logging helper is missing. That
helper only formats messages for fmpy's default logger; Plant installs its
own, so the warning is noise.
"""

import contextlib
import io

with contextlib.redirect_stdout(io.StringIO()):
    import fmpy
    from fmpy import extract, read_model_description
    from fmpy.fmi2 import (FMU2Slave, defaultCallbacks, fmi2CallbackFunctions,
                           fmi2CallbackLoggerTYPE)

__all__ = ['fmpy', 'extract', 'read_model_description', 'FMU2Slave', 'defaultCallbacks',
           'fmi2CallbackFunctions', 'fmi2CallbackLoggerTYPE']
