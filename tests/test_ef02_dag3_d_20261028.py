from pathlib import Path


def test_ef02_dag3_d_depends_b():
    assert (Path(__file__).parent/'test_ef02_dag3_b_20261028.py').is_file()
