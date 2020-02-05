# Copyright 2020 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

import logging
from dataclasses import dataclass
from pants.engine.rules import UnionRule, rule
from pants.rules.core.repl import ReplBinary, ReplTarget
from pants.backend.python.subsystems.ipython import IPython
from pants.engine.fs import EMPTY_DIRECTORY_DIGEST
from pants.engine.selectors import Get
from pants.engine.legacy.structs import PythonBinaryAdaptor, PythonTargetAdaptor
from pants.backend.python.rules.pex import Pex
from pants.backend.python.rules.pex_from_target_closure import CreatePexFromTargetClosure
from pants.engine.addressable import BuildFileAddresses

logger = logging.getLogger(__name__)


@rule
async def run_python_repl(target: PythonTargetAdaptor) -> ReplBinary:
  logger.info("Starting Python REPL")
  addresses = BuildFileAddresses((target.address,))
  create_pex = CreatePexFromTargetClosure(
    build_file_addresses=addresses,
    output_filename="python-repl.pex",
    entry_point=None,
  )

  pex = await Get[Pex](CreatePexFromTargetClosure, create_pex)

  repl_binary = ReplBinary(
    digest=pex.directory_digest,
    name=pex.output_filename
  )
  return repl_binary


def rules():
  return [
    run_python_repl,
    UnionRule(ReplTarget, PythonTargetAdaptor),
  ]

