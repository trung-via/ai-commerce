"""No-GitHub/no-Windows deterministic contract tests for HostOps read-only v1."""
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / 'scripts' / 'hostops_request.py'
spec = importlib.util.spec_from_file_location('hostops_request', MODULE)
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
SHA = '7ae8893724b83ea6342fc5d041ee938590fb9cd4'


def valid():
    return dict(schema=h.REQUEST_SCHEMA, request_id='hostops-20261102-001',
                repository=h.REPOSITORY, source_sha=SHA,
                operation='d5_readonly', parameters={})


def checked(data=None, **kw):
    values = dict(repository=h.REPOSITORY, issuer='trung-via', owner='trung-via',
                  source_sha=SHA, is_private=True)
    values.update(kw)
    return h.validate_request(json.dumps(valid() if data is None else data).encode(), **values)


class ValidateTests(unittest.TestCase):
    def test_good_readonly_request(self):
        self.assertEqual(checked()['operation'], 'd5_readonly')
        d = valid(); d['operation'] = 'runner_readonly'
        self.assertEqual(checked(d)['operation'], 'runner_readonly')

    def test_reject_public_and_non_owner(self):
        for kwargs in (dict(is_private=False), dict(issuer='somebot'), dict(owner='somebot'),
                       dict(repository='trung-via/AIOS-renew')):
            with self.subTest(kwargs=kwargs), self.assertRaises(h.RequestRejected):
                checked(**kwargs)

    def test_reject_unsupported_or_dangerous_fields(self):
        invalid = []
        for op in ('send_wake', 'powershell', 'restart_runner', '; Start-Process'):
            d = valid(); d['operation'] = op; invalid.append(d)
        for params in ({'command':'Get-ChildItem Env:'}, {'port':9333}, {'path':'C:\\'}):
            d = valid(); d['parameters'] = params; invalid.append(d)
        d = valid(); d['command'] = 'pwd'; invalid.append(d)
        d = valid(); d['source_sha'] = '0'*40; invalid.append(d)
        d = valid(); d['request_id'] = 'a'; invalid.append(d)
        for d in invalid:
            with self.subTest(request=d), self.assertRaises(h.RequestRejected):
                checked(d)

    def test_reject_duplicate_json_key(self):
        raw = json.dumps(valid()).replace('"parameters": {}', '"parameters": {}, "parameters": {}')
        with self.assertRaises(h.RequestRejected) as ctx:
            h.validate_request(raw.encode(), repository=h.REPOSITORY, issuer='trung-via',
                               owner='trung-via', source_sha=SHA, is_private=True)
        self.assertEqual(ctx.exception.code, 'DUPLICATE_JSON_KEY')

    def test_reject_oversize_or_invalid_utf8(self):
        for raw in (b' ' * 4097, b'\xff'):
            with self.subTest(size=len(raw)), self.assertRaises(h.RequestRejected):
                h.validate_request(raw, repository=h.REPOSITORY, issuer='trung-via',
                                   owner='trung-via', source_sha=SHA, is_private=True)

    def test_validate_cli_outputs_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            request, result = Path(tmp)/'input.json', Path(tmp)/'validated.json'
            request.write_text(json.dumps(valid()), encoding='utf-8')
            args = ['validate', '--request-file', str(request), '--repository', h.REPOSITORY,
                    '--issuer','trung-via','--owner','trung-via','--source-sha',SHA,
                    '--is-private','true','--output',str(result)]
            self.assertEqual(h.main(args), 0)
            self.assertEqual(json.loads(result.read_text())['operation'], 'd5_readonly')
            args[args.index('true')] = 'false'
            self.assertEqual(h.main(args), 2)
            self.assertEqual(json.loads(result.read_text())['status'], 'BLOCKED')


class ExecuteTests(unittest.TestCase):
    def test_command_is_closed_no_shell_and_json_validated(self):
        payload = {'operation':'d5_readonly','session_id':1,'pwsh_major':7,
                   'runner_listener_count':1,'chrome_process_count':14,
                   'cdp_9333_listening':True}
        cp = subprocess.CompletedProcess(args=[], returncode=0,
                                         stdout=json.dumps(payload).encode(), stderr=b'')
        with patch.object(h.subprocess, 'run', return_value=cp) as mock:
            result = h.run_diagnostics(valid(), 'a'*64)
            called = mock.call_args
            self.assertIsInstance(called.args[0], list)
            self.assertNotIn('shell', called.kwargs)
            self.assertEqual(called.args[0][-2:], ['-Operation', 'd5_readonly'])
        self.assertEqual(result['status'], 'OBSERVED')
        self.assertTrue(result['diagnostics']['cdp_9333_listening'])

    def test_reject_bad_diagnostics_or_timeout(self):
        base = {'operation':'d5_readonly','session_id':1,'pwsh_major':7,
                'runner_listener_count':1,'chrome_process_count':1,
                'cdp_9333_listening':True}
        for modifications in ({'session_id':True}, {'command_line':'secret'},
                              {'cdp_9333_listening':'true'}, {'operation':'send_wake'}):
            payload = {**base, **modifications}
            cp = subprocess.CompletedProcess(args=[], returncode=0,
                                             stdout=json.dumps(payload).encode(), stderr=b'')
            with self.subTest(fields=modifications), patch.object(h.subprocess,'run',return_value=cp):
                with self.assertRaises(h.RequestRejected):
                    h.run_diagnostics(valid(), 'a'*64)
        with patch.object(h.subprocess, 'run', side_effect=subprocess.TimeoutExpired('pwsh', 45)):
            with self.assertRaises(h.RequestRejected) as ctx:
                h.run_diagnostics(valid(), 'a'*64)
            self.assertEqual(ctx.exception.code, 'POWERSHELL_UNAVAILABLE_OR_TIMEOUT')


if __name__ == '__main__':
    unittest.main()
