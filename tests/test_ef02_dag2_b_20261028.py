from ai_commerce.engineering.profile import ExecutorSelection


def test_ef02_dag2_b():
    s = ExecutorSelection('antigravity','gemini-3.8-flash','medium')
    assert (s.executor,s.model,s.effort) == ('antigravity','gemini-3.8-flash','medium')
