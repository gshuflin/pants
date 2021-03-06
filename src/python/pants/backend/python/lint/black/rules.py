# Copyright 2019 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

import re
from dataclasses import dataclass
from pathlib import PurePath
from typing import Tuple

from pants.backend.python.lint.black.subsystem import Black
from pants.backend.python.lint.python_fmt import PythonFmtRequest
from pants.backend.python.rules import pex
from pants.backend.python.rules.pex import (
    Pex,
    PexInterpreterConstraints,
    PexProcess,
    PexRequest,
    PexRequirements,
)
from pants.backend.python.target_types import PythonSources
from pants.core.goals.fmt import FmtResult
from pants.core.goals.lint import LintRequest, LintResult, LintResults
from pants.core.util_rules import source_files, stripped_source_files
from pants.core.util_rules.source_files import SourceFiles, SourceFilesRequest
from pants.engine.fs import Digest, GlobMatchErrorBehavior, MergeDigests, PathGlobs
from pants.engine.process import FallibleProcessResult, Process, ProcessResult
from pants.engine.rules import Get, MultiGet, collect_rules, rule
from pants.engine.target import FieldSet
from pants.engine.unions import UnionRule
from pants.util.logging import LogLevel
from pants.util.strutil import pluralize


@dataclass(frozen=True)
class BlackFieldSet(FieldSet):
    required_fields = (PythonSources,)

    sources: PythonSources


class BlackRequest(PythonFmtRequest, LintRequest):
    field_set_type = BlackFieldSet


@dataclass(frozen=True)
class SetupRequest:
    request: BlackRequest
    check_only: bool


@dataclass(frozen=True)
class Setup:
    process: Process
    original_digest: Digest


def generate_args(*, source_files: SourceFiles, black: Black, check_only: bool) -> Tuple[str, ...]:
    args = []
    if check_only:
        args.append("--check")
    if black.config:
        args.extend(["--config", black.config])
    args.extend(black.args)
    # NB: For some reason, Black's --exclude option only works on recursive invocations, meaning
    # calling Black on a directory(s) and letting it auto-discover files. However, we don't want
    # Black to run over everything recursively under the directory of our target, as Black should
    # only touch files directly specified. We can use `--include` to ensure that Black only
    # operates on the files we actually care about.
    args.extend(["--include", "|".join(re.escape(f) for f in source_files.files)])
    args.extend(PurePath(f).parent.as_posix() for f in source_files.files)
    return tuple(args)


@rule(level=LogLevel.DEBUG)
async def setup_black(setup_request: SetupRequest, black: Black) -> Setup:
    requirements_pex_request = Get(
        Pex,
        PexRequest(
            output_filename="black.pex",
            internal_only=True,
            requirements=PexRequirements(black.all_requirements),
            interpreter_constraints=PexInterpreterConstraints(black.interpreter_constraints),
            entry_point=black.entry_point,
        ),
    )

    config_digest_request = Get(
        Digest,
        PathGlobs(
            globs=[black.config] if black.config else [],
            glob_match_error_behavior=GlobMatchErrorBehavior.error,
            description_of_origin="the option `--black-config`",
        ),
    )

    source_files_request = Get(
        SourceFiles,
        SourceFilesRequest(field_set.sources for field_set in setup_request.request.field_sets),
    )

    source_files, requirements_pex, config_digest = await MultiGet(
        source_files_request, requirements_pex_request, config_digest_request
    )
    source_files_snapshot = (
        source_files.snapshot
        if setup_request.request.prior_formatter_result is None
        else setup_request.request.prior_formatter_result
    )

    input_digest = await Get(
        Digest,
        MergeDigests((source_files_snapshot.digest, requirements_pex.digest, config_digest)),
    )

    process = await Get(
        Process,
        PexProcess(
            requirements_pex,
            argv=generate_args(
                source_files=source_files, black=black, check_only=setup_request.check_only
            ),
            input_digest=input_digest,
            output_files=source_files_snapshot.files,
            description=f"Run Black on {pluralize(len(setup_request.request.field_sets), 'file')}.",
            level=LogLevel.DEBUG,
        ),
    )
    return Setup(process, original_digest=source_files_snapshot.digest)


@rule(desc="Format with Black", level=LogLevel.DEBUG)
async def black_fmt(field_sets: BlackRequest, black: Black) -> FmtResult:
    if black.skip:
        return FmtResult.skip(formatter_name="Black")
    setup = await Get(Setup, SetupRequest(field_sets, check_only=False))
    result = await Get(ProcessResult, Process, setup.process)
    return FmtResult.from_process_result(
        result,
        original_digest=setup.original_digest,
        formatter_name="Black",
        strip_chroot_path=True,
    )


@rule(desc="Lint with Black", level=LogLevel.DEBUG)
async def black_lint(field_sets: BlackRequest, black: Black) -> LintResults:
    if black.skip:
        return LintResults([], linter_name="Black")
    setup = await Get(Setup, SetupRequest(field_sets, check_only=True))
    result = await Get(FallibleProcessResult, Process, setup.process)
    return LintResults(
        [LintResult.from_fallible_process_result(result, strip_chroot_path=True)],
        linter_name="Black",
    )


def rules():
    return [
        *collect_rules(),
        UnionRule(PythonFmtRequest, BlackRequest),
        UnionRule(LintRequest, BlackRequest),
        *pex.rules(),
        *source_files.rules(),
        *stripped_source_files.rules(),
    ]
