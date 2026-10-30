"""Public-lifecycle checks; no dependency on an Agent-generated regression test."""

import asyncio

import pytest
from pocketflow import AsyncBatchNode, AsyncParallelBatchNode


def run_batch(kind, items):
    calls = []
    posted = []

    class Probe(kind):
        async def prep_async(self, shared):
            return items

        async def exec_async(self, item):
            calls.append(item)
            return item * 2

        async def post_async(self, shared, prep_result, result):
            posted.append(result)
            return result

    result = asyncio.run(Probe().run_async({}))
    return result, calls, posted


@pytest.mark.parametrize("kind", [AsyncBatchNode, AsyncParallelBatchNode])
def test_none_prep_is_empty_and_posts(kind):
    try:
        result = run_batch(kind, None)
    except TypeError:
        result = "None was iterated"
    assert result == ([], [], [[]])


@pytest.mark.parametrize("kind", [AsyncBatchNode, AsyncParallelBatchNode])
def test_empty_sequence_still_posts(kind):
    assert run_batch(kind, []) == ([], [], [[]])


@pytest.mark.parametrize("kind", [AsyncBatchNode, AsyncParallelBatchNode])
def test_nonempty_sequence_keeps_order(kind):
    assert run_batch(kind, [3, 1, 3]) == ([6, 2, 6], [3, 1, 3], [[6, 2, 6]])


@pytest.mark.parametrize("kind", [AsyncBatchNode, AsyncParallelBatchNode])
def test_generator_is_consumed_once(kind):
    assert run_batch(kind, (n for n in [2, 4])) == ([4, 8], [2, 4], [[4, 8]])


@pytest.mark.parametrize("kind", [AsyncBatchNode, AsyncParallelBatchNode])
def test_execution_failure_still_propagates(kind):
    class Failing(kind):
        async def prep_async(self, shared):
            return [1]

        async def exec_async(self, item):
            raise ValueError("preserve execution error")

    with pytest.raises(ValueError, match="preserve execution error"):
        asyncio.run(Failing().run_async({}))
