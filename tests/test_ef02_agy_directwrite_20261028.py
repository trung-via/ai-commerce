from ai_commerce.engineering.profile import ExecutorSelection


def test_agy_directwrite():
    s = ExecutorSelection('antigravity', 'gemini-3.8-flash', 'medium')
    assert (s.executor, s.model, s.effort) == ('antigravity', 'gemini-3.8-flash', 'medium')
