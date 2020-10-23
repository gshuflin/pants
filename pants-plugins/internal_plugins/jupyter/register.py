# Copyright 2018 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

import logging

from textwrap import dedent
from pathlib import PurePath

from pants.engine.addresses import Addresses
from pants.engine.console import Console
from pants.option.global_options import GlobalOptions
from pants.engine.goal import Goal, GoalSubsystem
from pants.engine.fs import CreateDigest, Digest, MergeDigests, FileContent
from pants.engine.rules import Get, MultiGet, QueryRule, collect_rules, goal_rule
from pants.engine.process import InteractiveProcess, InteractiveRunner
from pants.backend.python.util_rules.pex import (
    Pex,
    PexInterpreterConstraints,
    PexRequest,
    PexRequirements,
)
from pants.backend.python.util_rules.pex_from_targets import PexFromTargetsRequest
from pants.backend.python.util_rules.python_sources import (
    PythonSourceFiles,
    PythonSourceFilesRequest,
)

from pants.engine.target import (
    Field,
    Target,
    Targets,
    TransitiveTargets,
    TransitiveTargetsRequest,
    TargetsWithOrigins,
)

logger = logging.getLogger(__name__)

class JupyterSubsystem(GoalSubsystem):
    """Launch a Jupyter Notebook app (repl)."""

    name = "jupyter"
    targets: Targets

    @classmethod
    def is_valid(cls, tgt: Target) -> bool:
        return tgt.has_fields(cls.required_fields)


class Jupyter(Goal):
    subsystem_cls = JupyterSubsystem

@goal_rule
async def jupyter(
    all_specified_addresses: Addresses,
    global_options: GlobalOptions,
    interactive_runner: InteractiveRunner,
) -> Jupyter:

    logger.warning("RUNNING JUPYTER desu")

        # from jupyter_stubber-v1.0.0

    jupyter_requirements = [
        "ipykernel==5.3.4",
        "ipython==7.8.0",
        "jupyter_console==6.0.0",
        "nbconvert==5.6.1",
        "nbformat==4.4.0",
        "notebook==6.0.1",
        "pyzmq==18.1.1",
        "tornado==6.0.4",
    ]

    for addr in all_specified_addresses:
        logger.debug("address: {}".format(addr))

    logger.info("jupyter_requirements: {}".format(jupyter_requirements))

    LAUNCHER_FILE = FileContent(
        "__jupyter_launcher.py",
        dedent(
            """\
            import os
            import sys
            import re

            from notebook.notebookapp import main

            os.environ["PYTHONPATH"] = ":".join(sys.path)
            sys.argv[0] = re.sub(r"(-script\.pyw?|\.exe)?$", "", sys.argv[0])
            sys.exit(main())
            """
        ).encode()
    )

    launcher_digest = await Get(Digest, CreateDigest([LAUNCHER_FILE]))

    jupyter_stubber_request = Get(
        Pex,
        PexRequest(
            sources=launcher_digest,
            output_filename="jupyter-py3.pex",
            requirements=PexRequirements(jupyter_requirements),
            internal_only=True,
            #interpreter_constraints=PexInterpreterConstraints(
            #    ["CPython>=2.7.17,<3", "CPython>=3.6"]
            #),
            entry_point=PurePath(LAUNCHER_FILE.path).stem,
        ),
    )

    transitive_targets = await Get(
        TransitiveTargets, TransitiveTargetsRequest(all_specified_addresses)
    )

    sources_request = Get(
        PythonSourceFiles,
        PythonSourceFilesRequest(transitive_targets.closure, include_files=True),
    )

    jupyter_stubber_pex, sources = await MultiGet(
        jupyter_stubber_request, sources_request
    )
    logger.info("jupyter_stubber_pex: {}".format(jupyter_stubber_pex))
    logger.info("sources: {}".format(sources))

    merged_digest = await Get(
        Digest,
        MergeDigests(
            (jupyter_stubber_pex.digest, sources.source_files.snapshot.digest)
        ),
    )

    result = interactive_runner.run(
        InteractiveProcess(
            argv=[jupyter_stubber_pex.name],
            env=None,
            input_digest=merged_digest,
            hermetic_env=False,  # XXX setting this to True results in No such file error
        )
    )

    # XXX Jupyter Exit Issue
    # Exiting from the above with ctrl-c causes the pantsd to exit with
    # exit_code 1.
    #
    # Never gets to this part.
    #
    # This might be resolved by using newer versions of notebook that can be
    # exited from the web GUI. Or it might be fixed by pantsd handling
    # interrupts differently.

    exit_code = result.exit_code
    logger.info("exit code is {}".format(exit_code))  # XXX make debug
    return Jupyter(exit_code=exit_code)

def rules():
    return [*collect_rules(), QueryRule(JupyterSubsystem, [])]

