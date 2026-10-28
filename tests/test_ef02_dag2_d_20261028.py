from pathlib import Path


def test_ef02_dag2_d_sees_b():
    assert (Path(__file__).parent / 'test_ef02_dag2_b_20261028.py').is_file()
