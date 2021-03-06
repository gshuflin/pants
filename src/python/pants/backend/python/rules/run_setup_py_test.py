# Copyright 2019 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

import json
import textwrap
from typing import Iterable, Type

import pytest

from pants.backend.python.python_artifact import PythonArtifact
from pants.backend.python.rules import python_sources
from pants.backend.python.rules.run_setup_py import (
    AmbiguousOwnerError,
    DependencyOwner,
    ExportedTarget,
    ExportedTargetRequirements,
    InvalidEntryPoint,
    InvalidSetupPyArgs,
    NoOwnerError,
    OwnedDependencies,
    OwnedDependency,
    SetupPyChroot,
    SetupPyChrootRequest,
    SetupPySources,
    SetupPySourcesRequest,
    generate_chroot,
    get_exporting_owner,
    get_owned_dependencies,
    get_requirements,
    get_sources,
    validate_args,
)
from pants.backend.python.target_types import (
    PythonBinary,
    PythonDistribution,
    PythonLibrary,
    PythonRequirementLibrary,
)
from pants.build_graph.build_file_aliases import BuildFileAliases
from pants.core.target_types import Files, Resources
from pants.core.util_rules import source_files, stripped_source_files
from pants.engine.addresses import Address
from pants.engine.fs import Snapshot
from pants.engine.internals.scheduler import ExecutionError
from pants.engine.rules import QueryRule
from pants.engine.target import Target, Targets, WrappedTarget
from pants.option.options_bootstrapper import OptionsBootstrapper
from pants.testutil.option_util import create_options_bootstrapper
from pants.testutil.test_base import TestBase

_namespace_decl = "__import__('pkg_resources').declare_namespace(__name__)"


class TestSetupPyBase(TestBase):
    @classmethod
    def alias_groups(cls) -> BuildFileAliases:
        return BuildFileAliases(objects={"setup_py": PythonArtifact})

    @classmethod
    def target_types(cls):
        return [
            PythonBinary,
            PythonDistribution,
            PythonLibrary,
            PythonRequirementLibrary,
            Resources,
            Files,
        ]

    def tgt(self, addr: str) -> Target:
        return self.request_product(
            WrappedTarget, [Address.parse(addr), create_options_bootstrapper()]
        ).target


class TestGenerateChroot(TestSetupPyBase):
    @classmethod
    def rules(cls):
        return (
            *super().rules(),
            generate_chroot,
            get_sources,
            get_requirements,
            get_owned_dependencies,
            get_exporting_owner,
            *python_sources.rules(),
            QueryRule(SetupPyChroot, (SetupPyChrootRequest, OptionsBootstrapper)),
        )

    def assert_chroot(self, expected_files, expected_setup_kwargs, addr):
        chroot = self.request_product(
            SetupPyChroot,
            [
                SetupPyChrootRequest(ExportedTarget(self.tgt(addr)), py2=False),
                create_options_bootstrapper(),
            ],
        )
        snapshot = self.request_product(Snapshot, [chroot.digest])
        assert sorted(expected_files) == sorted(snapshot.files)
        kwargs = json.loads(chroot.setup_keywords_json)
        assert expected_setup_kwargs == kwargs

    def assert_error(self, addr: str, exc_cls: Type[Exception]):
        with pytest.raises(ExecutionError) as excinfo:
            self.request_product(
                SetupPyChroot,
                [
                    SetupPyChrootRequest(ExportedTarget(self.tgt(addr)), py2=False),
                    create_options_bootstrapper(),
                ],
            )
        ex = excinfo.value
        assert len(ex.wrapped_exceptions) == 1
        assert type(ex.wrapped_exceptions[0]) == exc_cls

    def test_generate_chroot(self) -> None:
        self.create_file(
            "src/python/foo/bar/baz/BUILD",
            textwrap.dedent(
                """
            python_distribution(
                name="baz-dist",
                dependencies=[':baz'],
                provides=setup_py(
                    name='baz',
                    version='1.1.1'
                )
            )

            python_library()
            """
            ),
        )
        self.create_file("src/python/foo/bar/baz/baz.py", "")
        self.create_file(
            "src/python/foo/qux/BUILD",
            textwrap.dedent(
                """
                python_library()

                python_binary(name="bin", entry_point="foo.qux.bin")
                """
            ),
        )
        self.create_file("src/python/foo/qux/__init__.py", "")
        self.create_file("src/python/foo/qux/qux.py", "")
        self.create_file("src/python/foo/resources/BUILD", 'resources(sources=["js/code.js"])')
        self.create_file("src/python/foo/resources/js/code.js", "")
        self.create_file("files/BUILD", 'files(sources=["README.txt"])')
        self.create_file("files/README.txt", "")
        self.create_file(
            "src/python/foo/BUILD",
            textwrap.dedent(
                """
                python_distribution(
                    name='foo-dist',
                    dependencies=[
                        ':foo',
                    ],
                    provides=setup_py(
                        name='foo', version='1.2.3'
                    ).with_binaries(
                        foo_main='src/python/foo/qux:bin'
                    )
                )

                python_library(
                    dependencies=[
                        'src/python/foo/bar/baz',
                        'src/python/foo/qux',
                        'src/python/foo/resources',
                        'files',
                    ]
                )
                """
            ),
        )
        self.create_file("src/python/foo/__init__.py", _namespace_decl)
        self.create_file("src/python/foo/foo.py", "")
        self.assert_chroot(
            [
                "src/files/README.txt",
                "src/foo/qux/__init__.py",
                "src/foo/qux/qux.py",
                "src/foo/resources/js/code.js",
                "src/foo/__init__.py",
                "src/foo/foo.py",
                "setup.py",
                "MANIFEST.in",
            ],
            {
                "name": "foo",
                "version": "1.2.3",
                "package_dir": {"": "src"},
                "packages": ["foo", "foo.qux"],
                "namespace_packages": ["foo"],
                "package_data": {"foo": ["resources/js/code.js"]},
                "install_requires": ["baz==1.1.1"],
                "entry_points": {"console_scripts": ["foo_main=foo.qux.bin"]},
            },
            "src/python/foo:foo-dist",
        )

    def test_invalid_binary(self) -> None:
        self.create_file(
            "src/python/invalid_binary/BUILD",
            textwrap.dedent(
                """
                python_library(name='not_a_binary', sources=[])
                python_binary(name='no_entrypoint')
                python_distribution(
                    name='invalid_bin1',
                    provides=setup_py(
                        name='invalid_bin1', version='1.1.1'
                    ).with_binaries(foo=':not_a_binary')
                )
                python_distribution(
                    name='invalid_bin2',
                    provides=setup_py(
                        name='invalid_bin2', version='1.1.1'
                    ).with_binaries(foo=':no_entrypoint')
                )
                """
            ),
        )

        self.assert_error("src/python/invalid_binary:invalid_bin1", InvalidEntryPoint)
        self.assert_error("src/python/invalid_binary:invalid_bin2", InvalidEntryPoint)


class TestGetSources(TestSetupPyBase):
    @classmethod
    def rules(cls):
        return (
            *super().rules(),
            get_sources,
            *source_files.rules(),
            *stripped_source_files.rules(),
            *python_sources.rules(),
            QueryRule(SetupPySources, (SetupPySourcesRequest, OptionsBootstrapper)),
        )

    def assert_sources(
        self,
        expected_files,
        expected_packages,
        expected_namespace_packages,
        expected_package_data,
        addrs,
    ):
        srcs = self.request_product(
            SetupPySources,
            [
                SetupPySourcesRequest(Targets([self.tgt(addr) for addr in addrs]), py2=False),
                create_options_bootstrapper(),
            ],
        )
        chroot_snapshot = self.request_product(Snapshot, [srcs.digest])

        assert sorted(expected_files) == sorted(chroot_snapshot.files)
        assert sorted(expected_packages) == sorted(srcs.packages)
        assert sorted(expected_namespace_packages) == sorted(srcs.namespace_packages)
        assert expected_package_data == dict(srcs.package_data)

    def test_get_sources(self) -> None:
        self.create_file(
            "src/python/foo/bar/baz/BUILD",
            textwrap.dedent(
                """
                python_library(name='baz1', sources=['baz1.py'])
                python_library(name='baz2', sources=['baz2.py'])
                """
            ),
        )
        self.create_file("src/python/foo/bar/baz/baz1.py", "")
        self.create_file("src/python/foo/bar/baz/baz2.py", "")
        self.create_file("src/python/foo/bar/__init__.py", _namespace_decl)
        self.create_file("src/python/foo/qux/BUILD", "python_library()")
        self.create_file("src/python/foo/qux/__init__.py", "")
        self.create_file("src/python/foo/qux/qux.py", "")
        self.create_file("src/python/foo/resources/BUILD", 'resources(sources=["js/code.js"])')
        self.create_file("src/python/foo/resources/js/code.js", "")
        self.create_file("src/python/foo/__init__.py", "")

        self.assert_sources(
            expected_files=["foo/bar/baz/baz1.py", "foo/bar/__init__.py", "foo/__init__.py"],
            expected_packages=["foo", "foo.bar", "foo.bar.baz"],
            expected_namespace_packages=["foo.bar"],
            expected_package_data={},
            addrs=["src/python/foo/bar/baz:baz1"],
        )

        self.assert_sources(
            expected_files=["foo/bar/baz/baz2.py", "foo/bar/__init__.py", "foo/__init__.py"],
            expected_packages=["foo", "foo.bar", "foo.bar.baz"],
            expected_namespace_packages=["foo.bar"],
            expected_package_data={},
            addrs=["src/python/foo/bar/baz:baz2"],
        )

        self.assert_sources(
            expected_files=["foo/qux/qux.py", "foo/qux/__init__.py", "foo/__init__.py"],
            expected_packages=["foo", "foo.qux"],
            expected_namespace_packages=[],
            expected_package_data={},
            addrs=["src/python/foo/qux"],
        )

        self.assert_sources(
            expected_files=[
                "foo/bar/baz/baz1.py",
                "foo/bar/__init__.py",
                "foo/qux/qux.py",
                "foo/qux/__init__.py",
                "foo/__init__.py",
                "foo/resources/js/code.js",
            ],
            expected_packages=["foo", "foo.bar", "foo.bar.baz", "foo.qux"],
            expected_namespace_packages=["foo.bar"],
            expected_package_data={"foo": ("resources/js/code.js",)},
            addrs=["src/python/foo/bar/baz:baz1", "src/python/foo/qux", "src/python/foo/resources"],
        )

        self.assert_sources(
            expected_files=[
                "foo/bar/baz/baz1.py",
                "foo/bar/baz/baz2.py",
                "foo/bar/__init__.py",
                "foo/qux/qux.py",
                "foo/qux/__init__.py",
                "foo/__init__.py",
                "foo/resources/js/code.js",
            ],
            expected_packages=["foo", "foo.bar", "foo.bar.baz", "foo.qux"],
            expected_namespace_packages=["foo.bar"],
            expected_package_data={"foo": ("resources/js/code.js",)},
            addrs=[
                "src/python/foo/bar/baz:baz1",
                "src/python/foo/bar/baz:baz2",
                "src/python/foo/qux",
                "src/python/foo/resources",
            ],
        )


class TestGetRequirements(TestSetupPyBase):
    @classmethod
    def rules(cls):
        return (
            *super().rules(),
            get_requirements,
            get_owned_dependencies,
            get_exporting_owner,
            QueryRule(ExportedTargetRequirements, (DependencyOwner, OptionsBootstrapper)),
        )

    def assert_requirements(self, expected_req_strs, addr):
        reqs = self.request_product(
            ExportedTargetRequirements,
            [DependencyOwner(ExportedTarget(self.tgt(addr))), create_options_bootstrapper()],
        )
        assert sorted(expected_req_strs) == list(reqs)

    def test_get_requirements(self) -> None:
        self.create_file(
            "3rdparty/BUILD",
            textwrap.dedent(
                """
                python_requirement_library(
                    name='ext1',
                    requirements=['ext1==1.22.333'],
                )
                python_requirement_library(
                    name='ext2',
                    requirements=['ext2==4.5.6'],
                )
                python_requirement_library(
                    name='ext3',
                    requirements=['ext3==0.0.1'],
                )
                """
            ),
        )
        self.create_file(
            "src/python/foo/bar/baz/BUILD",
            "python_library(dependencies=['3rdparty:ext1'], sources=[])",
        )
        self.create_file(
            "src/python/foo/bar/qux/BUILD",
            "python_library(dependencies=['3rdparty:ext2', 'src/python/foo/bar/baz'], sources=[])",
        )
        self.create_file(
            "src/python/foo/bar/BUILD",
            textwrap.dedent(
                """
                python_distribution(
                    name='bar-dist',
                    dependencies=[':bar'],
                    provides=setup_py(name='bar', version='9.8.7'),
                )

                python_library(
                    sources=[],
                    dependencies=['src/python/foo/bar/baz', 'src/python/foo/bar/qux'],
                )
              """
            ),
        )
        self.create_file(
            "src/python/foo/corge/BUILD",
            textwrap.dedent(
                """
                python_distribution(
                    name='corge-dist',
                    # Tests having a 3rdparty requirement directly on a python_distribution.
                    dependencies=[':corge', '3rdparty:ext3'],
                    provides=setup_py(name='corge', version='2.2.2'),
                )

                python_library(
                    sources=[],
                    dependencies=['src/python/foo/bar'],
                )
                """
            ),
        )

        self.assert_requirements(["ext1==1.22.333", "ext2==4.5.6"], "src/python/foo/bar:bar-dist")
        self.assert_requirements(["ext3==0.0.1", "bar==9.8.7"], "src/python/foo/corge:corge-dist")


class TestGetOwnedDependencies(TestSetupPyBase):
    @classmethod
    def rules(cls):
        return (
            *super().rules(),
            get_owned_dependencies,
            get_exporting_owner,
            QueryRule(OwnedDependencies, (DependencyOwner, OptionsBootstrapper)),
        )

    def assert_owned(self, owned: Iterable[str], exported: str):
        assert sorted(owned) == sorted(
            od.target.address.spec
            for od in self.request_product(
                OwnedDependencies,
                [
                    DependencyOwner(ExportedTarget(self.tgt(exported))),
                    create_options_bootstrapper(),
                ],
            )
        )

    def test_owned_dependencies(self) -> None:
        self.create_file(
            "src/python/foo/bar/baz/BUILD",
            textwrap.dedent(
                """
                python_library(name='baz1', sources=[])
                python_library(name='baz2', sources=[])
                """
            ),
        )
        self.create_file(
            "src/python/foo/bar/BUILD",
            textwrap.dedent(
                """
                python_distribution(
                    name='bar1-dist',
                    dependencies=[':bar1'],
                    provides=setup_py(name='bar1', version='1.1.1'),
                )

                python_library(
                    name='bar1',
                    sources=[],
                    dependencies=['src/python/foo/bar/baz:baz1'],
                )

                python_library(
                    name='bar2',
                    sources=[],
                    dependencies=[':bar-resources', 'src/python/foo/bar/baz:baz2'],
                )
                resources(name='bar-resources', sources=[])
                """
            ),
        )
        self.create_file(
            "src/python/foo/BUILD",
            textwrap.dedent(
                """
                python_distribution(
                    name='foo-dist',
                    dependencies=[':foo'],
                    provides=setup_py(name='foo', version='3.4.5'),
                )

                python_library(
                    sources=[],
                    dependencies=['src/python/foo/bar:bar1', 'src/python/foo/bar:bar2'],
                )
                """
            ),
        )

        self.assert_owned(
            [
                "src/python/foo/bar:bar1",
                "src/python/foo/bar:bar1-dist",
                "src/python/foo/bar/baz:baz1",
            ],
            "src/python/foo/bar:bar1-dist",
        )
        self.assert_owned(
            [
                "src/python/foo",
                "src/python/foo:foo-dist",
                "src/python/foo/bar:bar2",
                "src/python/foo/bar:bar-resources",
                "src/python/foo/bar/baz:baz2",
            ],
            "src/python/foo:foo-dist",
        )


class TestGetExportingOwner(TestSetupPyBase):
    @classmethod
    def rules(cls):
        return (
            *super().rules(),
            get_exporting_owner,
            QueryRule(ExportedTarget, (OwnedDependency, OptionsBootstrapper)),
        )

    def assert_is_owner(self, owner: str, owned: str):
        assert (
            owner
            == self.request_product(
                ExportedTarget,
                [OwnedDependency(self.tgt(owned)), create_options_bootstrapper()],
            ).target.address.spec
        )

    def assert_error(self, owned: str, exc_cls: Type[Exception]):
        with pytest.raises(ExecutionError) as excinfo:
            self.request_product(
                ExportedTarget,
                [OwnedDependency(self.tgt(owned)), create_options_bootstrapper()],
            )
        ex = excinfo.value
        assert len(ex.wrapped_exceptions) == 1
        assert type(ex.wrapped_exceptions[0]) == exc_cls

    def assert_no_owner(self, owned: str):
        self.assert_error(owned, NoOwnerError)

    def assert_ambiguous_owner(self, owned: str):
        self.assert_error(owned, AmbiguousOwnerError)

    def test_get_owner_simple(self) -> None:
        self.create_file(
            "src/python/foo/bar/baz/BUILD",
            textwrap.dedent(
                """
                python_library(name='baz1', sources=[])
                python_library(name='baz2', sources=[])
                """
            ),
        )
        self.create_file(
            "src/python/foo/bar/BUILD",
            textwrap.dedent(
                """
                python_distribution(
                    name='bar1',
                    dependencies=['src/python/foo/bar/baz:baz1'],
                    provides=setup_py(name='bar1', version='1.1.1'),
                )
                python_library(
                    name='bar2',
                    sources=[],
                    dependencies=[':bar-resources', 'src/python/foo/bar/baz:baz2'],
                )
                resources(name='bar-resources', sources=[])
                """
            ),
        )
        self.create_file(
            "src/python/foo/BUILD",
            textwrap.dedent(
                """
                python_distribution(
                    name='foo1',
                    dependencies=['src/python/foo/bar/baz:baz2'],
                    provides=setup_py(name='foo1', version='0.1.2'),
                )
                python_library(name='foo2', sources=[])
                python_distribution(
                    name='foo3',
                    dependencies=['src/python/foo/bar:bar2'],
                    provides=setup_py(name='foo3', version='3.4.5'),
                )
                """
            ),
        )

        self.assert_is_owner("src/python/foo/bar:bar1", "src/python/foo/bar:bar1")
        self.assert_is_owner("src/python/foo/bar:bar1", "src/python/foo/bar/baz:baz1")

        self.assert_is_owner("src/python/foo:foo1", "src/python/foo:foo1")

        self.assert_is_owner("src/python/foo:foo3", "src/python/foo:foo3")
        self.assert_is_owner("src/python/foo:foo3", "src/python/foo/bar:bar2")
        self.assert_is_owner("src/python/foo:foo3", "src/python/foo/bar:bar-resources")

        self.assert_no_owner("src/python/foo:foo2")
        self.assert_ambiguous_owner("src/python/foo/bar/baz:baz2")

    def test_get_owner_siblings(self) -> None:
        self.create_file(
            "src/python/siblings/BUILD",
            textwrap.dedent(
                """
                python_library(name='sibling1', sources=[])
                python_distribution(
                    name='sibling2',
                    dependencies=['src/python/siblings:sibling1'],
                    provides=setup_py(name='siblings', version='2.2.2'),
                )
                """
            ),
        )

        self.assert_is_owner("src/python/siblings:sibling2", "src/python/siblings:sibling1")
        self.assert_is_owner("src/python/siblings:sibling2", "src/python/siblings:sibling2")

    def test_get_owner_not_an_ancestor(self) -> None:
        self.create_file(
            "src/python/notanancestor/aaa/BUILD",
            textwrap.dedent(
                """
                python_library(name='aaa', sources=[])
                """
            ),
        )
        self.create_file(
            "src/python/notanancestor/bbb/BUILD",
            textwrap.dedent(
                """
                python_distribution(
                    name='bbb',
                    dependencies=['src/python/notanancestor/aaa'],
                    provides=setup_py(name='bbb', version='11.22.33'),
                )
                """
            ),
        )

        self.assert_no_owner("src/python/notanancestor/aaa")
        self.assert_is_owner("src/python/notanancestor/bbb", "src/python/notanancestor/bbb")

    def test_get_owner_multiple_ancestor_generations(self) -> None:
        self.create_file(
            "src/python/aaa/bbb/ccc/BUILD",
            textwrap.dedent(
                """
                python_library(name='ccc', sources=[])
                """
            ),
        )
        self.create_file(
            "src/python/aaa/bbb/BUILD",
            textwrap.dedent(
                """
                python_distribution(
                    name='bbb',
                    dependencies=['src/python/aaa/bbb/ccc'],
                    provides=setup_py(name='bbb', version='1.1.1'),
                )
                """
            ),
        )
        self.create_file(
            "src/python/aaa/BUILD",
            textwrap.dedent(
                """
                python_distribution(
                    name='aaa',
                    dependencies=['src/python/aaa/bbb/ccc'],
                    provides=setup_py(name='aaa', version='2.2.2'),
                )
                """
            ),
        )

        self.assert_is_owner("src/python/aaa/bbb", "src/python/aaa/bbb/ccc")
        self.assert_is_owner("src/python/aaa/bbb", "src/python/aaa/bbb")
        self.assert_is_owner("src/python/aaa", "src/python/aaa")


def test_validate_args() -> None:
    with pytest.raises(InvalidSetupPyArgs):
        validate_args(("bdist_wheel", "upload"))
    with pytest.raises(InvalidSetupPyArgs):
        validate_args(("sdist", "-d", "new_distdir/"))
    with pytest.raises(InvalidSetupPyArgs):
        validate_args(("--dist-dir", "new_distdir/", "sdist"))

    validate_args(("sdist",))
    validate_args(("bdist_wheel", "--foo"))
