# Copyright 2019 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from pants.engine.legacy.structs import TargetAdaptor, PythonBinaryAdaptor, PythonTestsAdaptor
from pants.rules.core.strip_source_root import SourceRootStrippedSources, strip_source_root
from pants.engine.rules import optionable_rule
from pants.engine.selectors import Get
from pants_test.test_base import TestBase
from pants.source.source_root import SourceRootConfig
from unittest.mock import Mock
from pants.build_graph.address import Address, BuildFileAddress
from pants.engine.legacy.graph import HydratedTarget, TransitiveHydratedTargets

class StripSourceRootsTests(TestBase):

  @classmethod
  def rules(cls):
    return super().rules() + [
      strip_source_root,
      optionable_rule(SourceRootConfig)
    ]

  def test_source_roots(self):

    adaptor = PythonTestsAdaptor(type_alias='python_tests')
    target = HydratedTarget(Address.parse("some/target"), adaptor, ())
    source_root_config = SourceRootConfig.global_instance()

    output = self.scheduler.product_request(SourceRootStrippedSources, [target, source_root_config])

    print(f"Output: {output}")
    self.assertEqual(1, 1)
    self.assertEqual(output, 5)

