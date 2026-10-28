from ai_commerce.engineering.profile import ExecutorSelection


def test_ef02_live_codex_mxc_retains_executor_selection():
    selection = ExecutorSelection('codex', 'gpt-6.1-sol', 'max')

    assert selection.executor == 'codex'
    assert selection.model == 'gpt-6.1-sol'
    assert selection.effort == 'max'
