from ai_commerce.engineering.profile import ExecutorSelection


def test_ef02_dag3_a():
    s=ExecutorSelection('codex','gpt-6.1-sol','max')
    assert (s.executor,s.model,s.effort)==('codex','gpt-6.1-sol','max')
