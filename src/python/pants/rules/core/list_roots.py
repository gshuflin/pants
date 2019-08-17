# Copyright 2019 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

import itertools
from typing import Set

from pants.engine.console import Console
from pants.engine.fs import PathGlobs, Snapshot
from pants.engine.goal import Goal, LineOriented
from pants.engine.rules import console_rule, optionable_rule, rule
from pants.engine.selectors import Get
from pants.source.source_root import SourceRoots, SourceRootConfig

def compute_glob_text(path: str, langs) -> Set[str]:
  output_paths = set()
  path_components = path.split('*')
  if len(path_components) == 1:
    return [path]

  repeat = len(path_components) - 1
  lang_combinations = itertools.product(langs, repeat=repeat)
  for combination in lang_combinations:
    interpolated = [*[item for pair in zip(path_components, combination) for item in pair], path_components[-1]]
    output_paths.add(''.join(interpolated))

  return output_paths


class Roots(LineOriented, Goal):
  """List the repo's registered source roots."""
  name = 'roots'


@rule(SourceRoots, [SourceRootConfig])
def all_roots(source_root_config):
  uncanonicalized_source_roots = source_root_config.get_source_roots().traverse()

  print(f"Uncanonicalized source roots: {len(uncanonicalized_source_roots)}")

  all_paths: Set[str] = set()
  for item in uncanonicalized_source_roots:
    path = item.path
    if path.startswith("^/"):
      glob_texts = {f"**/{path[2:]}"}
    else:
      glob_texts = compute_glob_text(path, item.langs)

    all_paths |= glob_texts

  all_paths_list = [x for x in all_paths]
  path_globs = [PathGlobs(include=(f'{glob_text}/**',)) for glob_text in all_paths_list]
  snapshots = yield [Get(Snapshot, PathGlobs, glob) for glob in path_globs]
  for snapshot in snapshots:
    if len(snapshot.dirs) > 0:
      pass

    print(f"Snapshot:: dirs: {snapshot.dirs}, files: {snapshot.files} ")

  yield []


@console_rule(Roots, [Console, Roots.Options, SourceRootConfig])
def list_roots(console, options, source_root_config):
  #all_roots = source_root_config.get_source_roots().all_roots()
  #print(f"ALL ROOTS LEN: {len(list(all_roots))}")
  all_roots = yield Get(SourceRoots, SourceRootConfig, source_root_config)

  print(f"What does all_roots look like? {all_roots}")
  with Roots.line_oriented(options, console) as (print_stdout, print_stderr):
    for src_root in sorted(all_roots, key=lambda x: x.path):
      all_langs = ','.join(sorted(src_root.langs))
      print_stdout(f"{src_root.path}: {all_langs or '*'}")
  yield Roots(exit_code=0)


def rules():
  return [
      optionable_rule(SourceRootConfig),
      all_roots,
      list_roots,
    ]
