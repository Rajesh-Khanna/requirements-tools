# pylint:disable=redefined-outer-name
from __future__ import absolute_import
from __future__ import unicode_literals

import contextlib
import json
import re
import subprocess

import mock
import pkg_resources
import pytest

import check_requirements as main


@pytest.mark.parametrize(
    ('reqin', 'reqout'),
    (
        ('a', 'a'),
        ('a==1', 'a==1'),
        ('a==1,<3', 'a==1,<3'),
    ),
)
def test_parse_requirement(reqin, reqout):
    assert (
        main.parse_requirement(reqin) ==
        pkg_resources.Requirement.parse(reqout)
    )


def test_get_lines_from_file_trivial(tmpdir):
    tmpfile = tmpdir.join('foo').ensure()
    assert main.get_lines_from_file(tmpfile.strpath) == []


def test_get_lines_from_file_ignores_comments(tmpdir):
    tmpfile = tmpdir.join('foo')
    tmpfile.write('foo\n#bar\nbaz')
    assert main.get_lines_from_file(tmpfile.strpath) == ['foo', 'baz']


def test_get_lines_from_file_strips_ws(tmpdir):
    tmpfile = tmpdir.join('foo')
    tmpfile.write(' foo \n    \n \tbaz')
    assert main.get_lines_from_file(tmpfile.strpath) == ['foo', 'baz']


def test_get_raw_requirements_trivial(tmpdir):
    reqs_filename = tmpdir.join('requirements.txt').ensure()
    assert main.get_raw_requirements(reqs_filename.strpath) == []


def test_get_raw_requirements_some_things(tmpdir):
    reqs_file = tmpdir.join('requirements.txt')
    reqs_file.write('-e .\nfoo==1\nbar==2')
    requirements = main.get_raw_requirements(reqs_file.strpath)
    assert requirements == [
        (pkg_resources.Requirement.parse('foo==1'), reqs_file.strpath),
        (pkg_resources.Requirement.parse('bar==2'), reqs_file.strpath),
    ]


def test_to_version():
    assert main.to_version(pkg_resources.Requirement.parse('foo==2')) == '2'
    assert main.to_version(pkg_resources.Requirement.parse('foo')) is None
    assert main.to_version(pkg_resources.Requirement.parse('foo>3')) is None
    assert main.to_version(pkg_resources.Requirement.parse('foo>3,<7')) is None


def test_to_equality_str():
    req = pkg_resources.Requirement.parse('foo==2.2')
    assert main.to_equality_str(req) == 'foo==2.2'


def test_to_pinned_versions_trivial():
    assert main.to_pinned_versions(()) == {}


def test_to_pinned_versions():
    pinned_versions = main.to_pinned_versions((
        (pkg_resources.Requirement.parse('foo==2'), 'reqs.txt'),
        (pkg_resources.Requirement.parse('bar==3'), 'reqs.txt'),
    ))
    assert pinned_versions == {'foo': '2', 'bar': '3'}


def test_to_pinned_versions_uses_key():
    pinned_versions = main.to_pinned_versions((
        (pkg_resources.Requirement.parse('Foo==2'), 'reqs.txt'),
    ))
    assert pinned_versions == {'foo': '2'}


def test_unpinned_things():
    pkgreq = pkg_resources.Requirement.parse('pkg-with-deps==0.1.0')
    ret = main.find_unpinned_requirements(((pkgreq, 'reqs.txt'),))
    assert ret == {
        ('pkg-dep-1', pkgreq, 'reqs.txt'),
        ('pkg-dep-2', pkgreq, 'reqs.txt'),
    }


def test_format_unpinned_requirements():
    unpinned = main.find_unpinned_requirements((
        (pkg_resources.Requirement.parse('pkg-with-deps==0.1.0'), 'reqs.txt'),
    ))
    ret = main.format_unpinned_requirements(unpinned)
    assert ret == (
        "\tpkg-dep-1 (required by pkg-with-deps==0.1.0 in reqs.txt)\n"
        '\t\tmaybe you want "pkg-dep-1==1.0.0"?\n'
        "\tpkg-dep-2 (required by pkg-with-deps==0.1.0 in reqs.txt)\n"
        '\t\tmaybe you want "pkg-dep-2==2.0.0"?'
    )


def test_test_top_level_dependencies(in_tmpdir):
    # So we don't skip
    in_tmpdir.join('requirements-minimal.txt').write('pkg-with-deps')
    in_tmpdir.join('requirements.txt').write(
        'pkg-dep-1==1.0.0\n'
        'pkg-dep-2==2.0.0\n'
        'pkg-with-deps==0.1.0\n'
    )
    # Should pass since all are satisfied
    main.test_top_level_dependencies()


def test_test_top_level_dependencies_with_extras(in_tmpdir):
    in_tmpdir.join('requirements-minimal.txt').write('pkg-with-extras[extra1]')
    in_tmpdir.join('requirements.txt').write(
        'pkg-with-extras==0.1.0\n'
        'pkg-dep-1==1.0.0\n'
    )
    # Should pass
    main.test_top_level_dependencies()


def test_test_top_level_dependencies_with_depends_on_extras(in_tmpdir):
    in_tmpdir.join('requirements-minimal.txt').write(
        'depends-on-pkg-with-extras'
    )
    in_tmpdir.join('requirements.txt').write(
        'depends-on-pkg-with-extras==3.0.0\n'
        'pkg-with-extras==0.1.0\n'
        'pkg-dep-1==1.0.0\n'
        'pkg-dep-2==2.0.0\n'
        'prerelease-pkg==1.2.3-rc1\n'
    )
    # Should pass
    main.test_top_level_dependencies()


def test_test_top_level_dependencies_not_enough_pinned(in_tmpdir):
    # So we don't skip
    in_tmpdir.join('requirements-minimal.txt').write('pkg-with-deps')
    in_tmpdir.join('requirements.txt').write(
        'pkg-dep-1==1.0.0\n'
        'pkg-with-deps==0.1.0\n'
    )
    with pytest.raises(AssertionError) as excinfo:
        main.test_top_level_dependencies()
    assert excinfo.value.args == (
        'Dependencies derived from requirements-minimal.txt are not pinned in '
        'requirements.txt\n'
        '(Probably need to add something to requirements.txt):\n'
        '\t- pkg-dep-2==2.0.0',
    )


def test_test_top_level_dependencies_unmet_dependency(in_tmpdir):
    in_tmpdir.join('requirements-minimal.txt').write('pkg-unmet-deps')
    in_tmpdir.join('requirements.txt').write('pkg-unmet-deps==1.0')
    with pytest.raises(AssertionError) as excinfo:
        main.test_top_level_dependencies()
    assert excinfo.value.args == (
        'Unmet dependency detected!\n'
        'Somehow `missing-dependency` is not installed!\n'
        '  (from pkg-unmet-deps)\n'
        'Are you suffering from https://github.com/pypa/pip/issues/3903?',
    )


@pytest.mark.parametrize('version', ('1.2.3-rc1', '1.2.3rc1'))
def test_prerelease_name_normalization(in_tmpdir, version):
    in_tmpdir.join('requirements-minimal.txt').write('prerelease-pkg')
    in_tmpdir.join('requirements.txt').write(
        'prerelease-pkg=={}'.format(version),
    )
    main.test_top_level_dependencies()


def test_test_top_level_dependencies_no_requirements_dev_minimal(
        in_tmpdir, capsys,
):
    """If there's no requirements-dev-minimal.txt, we should suggest you create
    a requirements-dev-minimal.txt but not fail.
    """
    in_tmpdir.join('requirements-minimal.txt').ensure()
    in_tmpdir.join('requirements.txt').ensure()
    in_tmpdir.join('requirements-dev.txt').write(
        'pkg-dep-1\n'
        'pkg-dep-2==2.0.0\n'
    )
    main.test_top_level_dependencies()  # should not raise
    assert (
        'Warning: check-requirements is *not* checking your dev dependencies.'
        in capsys.readouterr()[0]
    )


def test_test_top_level_dependencies_no_dev_deps_pinned(in_tmpdir):
    """If there's a requirements-dev-minimal.txt but no requirements-dev.txt,
    we should tell you to pin everything there.
    """
    in_tmpdir.join('requirements-minimal.txt').ensure()
    in_tmpdir.join('requirements.txt').ensure()
    in_tmpdir.join('requirements-dev-minimal.txt').write(
        'pkg-dep-1\n'
        'pkg-dep-2\n'
    )
    with pytest.raises(AssertionError) as excinfo:
        main.test_top_level_dependencies()
    assert excinfo.value.args == (
        'Dependencies derived from requirements-dev-minimal.txt are '
        'not pinned in requirements-dev.txt\n'
        '(Probably need to add something to requirements-dev.txt):\n'
        '\t- pkg-dep-1==1.0.0\n'
        '\t- pkg-dep-2==2.0.0',
    )

    # and when you do pin it, now the tests pass! :D
    in_tmpdir.join('requirements-dev.txt').write(
        'pkg-dep-1==1.0.0\npkg-dep-2==2.0.0\n',
    )
    main.test_top_level_dependencies()


def test_test_top_level_dependencies_some_dev_deps_not_pinned(in_tmpdir):
    """If there's a requirements-dev-minimal.txt but we're missing stuff in
    requirements-dev.txt, we should tell you to pin more stuff there.
    """
    in_tmpdir.join('requirements-minimal.txt').ensure()
    in_tmpdir.join('requirements.txt').ensure()
    in_tmpdir.join('requirements-dev-minimal.txt').write('pkg-with-deps')
    in_tmpdir.join('requirements-dev.txt').write(
        'pkg-with-deps==0.1.0\n'
        'pkg-dep-1==1.0.0\n'
    )
    with pytest.raises(AssertionError) as excinfo:
        main.test_top_level_dependencies()
    assert excinfo.value.args == (
        'Dependencies derived from requirements-dev-minimal.txt are '
        'not pinned in requirements-dev.txt\n'
        '(Probably need to add something to requirements-dev.txt):\n'
        '\t- pkg-dep-2==2.0.0',
    )

    # and when you do pin it, now the tests pass! :D
    in_tmpdir.join('requirements-dev.txt').write(
        'pkg-with-deps==0.1.0\n'
        'pkg-dep-1==1.0.0\n'
        'pkg-dep-2==2.0.0\n'
    )
    main.test_top_level_dependencies()


def test_test_top_level_dependencies_overlapping_prod_dev_deps(in_tmpdir):
    """If we have a dep which is both a prod and dev dep, we should complain if
    it appears in requirements-dev.txt.
    """
    in_tmpdir.join('requirements-minimal.txt').write('pkg-dep-1')
    in_tmpdir.join('requirements.txt').write('pkg-dep-1==1.0.0')
    in_tmpdir.join('requirements-dev-minimal.txt').write('pkg-dep-1')
    in_tmpdir.join('requirements-dev.txt').write('pkg-dep-1==1.0.0')
    with pytest.raises(AssertionError) as excinfo:
        main.test_top_level_dependencies()
    # TODO: this exception is misleading, ideally it should tell you that
    # you don't need to pin it in reqs-dev.txt if it's also a prod dep
    assert excinfo.value.args == (
        'Requirements are pinned in requirements-dev.txt but are not depended on in requirements-dev-minimal.txt!\n'  # noqa
        '\n'
        'Usually this happens because you upgraded some other dependency, and now no longer require these.\n'  # noqa
        "If that's the case, you should remove these from requirements-dev.txt.\n"  # noqa
        'Otherwise, if you *do* need these packages, then add them to requirements-dev-minimal.txt.\n'  # noqa
        '\t- pkg-dep-1==1.0.0',
    )


def test_test_top_level_dependencies_prod_dep_is_only_in_dev_deps(in_tmpdir):
    """If we've defined a prod dependency only in requirements-dev.txt, we
    should tell the user to put it in requirements.txt instead.
    """
    in_tmpdir.join('requirements-minimal.txt').write('pkg-with-deps')
    in_tmpdir.join('requirements.txt').write(
        'pkg-with-deps==0.1.0\n'
        'pkg-dep-1==1.0.0\n'
    )
    in_tmpdir.join('requirements-dev-minimal.txt').write('pkg-dep-2')
    in_tmpdir.join('requirements-dev.txt').write('pkg-dep-2==2.0.0')
    with pytest.raises(AssertionError) as excinfo:
        main.test_top_level_dependencies()
    assert excinfo.value.args == (
        'Dependencies derived from requirements-minimal.txt are not '
        'pinned in requirements.txt\n'
        '(Probably need to add something to requirements.txt):\n'
        '\t- pkg-dep-2==2.0.0',
    )


def test_test_top_level_dependencies_too_muchh_pinned(in_tmpdir):
    # So we don't skip
    in_tmpdir.join('requirements-minimal.txt').write('pkg-dep-1')
    in_tmpdir.join('requirements.txt').write(
        'pkg-dep-1==1.0.0\n'
        'other-dep-1==1.0.0\n'
    )
    with pytest.raises(AssertionError) as excinfo:
        main.test_top_level_dependencies()
    assert excinfo.value.args[0] == (
        'Requirements are pinned in requirements.txt but are not depended on in requirements-minimal.txt!\n'  # noqa
        '\n'
        'Usually this happens because you upgraded some other dependency, and now no longer require these.\n'  # noqa
        "If that's the case, you should remove these from requirements.txt.\n"  # noqa
        'Otherwise, if you *do* need these packages, then add them to requirements-minimal.txt.\n'  # noqa
        '\t- other-dep-1==1.0.0'
    )


def test_test_requirements_pinned_trivial(in_tmpdir):
    in_tmpdir.join('requirements.txt').ensure()
    # Should not raise
    main.test_requirements_pinned()


def test_test_requirements_pinned_trivial_with_dev_too(in_tmpdir):
    in_tmpdir.join('requirements.txt').ensure()
    in_tmpdir.join('requirements-dev.txt').ensure()
    # Should not raise
    main.test_requirements_pinned()


def test_test_requirements_pinned_all_pinned(in_tmpdir):
    in_tmpdir.join('requirements.txt').write(
        'pkg-with-deps==0.1.0\n'
        'pkg-dep-1==1.0.0\n'
        'pkg-dep-2==1.0.0\n'
    )
    # Should also not raise (all satisfied)
    main.test_requirements_pinned()


def test_test_requirements_pinned_all_pinned_dev_only(in_tmpdir):
    in_tmpdir.join('requirements-dev-minimal.txt').write('pkg-with-deps')
    in_tmpdir.join('requirements-dev.txt').write(
        'pkg-with-deps==0.1.0\n'
        'pkg-dep-1==1.0.0\n'
        'pkg-dep-2==1.0.0\n'
    )
    # Should also not raise (all satisfied)
    main.test_requirements_pinned()


def test_test_requirements_pinned_missing_some(in_tmpdir):
    in_tmpdir.join('requirements.txt').write('pkg-with-deps==0.1.0')
    in_tmpdir.join('requirements-dev.txt').write('other-pkg-with-deps==0.2.0')
    with pytest.raises(AssertionError) as excinfo:
        main.test_requirements_pinned()
    assert excinfo.value.args == (
        'Unpinned requirements detected!\n\n'
        '\tpkg-dep-1 (required by pkg-with-deps==0.1.0 in requirements.txt)\n'
        '\t\tmaybe you want "pkg-dep-1==1.0.0"?\n'
        '\tpkg-dep-2 (required by pkg-with-deps==0.1.0 in requirements.txt)\n'
        '\t\tmaybe you want "pkg-dep-2==2.0.0"?',
    )


def test_test_requirements_pinned_missing_some_with_dev_reqs(in_tmpdir):
    in_tmpdir.join('requirements.txt').write('pkg-with-deps==0.1.0')
    in_tmpdir.join('requirements-dev.txt').write('other-pkg-with-deps==0.2.0')
    in_tmpdir.join('requirements-dev-minimal.txt').write(
        'other-pkg-with-deps',
    )
    with pytest.raises(AssertionError) as excinfo:
        main.test_requirements_pinned()
    assert excinfo.value.args == (
        'Unpinned requirements detected!\n\n'
        '\tother-dep-1 (required by other-pkg-with-deps==0.2.0 in requirements-dev.txt)\n'  # noqa
        '\t\tmaybe you want "other-dep-1==1.0.0"?\n'
        '\tpkg-dep-1 (required by pkg-with-deps==0.1.0 in requirements.txt)\n'
        '\t\tmaybe you want "pkg-dep-1==1.0.0"?\n'
        '\tpkg-dep-2 (required by pkg-with-deps==0.1.0 in requirements.txt)\n'
        '\t\tmaybe you want "pkg-dep-2==2.0.0"?',
    )


@pytest.yield_fixture
def in_tmpdir(tmpdir):
    with tmpdir.as_cwd():
        yield tmpdir


@pytest.mark.parametrize(
    ('requirement', 'expected_pkgs'),
    (
        ('pkg-with-deps', ['pkg-dep-1', 'pkg-dep-2']),
        (
            'depends-on-pkg-with-extras',
            ['pkg-dep-1', 'pkg-dep-2', 'pkg-with-extras', 'prerelease-pkg'],
        ),
    ),
)
def test_get_pinned_versions_from_requirement(requirement, expected_pkgs):
    result = main.get_pinned_versions_from_requirement(
        main.parse_requirement(requirement),
    )
    # These are to make this not flaky in future when things change
    assert isinstance(result, set)
    result = sorted(result)
    split = [req.split('==') for req in result]
    packages = [package for package, _ in split]
    assert packages == expected_pkgs


def test_get_pinned_versions_from_requirement_circular():
    # Used to hang forever
    assert main.get_pinned_versions_from_requirement(
        main.parse_requirement('sphinx'),
    )


def test_format_versions_on_lines_with_dashes_trivial():
    assert main.format_versions_on_lines_with_dashes(()) == ''


def test_format_versions_on_lines_with_dashes_something():
    versions = [
        pkg_resources.Requirement.parse('a==4.5.6'),
        pkg_resources.Requirement.parse('b==1.2.3'),
        pkg_resources.Requirement.parse('c==7'),
    ]
    ret = main.format_versions_on_lines_with_dashes(versions)
    assert ret == (
        '\t- a==4.5.6\n'
        '\t- b==1.2.3\n'
        '\t- c==7'
    )


def test_test_no_underscores_passes_reqs_dev_doesnt_exist(in_tmpdir):
    """If requirements.txt exists (but not -dev.txt) we shouldn't raise."""
    in_tmpdir.join('requirements.txt').write('foo==1')
    # Should not raise
    main.test_no_underscores_all_dashes()


def test_test_no_underscores_all_dashes_ok(in_tmpdir):
    tmpfile = in_tmpdir.join('tmp')
    tmpfile.write('foo==1')
    # Should not raise
    main.test_no_underscores_all_dashes(requirements_files=(tmpfile.strpath,))


def test_test_no_underscores_all_dashes_error(in_tmpdir):
    tmpfile = in_tmpdir.join('tmp')
    tmpfile.write('foo_bar==1')
    with pytest.raises(AssertionError) as excinfo:
        main.test_no_underscores_all_dashes(
            requirements_files=(tmpfile.strpath,),
        )
    assert excinfo.value.args == (
        'Use dashes for package names {}: foo_bar==1'.format(tmpfile.strpath),
    )


def test_test_javascript_package_versions_no_npm_versions(in_tmpdir):
    in_tmpdir.join('package.json').write('{"dependencies": {}}')
    in_tmpdir.join('node_modules').ensure_dir()
    # Should not raise
    main.test_javascript_package_versions()


def test_test_javascript_package_versions_matching(in_tmpdir):
    # Contrived, but let's assume pkg-with-deps is an npm package
    in_tmpdir.join('package.json').write(
        '{"dependencies": {"pkg-with-deps": "0.1.0"}}',
    )
    in_tmpdir.join('node_modules').ensure_dir()
    # Should not raise
    main.test_javascript_package_versions()


def test_test_npm_package_irrelevant_version(in_tmpdir):
    # I hope we don't install a python package named herp any time soon :)
    in_tmpdir.join('package.json').write('{"dependencies": {"herp": "1.0"}}')
    in_tmpdir.join('node_modules').ensure_dir()
    # Should not raise
    main.test_javascript_package_versions()


def test_test_javascript_package_versions_not_matching_python(in_tmpdir):
    # Again, contrived, but let's assume pkg-with-deps is an npm
    in_tmpdir.join('package.json').write(
        '{"dependencies": {"pkg-with-deps": "0.0.0"}}',
    )
    in_tmpdir.join('node_modules').ensure_dir()
    with pytest.raises(AssertionError) as excinfo:
        main.test_javascript_package_versions()
    assert excinfo.value.args == (
        'The package "pkg-with-deps" is both a JavaScript and Python package.\n'  # noqa
        "The version installed by Python must match the JavaScript version, but it currently doesn't!\n"  # noqa
        '  JavaScript version: 0.0.0\n'
        '  Python version: 0.1.0\n'
        'Check requirements.txt and package.json!',
    )


def test_check_requirements_is_only_for_applications(in_tmpdir):
    in_tmpdir.join('requirements.txt').ensure()
    main.check_requirements_is_only_for_applications()


def test_check_requirements_is_only_for_applications_failing():
    with pytest.raises(AssertionError) as excinfo:
        main.check_requirements_is_only_for_applications()
    assert excinfo.value.args == (
        'check-requirements is designed specifically with applications in '
        'mind (and does not properly work for libraries).\n'
        "Either remove check-requirements (if you're a library) or "
        '`touch requirements.txt`.',
    )


def test_check_requirements_integrity_passing(in_tmpdir):
    in_tmpdir.join('requirements.txt').write('pkg-with-deps==0.1.0')
    main.check_requirements_integrity()


def test_check_requirements_integrity_doesnt_care_about_unpinned(in_tmpdir):
    in_tmpdir.join('requirements.txt').write('pkg-with-deps')
    main.check_requirements_integrity()


def test_check_requirements_integrity_failing(in_tmpdir):
    in_tmpdir.join('requirements.txt').write('pkg-with-deps==1.0.0')
    with pytest.raises(AssertionError) as excinfo:
        main.check_requirements_integrity()
    assert excinfo.value.args == (
        'Installed requirements do not match requirement files!\n'
        'Rebuild your virtualenv:\n'
        ' - (requirements.txt) pkg-with-deps==1.0.0 '
        '(installed) pkg-with-deps==0.1.0\n',
    )


@pytest.mark.parametrize('version', ('2.13-1', '2.13.post1'))
def test_check_requirements_integrity_post_version(in_tmpdir, version):
    in_tmpdir.join('requirements.txt').write('chameleon=={}'.format(version))
    main.check_requirements_integrity()


@contextlib.contextmanager
def subprocess_returns(this):
    with mock.patch.object(
        subprocess, 'check_output',
        return_value=json.dumps(this).encode('UTF-8'),
    ):
        yield


def uncolor(text):
    text = re.sub('\033\\[[^A-z]*[A-z]', '', text)
    return re.sub('[^\n\r]*\r', '', text)


@pytest.mark.parametrize(('tree', 'expected'), (
    (
        {'name': 'www_pages', 'dependencies': {}},
        {},
    ),
    (
        {
            'name': 'www_pages',
            'dependencies': {'closure_compiler': {'version': '1.0'}},
        },
        {'closure_compiler': {'1.0': {'(your app)@*'}}},
    ),
    (
        {'dependencies': {'closure_compiler': {'version': '1.0'}}},
        {'closure_compiler': {'1.0': {'(your app)@*'}}},
    ),
    (
        {
            'name': 'www_pages',
            'dependencies': {
                'closure_compiler': {
                    'version': '1.0',
                    'dependencies': {'closure_externs': {'version': '2.0'}}
                },
            },
        },
        {
            'closure_compiler': {'1.0': {'(your app)@*'}},
            'closure_externs': {'2.0': {'closure_compiler@1.0'}},
        },
    ),
    (
        {
            'name': 'www_pages',
            'dependencies': {
                'closure_compiler': {
                    'version': '1.0',
                    'dependencies': {'closure_externs': {'version': '2.0'}}
                },
                'closure_externs': {'version': '2.0'},
            },
        },
        {
            'closure_compiler': {'1.0': {'(your app)@*'}},
            'closure_externs': {
                '2.0': {'closure_compiler@1.0', '(your app)@*'},
            },
        },
    ),
    (
        {
            'name': 'www_pages',
            'dependencies': {
                'closure_compiler': {
                    'version': '1.0',
                    'dependencies': {'closure_externs': {'version': '2.0'}}
                },
                'closure_externs': {'version': '3.0'},
            },
        },
        {
            'closure_compiler': {'1.0': {'(your app)@*'}},
            'closure_externs': {
                '2.0': {'closure_compiler@1.0'},
                '3.0': {'(your app)@*'},
            },
        },
    ),
    # jquery tree should be ignored entirely
    (
        {
            'name': 'www_pages',
            'dependencies': {
                'jquery': {
                    'version': '1.0',
                    'dependencies': {'closure_externs': {'version': '2.0'}}
                },
            },
        },
        {},
    ),
))
def test_parse_npm_dependency_tree(tree, expected):
    assert main.parse_npm_dependency_tree(tree) == expected


@pytest.mark.parametrize('tree,package_json', (
    (
        {'name': 'www_pages', 'dependencies': {}},
        {'dependencies': {}},
    ),
    (
        {
            'name': 'www_pages',
            'dependencies': {
                'closure_compiler': {
                    'version': '1.0',
                    'dependencies': {'closure_externs': {'version': '2.0'}},
                },
            },
        },
        {
            'dependencies': {
                'closure_compiler': '1.0',
                'closure_externs': '2.0',
            }
        },
    ),
    (
        {
            'name': 'www_pages',
            'dependencies': {
                'closure_compiler': {
                    'version': '1.0',
                    'dependencies': {'closure_externs': {'version': '2.0'}},
                },
                'closure_externs': {'version': '2.0'},
            },
        },
        {
            'dependencies': {
                'closure_compiler': '1.0',
                'closure_externs': '2.0',
            }
        },
    ),
))
def test_test_all_npm_packages_pinned_success(tree, package_json, in_tmpdir):
    with subprocess_returns(tree):
        in_tmpdir.join('package.json').write(json.dumps(package_json))
        in_tmpdir.join('node_modules').ensure_dir()
        main.test_all_npm_packages_pinned()


@pytest.mark.parametrize('tree,package_json,error', (
    (
        {
            'name': 'www_pages',
            'dependencies': {'closure_compiler': {'version': '1.0'}},
        },
        {'dependencies': {}},
        (
            'Unpinned requirements detected!\n'
            '    closure_compiler@1.0 <-(your app)@*'
        ),
    ),
    (
        {
            'name': 'www_pages',
            'dependencies': {
                'closure_compiler': {
                    'version': '1.0',
                    'dependencies': {
                        'closure_externs': {
                            'version': '2.0',
                            'dependencies': {
                                'left-pad': {'version': '3.0'},
                            }
                        }
                    }
                },
            },
        },
        {'dependencies': {'closure_externs': '2.0'}},
        (
            'Unpinned requirements detected!\n'
            '    closure_compiler@1.0 <-(your app)@*\n'
            '    left-pad@3.0 <-closure_externs@2.0<-closure_compiler@1.0<-(your app)@*'  # noqa
        ),
    ),
))
def test_test_all_npm_packages_pinned_failure(
        tree,
        package_json,
        error,
        in_tmpdir,
):
    with subprocess_returns(tree):
        in_tmpdir.join('package.json').write(json.dumps(package_json))
        in_tmpdir.join('node_modules').ensure_dir()
        with pytest.raises(AssertionError) as excinfo:
            main.test_all_npm_packages_pinned()
    assert uncolor(excinfo.value.args[0]) == error


@pytest.mark.parametrize('tree,package_json', (
    (
        {'name': 'www_pages', 'dependencies': {}},
        {'dependencies': {}},
    ),
    (
        {
            'name': 'www_pages',
            'dependencies': {
                'closure_compiler': {
                    'version': '1.0',
                    'dependencies': {'closure_externs': {'version': '2.0'}},
                },
                'closure_externs': {'version': '2.0'},
            },
        },
        {
            'dependencies': {}
        },
    ),
))
def test_test_no_conflicting_npm_package_versions_success(
        tree,
        package_json,
        in_tmpdir,
):
    with subprocess_returns(tree):
        in_tmpdir.join('package.json').write(json.dumps(package_json))
        in_tmpdir.join('node_modules').ensure_dir()
        main.test_no_conflicting_npm_package_versions()


@pytest.mark.parametrize('tree,package_json,error', (
    (
        {
            'name': 'www_pages',
            'dependencies': {
                'closure_compiler': {
                    'version': '4.0',
                    'dependencies': {'closure_externs': {'version': '1.0'}},
                },
                'closure_externs': {
                    'version': '2.0',
                    'dependencies': {
                        'closure_compiler': {'version': '9.999'},
                    },
                }
            },
        },
        {'dependencies': {}},
        (
            'Conflicting NPM package requirements detected!\n'
            '  closure_compiler needs multiple versions:\n'
            '    closure_compiler@4.0 <-(your app)@*\n'
            '    closure_compiler@9.999 <-closure_externs@2.0<-(your app)@*\n'
            '  closure_externs needs multiple versions:\n'
            '    closure_externs@1.0 <-closure_compiler@4.0<-(your app)@*\n'
            '    closure_externs@2.0 <-(your app)@*'
        ),
    ),
))
def test_test_no_conflicting_npm_package_versions_failure(
        tree,
        package_json,
        error,
        in_tmpdir,
):
    with subprocess_returns(tree):
        in_tmpdir.join('package.json').write(json.dumps(package_json))
        in_tmpdir.join('node_modules').ensure_dir()
        with pytest.raises(AssertionError) as excinfo:
            main.test_no_conflicting_npm_package_versions()
    assert uncolor(excinfo.value.args[0]) == error


def test_test_javascript_tests_pass_with_no_dependencies_key(in_tmpdir):
    in_tmpdir.join('package.json').write('{"private": true}')
    in_tmpdir.join('node_modules').ensure_dir()

    # Should not raise
    main.test_javascript_package_versions()
    main.test_no_conflicting_npm_package_versions()
    main.test_all_npm_packages_pinned()


@pytest.mark.parametrize(
    'testfunc',
    (
        main.test_all_npm_packages_pinned,
        main.test_no_conflicting_npm_package_versions,
    ),
)
def test_missing_node_modules_raises(in_tmpdir, testfunc):
    in_tmpdir.join('package.json').write('{"private": true}')
    with pytest.raises(AssertionError) as excinfo:
        testfunc()
    assert excinfo.value.args == (
        'node_modules not found.  Are you missing a make target?',
    )
