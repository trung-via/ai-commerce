from pathlib import Path


def test_ef02_reused_upstream_a_is_visible():
    assert (Path(__file__).parent / 'test_ef02_dag3_a_20261028.py').is_file()
