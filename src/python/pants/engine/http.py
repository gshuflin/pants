# Copyright 2019 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from dataclasses import dataclass


@dataclass(frozen=True)
class HttpResponse:
  response_code: int


@dataclass(frozen=True)
class MakeHttpRequest:
  url: str


def create_http_rules():
  return []
