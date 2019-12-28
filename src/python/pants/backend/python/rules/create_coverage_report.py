# Copyright 2019 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

import os
from coverage.sqldata import CoverageData
import coverage
from tempfile import NamedTemporaryFile, TemporaryDirectory
from pants.backend.python.subsystems.pytest import PyTest
from pants.build_graph.address import Address
from pants.engine.addressable import BuildFileAddresses
from pants.engine.fs import Digest, FilesContent
from pants.engine.goal import Goal, GoalSubsystem, LineOriented
from pants.engine.rules import console_rule
from pants.engine.selectors import Get, MultiGet
from pants.rules.core.test import AddressAndTestResult
from pants.util.dirutil import safe_concurrent_creation, safe_mkdir

import warnings
class CoverageOptions(LineOriented, GoalSubsystem):
  name = 'coverage2'

  @classmethod
  def register_options(cls, register):
    super().register_options(register)
    register(
      '--transitive',
      default=True,
      type=bool,
      help='Run dependencies against transitive dependencies of targets specified on the command line.',
    )


class Coverage(Goal):
  subsystem_cls = CoverageOptions


@console_rule(name="Merge coverage reports")
async def merge_coverage_reports(
  addresses: BuildFileAddresses,
  pytest: PyTest,
) -> Coverage:
  """Takes all python test results and generates a single coverage report in dist/coverage."""
  results = await MultiGet(Get[AddressAndTestResult](Address, addr.to_address()) for addr in addresses)
  test_results = [(x.address, x.test_result) for x in results if x.test_result is not None]
  with TemporaryDirectory(dir='dist/coverage') as tmpdir:
    cov_data_file_name = os.path.join(tmpdir, '.coverage')
    with NamedTemporaryFile(dir=tmpdir, delete=False) as coverage_data:
      cov_data = CoverageData(cov_data_file_name)
      for _, test_result in test_results:
        # Each test_result has a Digest of the .coverage file produced when the tests were run.
        # Dump all of these .coverage files into a single directory and then execute `coverage combine`
        # unfortunately all these files are named `.coverage`.
        files = await Get[FilesContent](Digest, test_result.coverage_digest)
        # if test_result.status != Status.SUCCESS:
        #   continue
        for file_content in files:
          with NamedTemporaryFile(dir=tmpdir, delete=False) as f:
            f.write(file_content.content)
            f.flush()
            f.seek(0)
            cov_data.update(CoverageData(basename=f.name))
    import pdb; pdb.set_trace()
    cov = coverage.Coverage(data_file=cov_data_file_name)
    cov.load()
    try:
      cov.html_report(directory='dist/coverage')
    except coverage.misc.CoverageException as e:
      print(e)
      return Coverage(exit_code=1)
  # with warnings.catch_warnings(record=True) as wwwww:
  #   # cov.combine(['dist/coverage'])
  #   cov.combine(coverage_files, strict=True)
  #   # import pdb; pdb.set_trace()
  #   print(coverage_files)
  # cov.save()


  return Coverage(exit_code=0)


def rules():
  return [
    merge_coverage_reports,
  ]
