# JMESPath unknown function suggestions and options qualification

This project-authored candidate task qualifies a multi-file cross-module contract extension for `jmespath` at pinned revision `2812594e69d43098ef60f81f4efc404c071b0418` (MIT license).

The repository runs in the default offline sandbox `selfforge-agent-sandbox:latest` (`sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20`).

## Contract and controls

| Case | Contract and classification | Pinned source evidence / independent controls |
| --- | --- | --- |
| `jmespath_unknown_function_suggestions` | Multi-file feature extension: unknown function diagnostics with 'did you mean' suggestions and Options control | `jmespath` revision `2812594e69d43098ef60f81f4efc404c071b0418` (MIT License). Modifies three source files: `jmespath/exceptions.py` (adds `function_name` and `similar` to `UnknownFunctionError`), `jmespath/functions.py` (`call_function` matches closest function with `difflib.get_close_matches`), and `jmespath/visitor.py` (`Options` adds `suggest_functions=True`, forwarded by `TreeInterpreter`). Test suggestions on close typos, no-suggestion on distant names, and `Options(suggest_functions=False)`. |

### Behavioral specification

1. Exception Specification (`jmespath/exceptions.py`):
   - Update `UnknownFunctionError(function_name, similar=None)`:
     - Set attributes `self.function_name = function_name` and `self.similar = similar`.
     - When `similar` is non-empty, `str(error)` is `"Unknown function: <function_name>(), did you mean: <similar>()?"`.
     - When `similar` is None or omitted, `str(error)` is `"Unknown function: <function_name>()"`.
     - Support legacy single-argument string compatibility if formatted as `"Unknown function: <name>()"`.

2. Function Registry Resolution (`jmespath/functions.py`):
   - In `Functions.call_function(function_name, resolved_args, suggest=True)`:
     - If `function_name` is missing from `FUNCTION_TABLE`:
       - If `suggest` is True, find closest match using `difflib.get_close_matches(function_name, list(self.FUNCTION_TABLE.keys()), n=1, cutoff=0.6)`.
       - If a match is found, pass `similar=match[0]` to `UnknownFunctionError(function_name, similar=match[0])`.
       - Otherwise, raise `UnknownFunctionError(function_name)`.

3. Options & AST Interpreter Coordination (`jmespath/visitor.py`):
   - In `Options(dict_cls=None, custom_functions=None, suggest_functions=True)`:
     - Store `self.suggest_functions = suggest_functions`.
   - In `TreeInterpreter.visit_function_expression`:
     - Pass `suggest=getattr(self._options, 'suggest_functions', True)` to `self._functions.call_function(...)`.

4. Allowed Changes and Test Target:
   - Allowed change paths:
     - `jmespath/exceptions.py`
     - `jmespath/functions.py`
     - `jmespath/visitor.py`
     - `tests/test_functions.py`
   - Visible test target: `tests/test_functions.py`

## Validation command

```sh
uv run --no-sync selfforge-agent eval-validate \
  --manifest benchmark/validated-candidates/suite-jmespath-unknown-function.json \
  --sandbox host --trust-local
```
