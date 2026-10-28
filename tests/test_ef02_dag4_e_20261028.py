from pathlib import Path


def test_ef02_fanin_reuses_both_branches():
    root = Path(__file__).parent
    for name in ('test_ef02_dag3_a_20261028.py','test_ef02_dag3_b_20261028.py','test_ef02_dag4_c_20261028.py','test_ef02_dag3_d_20261028.py'):
        assert (root / name).is_file(), name
