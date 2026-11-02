"""HostOps v2 no-network safety and signature tests."""
import base64
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import shutil

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "hostops_v2.py"
spec = importlib.util.spec_from_file_location("hostops_v2", SCRIPT)
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
SHA = "2e612c3fbf7e4eda4032adfae97cf8d625b4c76a"

def req(op="d5_deep_read", **kwargs):
    return {"schema":h.SCHEMA,"request_id":"hostops-v2-test-001","repository":h.REPO,
            "source_sha":SHA,"operation":op,"parameters":kwargs}

def check(r, **kwargs):
    arguments={"repository":h.REPO,"owner":"trung-via","issuer":"trung-via","source_sha":SHA,"private":True}
    arguments.update(kwargs)
    return h.check_request(json.dumps(r).encode(), **arguments)

class HostopsV2Tests(unittest.TestCase):
    def test_read_both_without_params(self):
        for op in h.READ:
            self.assertEqual(check(req(op))["operation"],op)

    def test_private_owner_source_required(self):
        for args in ({"private":False},{"owner":"stranger"},{"issuer":"bot"},{"source_sha":"0"*40}):
            with self.subTest(args=args),self.assertRaises(h.Denied):check(req(),**args)

    def test_read_cannot_be_raw_shell_or_secrets(self):
        for p in ({"command":"Get-ChildItem Env:"},{"path":"C:/Users"},{"port":9333}):
            with self.assertRaises(h.Denied):check(req("d5_deep_read",**p))
        with self.assertRaises(h.Denied):check(req("powershell",command="dir"))

    def test_scratch_marker_bounded(self):
        self.assertEqual(check(req("scratch_marker_write",marker="EF02 diagnosis"))["operation"],"scratch_marker_write")
        for v in ("x"*101,"& rm","\n",""):
            with self.subTest(value=v),self.assertRaises(h.Denied):check(req("scratch_marker_write",marker=v))

    def test_signature_required_and_path_denials(self):
        unsafe=["../runner.txt","C:/Windows/hosts.txt","../foo",".github/settings.md",
                "docs/.ssh/key.txt","safe.ps1","../../secrets.json","x\\y.md"]
        fake=base64.b64encode(b"-----BEGIN SSH SIGNATURE-----\nplaceholder\n-----END SSH SIGNATURE-----\n").decode()
        for p in unsafe:
            with self.subTest(path=p),self.assertRaises(h.Denied):
                check({**req("workspace_file_write",relative_path=p,content="hello"),"approval_signature_b64":fake})
        with self.assertRaises(h.Denied):check(req("workspace_file_write",relative_path="report.txt",content="ok"))

    def test_human_signer_not_enrolled_fails_closed(self):
        r={**req("workspace_file_write",relative_path="notes/report.txt",content="ok"),
           "approval_signature_b64":base64.b64encode(b"-----BEGIN SSH SIGNATURE-----\nX\n-----END SSH SIGNATURE-----\n").decode()}
        check(r)
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(h.Denied) as ex:h.verify_human(r,signers_file=Path(t)/"no-signer")
            self.assertEqual(str(ex.exception),"HUMAN_KEY_NOT_ENROLLED")

    def test_signature_binds_operation_and_contents(self):
        r=req("workspace_file_write",relative_path="notes/report.txt",content="safe")
        before=h.canonical_for_signature(r)
        changed={**r,"parameters":{**r["parameters"],"content":"changed"}}
        self.assertNotEqual(before,h.canonical_for_signature(changed))

    def test_duplicate_key_and_extra_fields_rejected(self):
        raw=json.dumps(req()).replace('"parameters": {}','"parameters": {}, "parameters": {}')
        with self.assertRaises(h.Denied):
            h.check_request(raw.encode(),repository=h.REPO,owner="trung-via",issuer="trung-via",source_sha=SHA,private=True)
        r=req();r["shell"]="pwsh"
        with self.assertRaises(h.Denied):check(r)

    def test_safe_write_no_clobber(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            x=h.write_no_clobber(root,["notes","report.txt"],b"hello")
            self.assertEqual(x,h.hashlib.sha256(b"hello").hexdigest())
            with self.assertRaises(h.Denied):h.write_no_clobber(root,["notes","report.txt"],b"other")
            self.assertEqual((root/"notes"/"report.txt").read_bytes(),b"hello")

    def test_diagnostic_shape_and_no_shell(self):
        d={"operation":"d5_deep_read","session_id":1,"pwsh_major":7,"chrome_count":23,
           "runner_count":4,"cdp_9333_count":0,"cdp_9222_count":1,"tcp_known":True,
           "process_flags_known":True,"d5_debug_flag_count":1,"d5_profile_flag_count":1,"task_state":"Running"}
        p=subprocess.CompletedProcess(args=[],returncode=0,stdout=json.dumps(d).encode(),stderr=b"")
        with patch.object(h.subprocess,"run",return_value=p) as mock:
            o=h.run(req(),"a"*64,workdir=Path.cwd(),approved_root=None)
            self.assertEqual(o["status"],"OBSERVED")
            self.assertEqual(mock.call_args.args[0][-2:],["-Operation","d5_deep_read"])
            self.assertNotIn("shell",mock.call_args.kwargs)
        d["raw_command_line"]="secret"
        with patch.object(h.subprocess,"run",return_value=subprocess.CompletedProcess([],0,json.dumps(d).encode(),b"")):
            with self.assertRaises(h.Denied):h.run(req(),"a"*64,workdir=Path.cwd(),approved_root=None)

    def test_scratch_write_is_scoped(self):
        with tempfile.TemporaryDirectory() as t:
            o=h.run(req("scratch_marker_write",marker="PASS"),"a"*64,workdir=Path(t),approved_root=None)
            self.assertEqual(o["status"],"WRITTEN")
            self.assertTrue((Path(t)/".local"/"hostops-v2-scratch"/"hostops-v2-test-001.txt").exists())

    def test_missing_approved_root_blocks_write(self):
        with self.assertRaises(h.Denied):h.guarded_root(None)

    def test_ssh_signed_grant_cryptographically_validated(self):
        if not shutil.which("ssh-keygen"):self.skipTest("OpenSSH unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            key=root/"signer"
            subprocess.run(["ssh-keygen","-q","-t","ed25519","-N","","-f",str(key)],check=True,capture_output=True)
            signers=root/"signers"
            signers.write_text("ai-commerce-human "+(root/"signer.pub").read_text())
            r=req("workspace_file_write",relative_path="docs/readme.md",content="approved")
            canonical=root/"canonical.json"
            canonical.write_bytes(h.canonical_for_signature(r))
            subprocess.run(["ssh-keygen","-Y","sign","-n","hostops-v2","-f",str(key),str(canonical)],check=True,capture_output=True)
            r["approval_signature_b64"]=base64.b64encode((root/"canonical.json.sig").read_bytes()).decode("ascii")
            check(r)
            h.verify_human(r,signers_file=signers)
            r["parameters"]["content"]="unapproved change"
            with self.assertRaises(h.Denied) as ex:h.verify_human(r,signers_file=signers)
            self.assertEqual(str(ex.exception),"HUMAN_SIGNATURE_INVALID")

if __name__ == "__main__":unittest.main()
