from ai_commerce.engineering.profile import ExecutorSelection


def test_ef02_dag_root_a():
    selection = ExecutorSelection('codex', 'gpt-6.1-sol', 'max')

    assert selection.executor == 'codex'
    assert selection.model == 'gpt-6.1-sol'
    assert selection.effort == 'max'
