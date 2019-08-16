# Copyright 2019 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

import json

from pants.rules.core import list_roots
from pants_test.console_rule_test_base import ConsoleRuleTestBase
from pants.rules.core.list_roots import compute_glob_text


class RootsTest(ConsoleRuleTestBase):
  goal_cls = list_roots.Roots

  @classmethod
  def rules(cls):
    return super().rules() + list_roots.rules()

  def test_no_langs(self):
    source_roots = json.dumps({'fakeroot': tuple()})
    self.create_dir('fakeroot')
    self.assert_console_output('fakeroot: *',
      args=[f"--source-source-roots={source_roots}"]
    )

  def test_single_source_root(self):
    source_roots = json.dumps({'fakeroot': ('lang1', 'lang2')})
    self.create_dir('fakeroot')
    self.assert_console_output('fakeroot: lang1,lang2',
        args=[f"--source-source-roots={source_roots}"]
    )

  def test_multiple_source_roots(self):
    source_roots = json.dumps({
      'fakerootA': ('lang1',),
      'fakerootB': ('lang2',)
    })
    self.create_dir('fakerootA')
    self.create_dir('fakerootB')
    self.assert_console_output('fakerootA: lang1', 'fakerootB: lang2',
      args=[f"--source-source-roots={source_roots}"]
    )

  def test_compute_glob_text(self):
    self.assertEqual({'a/python/b', 'a/javascript/b'},
    compute_glob_text('a/*/b', ['python', 'javascript']))

    print(compute_glob_text('a/*/b/*', ['python', 'javascript']))
    self.assertEqual({
      'python/b/python', 'python/b/javascript',
      'javascript/b/python', 'javascript/b/javascript'},
    compute_glob_text('*/b/*', ['python', 'javascript']))


