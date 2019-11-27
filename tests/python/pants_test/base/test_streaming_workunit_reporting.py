# Copyright 2019 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from pants.testutil.pants_run_integration_test import PantsRunIntegrationTest

class StreamingWorkunitHandler(PantsRunIntegrationTest):


  --streaming-workunits-handlers="['pants.reporting.workunits.Workunits']" binary examples/src/python/example/hello/main/
