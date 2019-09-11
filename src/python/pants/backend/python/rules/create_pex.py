# Copyright 2019 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from pants.engine.fs import Digest, DirectoriesToMerge
from pants.backend.python.rules.download_pex_bin import DownloadedPexBin
from pants.backend.python.subsystems.python_native_code import PexBuildEnvironment, PythonNativeCode
from pants.backend.python.subsystems.python_setup import PythonSetup
from pants.engine.isolated_process import ExecuteProcessRequest, ExecuteProcessResult
from pants.engine.rules import optionable_rule, rule
from pants.engine.selectors import Get
from pants.util.objects import datatype, hashable_string_list, string_optional, string_type
from pants.util.strutil import create_path_env_var

#input_digests is tuple of Digests
class PexRequest(datatype([
  ('output_filename', string_type),
  ('requirements', hashable_string_list),
  ('interpreter_constraints', hashable_string_list),
  ('entry_point', string_optional),
  'input_digests'
])):
  """A request to create a PEX file populated with the given options."""


class PexOutput(datatype([('directory_digest', Digest)])):
  """Wrapper around a Digest containing a PEX file."""

# TODO: This is non-hermetic because the requirements will be resolved on the fly by
# pex, where it should be hermetically provided in some way.
@rule(PexOutput, [PexRequest, DownloadedPexBin, PythonSetup, PexBuildEnvironment])
def create_pex(request, pex_bin, python_setup, pex_build_environment):
  """Returns a PEX with the given requirements, optional sources, optional
  resources, optional entry point, and optional interpreter constraints."""

  interpreter_search_paths = create_path_env_var(python_setup.interpreter_search_paths)
  env = {"PATH": interpreter_search_paths, **pex_build_environment.invocation_environment_dict}

  interpreter_constraint_arg_string = ','.join([constraint for constraint in request.interpreter_constraints])

  # NB: we use the hardcoded and generic bin name `python`, rather than something dynamic like
  # `sys.executable`, to ensure that the interpreter may be discovered both locally and in remote
  # execution (so long as `env` is populated with a `PATH` env var and `python` is discoverable
  # somewhere on that PATH). This is only used to run the downloaded PEX tool; it is not
  # necessarily the interpreter that PEX will use to execute the generated .pex file.
  # TODO(#7735): Set --python-setup-interpreter-search-paths differently for the host and target
  # platforms, when we introduce platforms in https://github.com/pantsbuild/pants/issues/7735.

  argv = ["python", f"./{pex_bin.executable}",
    "--output-file", request.output_filename,
    *(["--entry-point", request.entry_point] if request.entry_point is not None else []),
    '--interpreter-constraint', interpreter_constraint_arg_string,
    '--disable-cache'
  ]

  input_digests = (
    pex_bin.directory_digest,
    *request.input_digests,
  )

  input_files = yield Get(Digest, DirectoriesToMerge, DirectoriesToMerge(directories=input_digests))

  execute_process_request = ExecuteProcessRequest(
    argv=tuple(argv),
    env=env,
    input_files=input_files,
    description=f"Create a requirements PEX: {', '.join(request.requirements)}",
    output_files=(request.output_filename,),
  )

  result = yield Get(ExecuteProcessResult, ExecuteProcessRequest, execute_process_request)
  yield PexOutput(directory_digest=result.output_directory_digest)

def rules():
  return [
    create_pex,
    optionable_rule(PythonSetup),
    optionable_rule(PythonNativeCode),
  ]
