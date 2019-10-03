# Copyright 2020 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from dataclasses import dataclass
from pathlib import Path
from pants.engine.selectors import Get
from pants.base.build_root import BuildRoot
from pants.engine.addressable import BuildFileAddresses
from pants.engine.console import Console
from pants.engine.goal import Goal, GoalSubsystem
from pants.engine.rules import UnionRule, goal_rule, rule, union
from pants.engine.legacy.graph import HydratedTargets, HydratedTarget
from pants.engine.interactive_runner import InteractiveProcessRequest, InteractiveRunner
from pants.util.contextutil import temporary_dir
from pants.option.global_options import GlobalOptions
from pants.engine.fs import Digest, DirectoryToMaterialize, Workspace


@dataclass(frozen=True)
class ReplBinary:
  digest: Digest
  name: str


@union
class ReplTarget:
  pass

class ReplOptions(GoalSubsystem):
  """Opens a REPL."""
  name = 'repl2'
  required_union_implementations = (ReplTarget,)


class Repl(Goal):
  subsystem_cls = ReplOptions


@goal_rule
async def repl(
    console: Console,
    workspace: Workspace,
    runner: InteractiveRunner,
    targets: HydratedTargets,
    build_root: BuildRoot,
    global_options: GlobalOptions) -> Repl:

  print(f"Hydrated targets: {targets.dependencies}")

  repl_binary = await Get[ReplBinary](HydratedTarget, targets.dependencies[0])

  with temporary_dir(root_dir=global_options.pants_workdir, cleanup=True) as tmpdir:
    path_relative_to_build_root = str(Path(tmpdir).relative_to(build_root.path))
    workspace.materialize_directory(
      DirectoryToMaterialize(repl_binary.digest, path_prefix=path_relative_to_build_root)
    )

    full_path = str(Path(tmpdir, repl_binary.name))
    run_request = InteractiveProcessRequest(
      argv=(full_path,),
      run_in_workspace=True,
    )
  result = runner.run_local_interactive_process(run_request)
  return Repl(result.process_exit_code)


@rule
async def coordinator(target: HydratedTarget) -> ReplBinary:
  repl_binary = await Get[ReplBinary](ReplTarget, target.adaptor)
  return repl_binary

def rules():
  return [
    repl,
    coordinator
  ]


