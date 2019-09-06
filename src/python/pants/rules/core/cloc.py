# Copyright 2019 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from pathlib import PurePath
from typing import Set

from pants.backend.graph_info.subsystems.cloc_binary import ClocBinary
from pants.base.build_environment import get_buildroot
from pants.base.specs import Specs
from pants.engine.console import Console
from pants.engine.fs import Digest, FilesContent, InputFileContent
from pants.engine.goal import Goal, LineOriented
from pants.engine.isolated_process import ExecuteProcessRequest, ExecuteProcessResult
from pants.engine.legacy.graph import HydratedTargets, TransitiveHydratedTargets
from pants.engine.rules import console_rule, optionable_rule
from pants.engine.selectors import Get

from pants.binaries.binary_util import BinaryUtil, BinaryRequest
from pants.binaries.binary_tool import BinaryRequestDigest


class CountLinesOfCode(LineOriented, Goal):
  name = 'clocv2'

  @classmethod
  def register_options(cls, register) -> None:
    super().register_options(register)
    register('--transitive', type=bool, fingerprint=True, default=True,
             help='Operate on the transitive dependencies of the specified targets.  '
                  'Unset to operate only on the specified targets.')
    register('--ignored', type=bool, fingerprint=True,
             help='Show information about files ignored by cloc.')


@console_rule(CountLinesOfCode, [Console, CountLinesOfCode.Options, ClocBinary, Specs])
def run_cloc(console, options, cloc_binary, specs):
  """Runs the cloc perl script in an isolated process"""


  version = cloc_binary.version()
  binary_request = cloc_binary.make_binary_request(version)
  print(f"Binary request: {binary_request}")

  cloc_digest = yield Get(BinaryRequestDigest, BinaryRequest, binary_request)
  print(f"Cloc digest: {cloc_digest}")


  cloc_binary_path = cloc_binary.select()

  transitive = options.values.transitive
  ignored = options.values.ignored

  if (transitive):
    targets = yield Get(TransitiveHydratedTargets, Specs, specs)
    all_target_adaptors = { t.adaptor for t in targets.roots }.union({ t.adaptor for t in targets.closure })
  else:
    targets = yield Get(HydratedTargets, Specs, specs)
    all_target_adaptors = { t.adaptor for t in targets }

  build_root = get_buildroot()
  source_paths: Set[str] = set()
  for t in all_target_adaptors:
    sources = getattr(t, 'sources', None)
    if sources is not None:
      for file in sources.snapshot.files:
        source_paths.add(str(PurePath(build_root, file)))

  file_content = bytes('\n'.join(source_paths), 'utf-8')

  input_files_filename = 'input_files.txt'
  report_filename = 'report.txt'
  ignore_filename = 'ignored.txt'

  input_file_list = InputFileContent(path=input_files_filename, content=file_content)
  digest = yield Get(Digest, InputFileContent, input_file_list)


  cmd = (
    '/usr/bin/perl',
    cloc_binary_path,
    '--skip-uniqueness', # Skip the file uniqueness check.
    f'--ignored={ignore_filename}', # Write the names and reasons of ignored files to this file.
    f'--report-file={report_filename}', # Write the output to this file rather than stdout.
    f'--list-file={input_files_filename}', # Read an exhaustive list of files to process from this file.
  )

  req = ExecuteProcessRequest(
    argv=cmd,
    input_files=digest,
    output_files=(report_filename, ignore_filename),
    description='cloc',
  )

  exec_result = yield Get(ExecuteProcessResult, ExecuteProcessRequest, req)
  files_content = yield Get(FilesContent, Digest, exec_result.output_directory_digest)

  file_outputs = { fc.path: fc.content.decode() for fc in files_content.dependencies }

  with CountLinesOfCode.line_oriented(options, console) as (print_stdout, print_stderr):
    output = file_outputs[report_filename]

    for line in output.splitlines():
      print_stdout(line)

    if ignored:
      print_stdout("\nIgnored the following files:")
      ignored = file_outputs[ignore_filename]
      for line in ignored.splitlines():
        print_stdout(line)

  yield CountLinesOfCode(exit_code=0)


def rules():
  return [
      optionable_rule(ClocBinary),
      run_cloc,
    ]
