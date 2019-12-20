# Copyright 2019 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

import os

from pants.backend.python.subsystems.pytest import PyTest
from pants.build_graph.address import Address
from pants.engine.addressable import BuildFileAddresses
from pants.engine.fs import Digest, FilesContent
from pants.engine.goal import Goal, GoalSubsystem, LineOriented
from pants.engine.rules import console_rule
from pants.engine.selectors import Get, MultiGet
from pants.rules.core.test import AddressAndTestResult


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
  for address, test_result in test_results:
    # Each test_result has a Digest of the .coverage file produced when the tests were run.
    # Dump all of these .coverage files into a single directory and then execute `coverage combine`
    # unfortunately all these files are named `.coverage`.
    filename = address.spec_path.replace(os.path.sep, '.')
    files = await Get[FilesContent](Digest, test_result.coverage_digest)
    # import pdb; pdb.set_trace()
    for file_content in files:
      with open(f'dist/coverage/.coverage_{filename}', 'wb') as f:
        f.write(file_content.content)

  return Coverage(exit_code=0)


def rules():
  return [
    merge_coverage_reports,
  ]
