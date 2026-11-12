import pytest

from selfforge_agent.runtime.environment import _convert_poetry_spec, check_dependency_freshness


@pytest.mark.parametrize('declaration', ['[project\ndependencies=["missing"]', '[project]\ndependencies=["not a valid req???"]', '[project]\ndependencies=["demo @ https://example.invalid/pkg.whl"]'])
def test_invalid_declarations_fail_closed(tmp_path, declaration):
    (tmp_path / 'pyproject.toml').write_text(declaration)
    (tmp_path / 'requirements.lock').write_text('demo==1 --hash=sha256:'+'a'*64+'\n')
    with pytest.raises(ValueError):
        check_dependency_freshness(tmp_path)

def test_optional_dependencies_are_checked(tmp_path):
    (tmp_path / 'pyproject.toml').write_text('[project]\ndependencies=["demo"]\n[project.optional-dependencies]\ntest=["missing>=2"]\n')
    (tmp_path / 'requirements.lock').write_text('demo==1 --hash=sha256:'+'a'*64+'\n')
    report = check_dependency_freshness(tmp_path)
    assert report['fresh'] is False
    assert report['missing_packages'][0]['name'] == 'missing'

@pytest.mark.parametrize(('spec','expected'), [('~1','>=1, <2.0.0'),('^0','>=0, <1.0.0'),('^0.0','>=0.0, <0.1.0')])
def test_short_poetry_constraints(spec, expected):
    assert _convert_poetry_spec(spec) == expected
