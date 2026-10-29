# Reproducible dependency images

`selfforge-agent sandbox build` prepares a local test image from a hash-locked requirements file and an existing wheelhouse. It is an explicit operator command, not an Agent tool or automatic installation of repository requirements.

The input contract is intentionally small: exact `name==version` pins with SHA-256 hashes, including transitive dependencies. Comments and backslash continuations are supported. URLs, editable installs, pip directives, markers, extras and source distributions are rejected. Profiles with compiled extensions need wheels matching the base image's Python/platform. Select trusted packages and a trusted local base image.

## Prepare once, execute offline

The default base image contains Python and pytest. Build it if absent:

```sh
docker build -t selfforge-agent-sandbox:latest -f docker/Dockerfile .
```

Dependency download is a separate, explicit network operation. This example uses the checked-in profile for ItsDangerous tests:

```sh
mkdir -p .local/wheelhouse/itsdangerous
python -m pip download --index-url https://pypi.org/simple \
  --only-binary=:all: --require-hashes \
  --dest .local/wheelhouse/itsdangerous -r docker/profiles/itsdangerous.lock

uv run selfforge-agent sandbox build \
  --requirements docker/profiles/itsdangerous.lock \
  --wheelhouse .local/wheelhouse/itsdangerous \
  --base-image selfforge-agent-sandbox:latest \
  --tag selfforge-itsdangerous:locked > .local/itsdangerous-image.json
```

The builder checks all wheel bytes against the lock before invoking Docker. Only the generated Dockerfile, frozen lock bytes and matching wheel files enter the tar context sent over stdin; repository source, `.env` and arbitrary Dockerfiles are not copied. Limits are 128 KiB for the lock, 200 requirements/wheels, 64 MiB per wheel and 128 MiB of total dependency input. Build timeout defaults to 300 seconds (maximum 1800).

Docker uses `--network=none --pull=false`; pip uses `--isolated --no-index --only-binary=:all: --require-hashes --force-reinstall`, followed by `pip check` and a pytest availability check. Missing dependencies, incompatible wheels, hash mismatches and build errors are explicit failures. There is no network fallback. Use the Docker driver with the base image already present in the same daemon; a remote builder without that image is not supported.

## Use the resulting image

The JSON result records the base image ID, built image ID, lock hash and individual wheel hashes. The builder binds a temporary alias to the resolved base ID, and obtains its own result through `--iidfile` so concurrent retagging cannot change the reported result. Only that temporary base alias is removed afterward.

Use the returned immutable `image_id` for stable runs:

```sh
SELFFORGE_TEST_IMAGE=$(python -c 'import json; print(json.load(open(".local/itsdangerous-image.json"))["image_id"])')
uv run selfforge-agent test --repo /path/to/itsdangerous --image "$SELFFORGE_TEST_IMAGE"
uv run selfforge-agent code --repo /path/to/itsdangerous --image "$SELFFORGE_TEST_IMAGE" \
  --task 'Inspect the code, implement the requested change, and run tests'
```

In Web Coding, choose **Docker 隔离** and paste the ID into **Docker 镜像**. Blank input uses the default sandbox. The field is hidden for Host execution. Resume currently requires selecting the same image again; image choice is not yet bound to durable session metadata. The `eval-suite` manifest also accepts the image in each case and already resolves it once per evaluation.

Images add `/workspace/src:/workspace` to `PYTHONPATH`, allowing source-layout projects to test their current checkout without installing the checkout itself. Commands/tests still use the existing isolated executor, with no network and discarded container writes. This does not enable project build hooks, system-package installation, automatic dependency discovery or graphical image building. Builds are reproducible in inputs and execution environment, not guaranteed byte-identical image hashes across Docker versions, architectures or repeated builds. Preserve the base and result IDs for comparisons; mutable tags are convenience names.

## Real repository verification — 2026-10-30

Source: `pallets/itsdangerous`, fixed SHA `672971d66a2ef9f85151e53283113f33d642dabd` (BSD-3-Clause). The profile versions/hashes come from that revision's `uv.lock`; no upstream source or wheel binaries are committed here. A fresh temporary clone at this SHA ran the entire `tests/` directory using `DockerTestRunner`:

| Environment | Passed | Failed | Collection errors | Skipped | Exit |
| --- | ---: | ---: | ---: | ---: | ---: |
| Default Python/pytest sandbox | 0 | 0 | 5 | 0 | 2 |
| Dependency image, same source | **297** | **0** | **0** | **0** | **0** |

- Base image: `sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20`
- Lock SHA-256: `884dc01acbd6ed8ae06a14bc5069afe5bb861cf5194d67ac1bd824d59b5c40fc`
- Tested image: `sha256:205fa9f663b395ace68468c42cfdfdb6c0b180593504c67d913634f962860da6`

This verifies the dependency environment, not a Coding Agent success rate. There was no model call or upstream source modification. The automated integration test builds an original tiny wheel fixture offline and checks source-layout imports; it needs a Docker daemon and the default sandbox image. CI runs it with the other Docker tests.


## Development verification

On 2026-10-30, the dependency-image and Web suites passed **24 tests**. The full branch suite passed **148 tests**, with no skips and one existing Starlette/httpx deprecation warning. Ruff, JavaScript syntax (`node --check`) and Git whitespace checks passed. This branch starts from main `278ff34`; it does not include tests added by the still-open PRs #24–#26.

A Chromium check confirmed the custom image field, Host-mode hiding and the submitted `image`/`sandbox` values. The browser submission was intercepted before the API to avoid starting a model task; the API forwarding has a separate pytest regression. At a 390px viewport the document width remained 390px. The temporary browser and server were closed afterward.
