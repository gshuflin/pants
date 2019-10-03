# Copyright 2020 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from dataclasses import dataclass
from pants.engine.rules import UnionRule, rule
from pants.rules.core.repl import ReplBinary, ReplTarget
from pants.backend.python.subsystems.ipython import IPython
from pants.engine.fs import EMPTY_DIRECTORY_DIGEST
from pants.engine.selectors import Get
from pants.engine.legacy.structs import PythonBinaryAdaptor, PythonTargetAdaptor


@dataclass(frozen=True)
class PythonRepl:
  pass


@rule
def run_python_repl(target: PythonTargetAdaptor) -> ReplBinary:
  repl_binary = ReplBinary(
    digest=EMPTY_DIRECTORY_DIGEST,
    name="/usr/bin/python"
  )
  return repl_binary


def rules():
  return [
    run_python_repl,
    UnionRule(ReplTarget, PythonTargetAdaptor),
  ]

