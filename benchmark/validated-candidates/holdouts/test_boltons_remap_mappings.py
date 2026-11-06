from boltons.dictutils import FrozenDict, OrderedMultiDict
from boltons.iterutils import remap


def test_remap_frozendict_basic():
    fd = FrozenDict({'alpha': 1, 'beta': 2})
    res = remap(fd)
    assert isinstance(res, FrozenDict)
    assert res == FrozenDict({'alpha': 1, 'beta': 2})
    assert hash(res) == hash(fd)


def test_remap_nested_frozendict_with_transform():
    data = {
        'frozen': FrozenDict({'x': 10, 'y': 20}),
        'list': [FrozenDict({'z': 30})],
        'num': 40,
    }

    def double_ints(path, key, value):
        if isinstance(value, int):
            return key, value * 2
        return key, value

    res = remap(data, visit=double_ints)
    assert isinstance(res['frozen'], FrozenDict)
    assert res['frozen'] == FrozenDict({'x': 20, 'y': 40})
    assert isinstance(res['list'][0], FrozenDict)
    assert res['list'][0] == FrozenDict({'z': 60})
    assert res['num'] == 80


def test_remap_ordered_multidict_duplicate_keys():
    omd = OrderedMultiDict([('k', 1), ('k', 2), ('other', 3)])
    res = remap(omd)
    assert isinstance(res, OrderedMultiDict)
    assert res.getlist('k') == [1, 2]
    assert res.getlist('other') == [3]


def test_remap_ordered_multidict_filtered_transform():
    omd = OrderedMultiDict([('k', 1), ('k', 2), ('k', 3), ('drop', 0)])

    def inc_drop(path, key, value):
        if key == 'drop':
            return False
        if isinstance(value, int):
            return key, value + 10
        return key, value

    res = remap(omd, visit=inc_drop)
    assert isinstance(res, OrderedMultiDict)
    assert res.getlist('k') == [11, 12, 13]
    assert 'drop' not in res


def test_frozendict_copy_protocol():
    fd = FrozenDict({'hello': 'world'})
    c = fd.copy()
    assert isinstance(c, FrozenDict)
    assert c is fd

