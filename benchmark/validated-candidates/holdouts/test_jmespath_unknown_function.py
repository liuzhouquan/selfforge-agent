import jmespath
import pytest
from jmespath import Options
from jmespath.exceptions import JMESPathError, UnknownFunctionError


def test_unknown_function_fuzzy_suggestions():
    # starts_wit -> starts_with
    with pytest.raises(UnknownFunctionError) as exc_info:
        jmespath.search('starts_wit(@, `a`)', 'abc')
    err = exc_info.value
    assert hasattr(err, "function_name")
    assert err.function_name == "starts_wit"
    assert hasattr(err, "similar")
    assert err.similar == "starts_with"
    assert "Unknown function: starts_wit(), did you mean: starts_with()?" in str(err)

    # to_numbe -> to_number
    with pytest.raises(UnknownFunctionError) as exc_info:
        jmespath.search('to_numbe(@)', '123')
    err = exc_info.value
    assert err.function_name == "to_numbe"
    assert err.similar == "to_number"
    assert "did you mean: to_number()?" in str(err)

    # to_strin -> to_string
    with pytest.raises(UnknownFunctionError) as exc_info:
        jmespath.search('to_strin(@)', 123)
    err = exc_info.value
    assert err.function_name == "to_strin"
    assert err.similar == "to_string"
    assert "did you mean: to_string()?" in str(err)

    # lengt -> length
    with pytest.raises(UnknownFunctionError) as exc_info:
        jmespath.search('lengt(@)', 'abc')
    err = exc_info.value
    assert err.function_name == "lengt"
    assert err.similar == "length"
    assert "did you mean: length()?" in str(err)


def test_unknown_function_without_close_matches():
    with pytest.raises(UnknownFunctionError) as exc_info:
        jmespath.search('completely_unknown_xyz(@)', 'abc')
    err = exc_info.value
    assert hasattr(err, "function_name")
    assert err.function_name == "completely_unknown_xyz"
    assert getattr(err, "similar", None) is None
    assert str(err) == "Unknown function: completely_unknown_xyz()"
    assert "did you mean" not in str(err)


def test_options_control_suggestions():
    # When suggest_functions is False, no suggestion should be given even for close matches
    opts_no_suggest = Options(suggest_functions=False)
    with pytest.raises(UnknownFunctionError) as exc_info:
        jmespath.search('starts_wit(@, `a`)', 'abc', options=opts_no_suggest)
    err = exc_info.value
    assert err.function_name == "starts_wit"
    assert getattr(err, "similar", None) is None
    assert "did you mean" not in str(err)
    assert str(err) == "Unknown function: starts_wit()"

    # Default options should enable suggestions
    opts_default = Options()
    assert getattr(opts_default, "suggest_functions", True) is True
    with pytest.raises(UnknownFunctionError) as exc_info:
        jmespath.search('starts_wit(@, `a`)', 'abc', options=opts_default)
    err = exc_info.value
    assert err.similar == "starts_with"
    assert "did you mean: starts_with()?" in str(err)


def test_error_type_hierarchy():
    with pytest.raises(JMESPathError):
        jmespath.search('unknown_fn(@)', 'abc')
    with pytest.raises(ValueError):
        jmespath.search('unknown_fn(@)', 'abc')


def test_custom_function_body_typeerror_is_not_retried():
    from jmespath import functions

    class Custom(functions.Functions):
        calls = 0

        @functions.signature({'types': ['string']})
        def _func_explode(self, value):
            self.calls += 1
            raise TypeError('custom body failed')

    custom = Custom()
    with pytest.raises(TypeError, match='custom body failed'):
        jmespath.search('explode(@)', 'text', options=Options(custom_functions=custom))
    assert custom.calls == 1


def test_legacy_custom_dispatch_without_suggest_keyword():
    class Legacy:
        def call_function(self, name, args):
            return (name, args)

    assert jmespath.search('legacy(@)', 'value', options=Options(custom_functions=Legacy())) == ('legacy', ['value'])
