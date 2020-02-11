# Copyright 2020 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from pants.backend.python.rules import repl
from pants.backend.python.rules.repl import PythonRepl
from pants.testutil.goal_rule_test_base import GoalRuleTestBase

from pants.base.build_root import BuildRoot
from pants.engine.interactive_runner import InteractiveRunner, create_interactive_runner_rules
from pants.engine.rules import RootRule, subsystem_rule
from pants.option.global_options import GlobalOptions
from pants.engine.legacy.options_parsing import create_options_parsing_rules
from pants.engine.legacy.graph import create_legacy_graph_tasks
from pants.engine.fs import create_fs_rules
from pants.engine.isolated_process import create_process_rules
from pants.engine.build_files import create_graph_rules
from pants.engine.legacy.structs import rules as structs_rules
from pants.scm.subsystems.changed import rules as changed_rules
from pants.engine.platform import create_platform_rules
from pants.backend.python.rules import (
  pex,
  pex_from_target_closure,
  prepare_chrooted_python_sources,
  inject_init,
  download_pex_bin,
)
from pants.rules.core import strip_source_root
from pants.backend.python.subsystems import python_native_code, subprocess_environment
from pants.engine.mapper import AddressMapper
from pants_test.engine.examples.parsers import JsonParser
from pants.testutil.engine.util import TARGET_TABLE


class PythonReplTest(GoalRuleTestBase):
  goal_cls = PythonRepl

  @classmethod
  def rules(cls):

    address_mapper = AddressMapper(JsonParser(TARGET_TABLE))

    return (
      *super().rules(),
      *repl.rules(),
      *download_pex_bin.rules(),
      *inject_init.rules(),
      *pex.rules(),
      *pex_from_target_closure.rules(),
      *prepare_chrooted_python_sources.rules(),
      *python_native_code.rules(),
      *strip_source_root.rules(),
      *subprocess_environment.rules(),
    )

  def test_repl_with_targets(self):

    additional_params = [
      InteractiveRunner(self.scheduler),
    ]

    self.execute_rule(args=["src/python:some_lib"], additional_params=additional_params)
    assert 1 == 1

