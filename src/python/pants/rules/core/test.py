# Copyright 2018 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

import logging
from dataclasses import dataclass
from typing import Optional

from pants.base.exiter import PANTS_FAILED_EXIT_CODE, PANTS_SUCCEEDED_EXIT_CODE
from pants.build_graph.address import Address, BuildFileAddress
from pants.engine.addressable import BuildFileAddresses
from pants.engine.build_files import AddressProvenanceMap
from pants.engine.console import Console
from pants.engine.goal import Goal
from pants.engine.legacy.graph import HydratedTarget
from pants.engine.rules import UnionMembership, console_rule, rule
from pants.engine.selectors import Get, MultiGet
from pants.rules.core.core_test_model import Status, TestResult, TestTarget


# TODO(#6004): use proper Logging singleton, rather than static logger.
logger = logging.getLogger(__name__)


class Test(Goal):
  """Runs tests."""

  name = 'test'


@dataclass(frozen=True)
class AddressAndTestResult:
  address: BuildFileAddress
  test_result: Optional[TestResult]  # If None, target was not a test target.

  @staticmethod
  def is_testable(
    target: HydratedTarget,
    *,
    union_membership: UnionMembership,
    provenance_map: AddressProvenanceMap
  ) -> bool:
    is_valid_target_type = (
      provenance_map.is_single_address(target.address)
      or union_membership.is_member(TestTarget, target.adaptor)
    )
    has_sources = hasattr(target.adaptor, "sources") and target.adaptor.sources.snapshot.files
    return is_valid_target_type and has_sources


@console_rule
async def fast_test(console: Console, addresses: BuildFileAddresses) -> Test:
  results = await MultiGet(Get(AddressAndTestResult, Address, addr.to_address()) for addr in addresses)
  did_any_fail = False
  filtered_results = [(x.address, x.test_result) for x in results if x.test_result is not None]

  for address, test_result in filtered_results:
    if test_result.status == Status.FAILURE:
      did_any_fail = True
    if test_result.stdout:
      result = (console.red(test_result.stdout) if test_result.status == Status.FAILURE
        else test_result.stdout)
      console.print_stdout(f"{address.reference()} stdout:\n{result}")

    if test_result.stderr:
      # NB: we write to stdout, rather than to stderr, to avoid potential issues interleaving the
      # two streams.
      result = (console.red(test_result.stderr) if test_result.status == Status.FAILURE
        else test_result.stderr)
      console.print_stdout(f"{address.reference()} stderr:\n{result}")
  console.print_stdout("\n", end="")

  for address, test_result in filtered_results:
    console.print_stdout('{0:80}.....{1:>10}'.format(
      address.reference(), test_result.status.value))

  if did_any_fail:
    console.print_stderr(console.red('Tests failed'))
    exit_code = PANTS_FAILED_EXIT_CODE
  else:
    exit_code = PANTS_SUCCEEDED_EXIT_CODE

  return Test(exit_code)


@rule
async def coordinator_of_tests(
  target: HydratedTarget,
  union_membership: UnionMembership,
  provenance_map: AddressProvenanceMap
) -> AddressAndTestResult:

  if not AddressAndTestResult.is_testable(
    target, union_membership=union_membership, provenance_map=provenance_map
  ):
    return AddressAndTestResult(target.address, None)

  # TODO(#6004): when streaming to live TTY, rely on V2 UI for this information. When not a
  # live TTY, periodically dump heavy hitters to stderr. See
  # https://github.com/pantsbuild/pants/issues/6004#issuecomment-492699898.
  logger.info("Starting tests: {}".format(target.address.reference()))
  # NB: This has the effect of "casting" a TargetAdaptor to a member of the TestTarget union.
  # The adaptor will always be a member because of the union membership check above, but if
  # it were not it would fail at runtime with a useful error message.
  result = await Get(TestResult, TestTarget, target.adaptor)
  logger.info("Tests {}: {}".format(
    "succeeded" if result.status == Status.SUCCESS else "failed",
    target.address.reference(),
  ))
  return AddressAndTestResult(target.address, result)


def rules():
  return [
      coordinator_of_tests,
      fast_test,
    ]
