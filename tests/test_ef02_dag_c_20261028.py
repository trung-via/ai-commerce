from pathlib import Path

from ai_commerce.engineering.profile import ExecutorSelection


def test_ef02_dag_child_c():
    dependency_a = Path(__file__).parent / 'test_ef02_dag_a_20261028.py'
    assert dependency_a.exists()
    assert 'def test_ef02_dag_root_a(' in dependency_a.read_text(encoding='utf-8')

    selection = ExecutorSelection('codex', 'gpt-6.1-sol', 'max')

    assert (selection.executor, selection.model, selection.effort) == (
        'codex',
        'gpt-6.1-sol',
        'max',
    )
