# Copyright 2018 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

import configparser
import json
import os
from io import StringIO
from textwrap import dedent
from typing import List, Optional, Set

import pkg_resources

from pants.backend.python.rules.inject_init import InjectedInitDigest
from pants.backend.python.rules.pex import (
  CreatePex,
  Pex,
  PexInterpreterConstraints,
  PexRequirements,
)
from pants.backend.python.subsystems.pytest import PyTest
from pants.backend.python.subsystems.python_setup import PythonSetup
from pants.backend.python.subsystems.subprocess_environment import SubprocessEncodingEnvironment
from pants.base.build_environment import get_buildroot
from pants.build_graph.address import Address
from pants.engine.fs import Digest, DirectoriesToMerge, FileContent, FilesContent, InputFilesContent
from pants.engine.isolated_process import ExecuteProcessRequest, FallibleExecuteProcessResult
from pants.engine.legacy.graph import BuildFileAddresses, HydratedTarget, TransitiveHydratedTargets
from pants.engine.legacy.structs import PythonTestsAdaptor
from pants.engine.rules import UnionRule, optionable_rule, rule
from pants.engine.selectors import Get, MultiGet
from pants.rules.core.core_test_model import TestResult, TestTarget
from pants.rules.core.strip_source_root import (
  SourceRootsAndSourceRootStrippedSources,
  SourceRootStrippedSources,
)


COVERAGE_PLUGIN_MODULE_NAME = '__coverage_coverage_plugin__'

DEFAULT_COVERAGE_CONFIG = dedent(f"""
  [run]
  branch = True
  timid = False
  """)


def get_file_names(all_target_adaptors):
  def iter_files():
    for adaptor in all_target_adaptors:
      if hasattr(adaptor, 'sources'):
        for file in adaptor.sources.snapshot.files:
          if file.endswith('.py'):
            yield file

  return list(iter_files())


def construct_coverage_config(
  source_roots_and_source_root_stripped_sources: SourceRootsAndSourceRootStrippedSources
) -> bytes:
  config_parser = configparser.ConfigParser()
  config_parser.read_file(StringIO(DEFAULT_COVERAGE_CONFIG))
  ensure_section(config_parser, 'run')
  config_parser.set('run', 'plugins', COVERAGE_PLUGIN_MODULE_NAME)
  # A map from source root stripped source to its source root. eg:
  #  {'pants/testutil/subsystem/util.py': 'src/python'}
  # This is so coverage reports referencing /tmp/alksdjfiwe/pants/testutil/subsystem/util.py can be mapped
  # back to the actual sources they reference when merging coverage reports.
  src_to_target_base = {
    source.source_path: source.source_root for source in source_roots_and_source_root_stripped_sources
  }
  config_parser.add_section(COVERAGE_PLUGIN_MODULE_NAME)
  config_parser.set(COVERAGE_PLUGIN_MODULE_NAME, 'buildroot', get_buildroot())
  config_parser.set(COVERAGE_PLUGIN_MODULE_NAME, 'src_to_target_base', json.dumps(src_to_target_base))
  config = StringIO()
  config_parser.write(config)
  return config.getvalue().encode()


def ensure_section(config_parser: configparser, section: str) -> None:
  """Ensure a section exists in a ConfigParser."""
  if not config_parser.has_section(section):
    config_parser.add_section(section)


def get_coverage_plugin_input():
  return InputFilesContent(
    FilesContent(
      (
        FileContent(
          path=f'{COVERAGE_PLUGIN_MODULE_NAME}.py',
          content=pkg_resources.resource_string(__name__, 'coverage/plugin.py'),
          is_executable=False,
        ),
      )
    )
  )


def get_coveragerc_input(coveragerc_content: bytes):
  return InputFilesContent(
    [
      FileContent(
        path='.coveragerc',
        content='coveragerc_content',
        is_executable=False,
      ),
    ]
  )


def calculate_timeout_seconds(
  *,
  timeouts_enabled: bool,
  target_timeout: Optional[int],
  timeout_default: Optional[int],
  timeout_maximum: Optional[int],
) -> Optional[int]:
  """Calculate the timeout for a test target.

  If a target has no timeout configured its timeout will be set to the default timeout.
  """
  if not timeouts_enabled:
    return None
  if target_timeout is None:
    if timeout_default is None:
      return None
    target_timeout = timeout_default
  if timeout_maximum is not None:
    return min(target_timeout, timeout_maximum)
  return target_timeout


def get_packages_to_cover(coverage: str, source_root_stripped_file_paths: List[str]) -> Set[str]:
  # TODO: Support values other than 'auto'
  if coverage == 'auto':
    return set(
      os.path.dirname(source_root_stripped_source_file_path).replace(os.sep, '.')
      for source_root_stripped_source_file_path in source_root_stripped_file_paths
    )
  return set()

@rule(name="Run pytest")
async def run_python_test(
  test_target: PythonTestsAdaptor,
  pytest: PyTest,
  python_setup: PythonSetup,
  subprocess_encoding_environment: SubprocessEncodingEnvironment
) -> TestResult:
  """Runs pytest for one target."""

  # TODO(7726): replace this with a proper API to get the `closure` for a TransitiveHydratedTarget.
  transitive_hydrated_targets = await Get(
    TransitiveHydratedTargets, BuildFileAddresses((test_target.address,))
  )
  all_targets = transitive_hydrated_targets.closure
  all_target_adaptors = tuple(t.adaptor for t in all_targets)

  interpreter_constraints = PexInterpreterConstraints.create_from_adaptors(
    adaptors=tuple(all_target_adaptors),
    python_setup=python_setup
  )

  output_pytest_requirements_pex_filename = 'pytest-with-requirements.pex'
  requirements = PexRequirements.create_from_adaptors(
    adaptors=all_target_adaptors,
    additional_requirements=pytest.get_requirement_strings()
  )
  plugin_file_digest = None
  if pytest.options.coverage:
    plugin_file_digest = await Get(Digest, InputFilesContent, get_coverage_plugin_input())

  resolved_requirements_pex = await Get(
    Pex, CreatePex(
      output_filename=output_pytest_requirements_pex_filename,
      requirements=requirements,
      interpreter_constraints=interpreter_constraints,
      entry_point="pytest:main",
      input_files_digest=plugin_file_digest,
    )
  )

  source_root_stripped_test_target_sources = await Get(
    SourceRootStrippedSources, Address, test_target.address.to_address()
  )

  source_root_stripped_sources = await MultiGet(
    Get(SourceRootStrippedSources, HydratedTarget, hydrated_target)
    for hydrated_target in all_targets
  )



  stripped_sources_digests = tuple(
    stripped_sources.snapshot.directory_digest for stripped_sources in source_root_stripped_sources
  )
  sources_digest = await Get(Digest, DirectoriesToMerge(directories=stripped_sources_digests))

  inits_digest = await Get(InjectedInitDigest, Digest, sources_digest)

  file_names = get_file_names(all_target_adaptors)
  source_roots_and_source_root_stripped_sources = await MultiGet(
    Get(SourceRootsAndSourceRootStrippedSources, str, file_name)
    for file_name in file_names
  )

  coverage_config_content = construct_coverage_config(source_roots_and_source_root_stripped_sources)
  coveragerc_digest = await Get[Digest](InputFilesContent, get_coveragerc_input(coverage_config_content))

  merged_input_files = await Get(
    Digest,
    DirectoriesToMerge(
      directories=(
        sources_digest,
        inits_digest.directory_digest,
        resolved_requirements_pex.directory_digest,
        coveragerc_digest,
      )
    ),
  )

  test_target_sources_file_names = sorted(source_root_stripped_test_target_sources.snapshot.files)
  timeout_seconds = calculate_timeout_seconds(
    timeouts_enabled=pytest.options.timeouts,
    target_timeout=getattr(test_target, 'timeout', None),
    timeout_default=pytest.options.timeout_default,
    timeout_maximum=pytest.options.timeout_maximum,
  )

  coverage_args = []
  if pytest.options.coverage:
    packages_to_cover = get_packages_to_cover(
      coverage='auto', # TODO: respect the actual option.
      source_root_stripped_file_paths=test_target_sources_file_names,
    )
    coverage_args = [
      '--cov-report=', # To not generate any output. https://pytest-cov.readthedocs.io/en/latest/config.html
    ]
    for package in packages_to_cover:
      coverage_args.extend(['--cov', package])


  request = resolved_requirements_pex.create_execute_request(
    python_setup=python_setup,
    subprocess_encoding_environment=subprocess_encoding_environment,
    pex_path=f'./{output_pytest_requirements_pex_filename}',
    pex_args=(*pytest.get_args(), *coverage_args, *test_target_sources_file_names),
    input_files=merged_input_files,
    output_directories=('.coverage',),
    description=f'Run Pytest for {test_target.address.reference()}',
    timeout_seconds=timeout_seconds if timeout_seconds is not None else 9999
  )
  result = await Get[FallibleExecuteProcessResult](
    ExecuteProcessRequest,
    request
  )
  return TestResult.from_fallible_execute_process_result(result)


def rules():
  return [
    run_python_test,
    UnionRule(TestTarget, PythonTestsAdaptor),
    optionable_rule(PyTest),
    optionable_rule(PythonSetup),
  ]
