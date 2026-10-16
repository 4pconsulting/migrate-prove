from migrate_prove.row_store import SpilledRowStore


def test_buffer_flushes_to_chunk_files(tmp_path):
    store = SpilledRowStore(buffer_rows=2, spill_dir=tmp_path / "spill", cleanup=False)
    store.append({"id": 1})
    assert store.chunk_paths == []
    store.append({"id": 2})
    assert len(store.chunk_paths) == 1
    store.append({"id": 3})
    store.close()
    assert len(store.chunk_paths) == 2
    rows = store.list_rows()
    assert [row["id"] for row in rows] == [1, 2, 3]


def test_max_rows_raises():
    store = SpilledRowStore(buffer_rows=10, max_rows=2, cleanup=True)
    store.append({"id": 1})
    store.append({"id": 2})
    try:
        store.append({"id": 3})
        raised = False
    except ValueError:
        raised = True
    assert raised
    store.cleanup()


def test_owned_temp_dir_cleaned_on_exit():
    with SpilledRowStore(buffer_rows=1) as store:
        store.append({"a": 1})
        path = store.spill_dir
        assert path.exists()
    assert not path.exists()
