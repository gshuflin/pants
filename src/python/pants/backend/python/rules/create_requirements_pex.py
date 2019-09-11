# Copyright 2019 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from pants.backend.python.rules.create_pex import PexRequest, PexOutput
from pants.engine.rules import rule
from pants.engine.selectors import Get
from pants.util.objects import datatype, hashable_string_list, string_optional, string_type
from pants.engine.fs import Digest

class RequirementsPexRequest(datatype([
  ('output_filename', string_type),
  ('requirements', hashable_string_list),
  ('interpreter_constraints', hashable_string_list),
  ('entry_point', string_optional),
])):
  pass

class RequirementsPex(datatype([('directory_digest', Digest)])):
  pass

@rule(RequirementsPex, [RequirementsPexRequest])
def create_requirements_pex(req):
  """Returns a PEX with the given requirements, optional entry point, and optional
  interpreter constraints."""

  pex_request = PexRequest(
    output_filename = req.output_filename,
    requirements = req.requirements,
    interpreter_constraints = req.interpreter_constraints,
    entry_point = req.entry_point,
    input_digests = tuple(),
  )
  pex_output = yield Get(PexOutput, PexRequest, pex_request)
  yield RequirementsPex(directory_digest=pex_output.directory_digest)

def rules():
  return [
    create_requirements_pex,
  ]
