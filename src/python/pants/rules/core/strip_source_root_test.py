# Copyright 2019 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from pants.engine.legacy.structs import TargetAdaptor, PythonBinaryAdaptor
from pants.rules.core.strip_source_root import SourceRootStrippedSources, strip_source_root
from pants.engine.rules import optionable_rule
from pants.engine.selectors import Get
from pants_test.test_base import TestBase
from pants.source.source_root import SourceRootConfig

class StripSourceRootsTests(TestBase):
  @classmethod
  def rules(cls):
    return super().rules() + [
      strip_source_root,
      optionable_rule(SourceRootConfig)
    ]

  def test_source_roots(self):

    empty_adaptor = TargetAdaptor()
    source_root_config = SourceRootConfig.global_instance()
    output = self.scheduler.product_request(SourceRootStrippedSources, [empty_adaptor, source_root_config])

    print(f"Output: {output}")
    self.assertEqual(1, 1)
    self.assertEqual(output, 5)

