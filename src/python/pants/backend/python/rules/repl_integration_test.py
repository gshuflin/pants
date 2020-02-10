# Copyright 2020 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from pants.backend.python.rules.repl import PythonRepl
from pants.testutil.goal_rule_test_base import GoalRuleTestBase


class PythonReplTest(GoalRuleTestBase):
  goal_cls = PythonRepl

  def test_repl_with_targets(self):
    assert 1 == 1

