# Copyright 2019 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

import os

from pants.backend.graph_info.subsystems.cloc_binary import ClocBinary
from pants.engine.console import Console
from pants.engine.selectors import Get
from pants.engine.fs import Digest, EMPTY_DIRECTORY_DIGEST, FileContent, FilesContent, PathGlobs, PathGlobsAndRoot, Snapshot
from pants.engine.goal import Goal, LineOriented
from pants.engine.isolated_process import ExecuteProcessRequest, ExecuteProcessResult
from pants.engine.legacy.graph import HydratedTargets
from pants.engine.rules import console_rule, optionable_rule
from pants.util.contextutil import temporary_dir


class CountLinesOfCode(LineOriented, Goal):
  """
  this is gonna need to work something like:


  @rule(ClocOutput, [ClocBinary])
def f(cloc_binary):
  input_file = yield Get(SingleFileSnapshot, TextFile(<arguments, idk>)
  full_snapshot = yield Get(Snapshot, MergedDirectories([input_file.single_file_path, cloc_binary.digest]))
  cloc_execution = yield Get(ExecuteProcessResult, ExecuteProcessRequest(..., input_files=full_snapshot.directory_digest))
  yield ClocOutput(cloc_execution)
  """
  name = 'clocv2'

  @classmethod
  def register_options(cls, register):
    super().register_options(register)
    register('--transitive', type=bool, fingerprint=True, default=True,
             help='Operate on the transitive dependencies of the specified targets.  '
                  'Unset to operate only on the specified targets.')
    register('--ignored', type=bool, fingerprint=True,
             help='Show information about files ignored by cloc.')


@console_rule(CountLinesOfCode, [Console, CountLinesOfCode.Options, ClocBinary, HydratedTargets])
def run_cloc(console, options, cloc_binary, hydrated_targets):
  transitive = options.values.transitive

  all_target_adaptors = [t.adaptor for t in hydrated_targets]

  source_paths: Set[str] = set()
  for t in all_target_adaptors:
    sources = getattr(t, 'sources', None)
    if sources is not None:
      for file in sources.snapshot.files:
        source_paths.add(file)

  print(f"Source paths: {source_paths}")

  cloc_binary_path = cloc_binary.select()

  with temporary_dir() as tmpdir:
    list_file = os.path.join(tmpdir, 'input_files_list')
    with open(list_file, 'w') as list_file_out:
      for path in sorted(source_paths):
        list_file_out.write(f'{path}\n')
    ignore_file = os.path.join(tmpdir, 'ignored')
    with open(ignore_file, 'w') as ignore_file_out:
      pass

  # neither file is input they are all outputs
  cmd = (
    '/usr/bin/perl',
    cloc_binary_path,
    '--skip-uniqueness',
    '--ignored=ignored', # this means write the names and reasons of ignored files to this file
    '--list-file=input_files_list',
    '--report-file=report',  # this means write the report to this file *instead* of stdout
  )

  print(f"Cloc binary path: {cloc_binary_path}")

  cmd=("/bin/bash", "-c", "echo -n 'YOLO SWAGG my brah' > myfile && echo -n 'mohonoro' > myfile2")

  digest : Digest = EMPTY_DIRECTORY_DIGEST
  req = ExecuteProcessRequest(
    argv=cmd,
    input_files=digest,
    output_files=('myfile', 'myfile2'), #maybe I don't need report ?
    description='cloc yo',
  )
  exec_result = yield Get(ExecuteProcessResult, ExecuteProcessRequest, req)
  digest = exec_result.output_directory_digest

  file_content = yield Get(FilesContent, Digest, digest)

  test_file_content = FileContent("file_name", b"contents")
  new_digest = yield Get(Digest, FilesContent((test_file_content,)))
  print(f"New digest: {new_digest}")

  with CountLinesOfCode.line_oriented(options, console) as (print_stdout, print_stderr):
    print_stdout(f"Gonna run command: {cmd}")
    print_stdout(f"The resul: {exec_result}")
    print_stdout(f'Digest: {digest}')
    print_stdout(f"LE CONTENT: {file_content}")
    print_stderr("test error")
    print_stdout(f"Transitive is {transitive}")

  yield CountLinesOfCode(exit_code=0)


def rules():
  return [
      optionable_rule(ClocBinary),
      run_cloc,
    ]

