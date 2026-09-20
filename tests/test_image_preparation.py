import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tools import gah_images as images
from tools import prepare_authority_runtime as authority, prepare_guardrail_runtime as guardrail

class ImagePreparationTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.base=Path(temp.name); self.root=self.base/"source"; self.root.mkdir()
        for name in images.DIRECTORIES: (self.root/name).mkdir()
        for name in set(authority.SOURCES+guardrail.SOURCES+["README.md","LICENSE"]):
            p=self.root/name; p.parent.mkdir(parents=True,exist_ok=True); p.write_text("source",encoding="utf-8")
        for kind in images.BUILDERS: (self.root/"config"/(kind+"-runtime.lock.json")).write_text("{}",encoding="utf-8")
        self.docker=self.base/"docker"; self.docker.write_bytes(b"docker")
        self.digest=hashlib.sha256(b"docker").hexdigest(); self.destination=self.base/"prepared"
        self.calls=[]; self.built={}; self.base_available=True
        self.engine_available=True; self.invalid_server_reply=False
        patcher=patch.object(images.shutil,"which",return_value=str(self.docker)); patcher.start(); self.addCleanup(patcher.stop)

    def invoke(self,argv,cwd,timeout,env):
        self.calls.append(argv)
        if "version" in argv:
            self.assertLessEqual(timeout,10)
            fixed_host = "npipe:////./pipe/dockerDesktopLinuxEngine" if images.os.name == "nt" else "unix:///var/run/docker.sock"
            self.assertEqual(argv[1:3],["--host",fixed_host])
            self.assertEqual(argv[3:], ["version", "--format", "{{json .Server}}"])
            if not self.engine_available: raise images.PreparationError("IMAGE_PREPARATION_FAILED")
            if self.invalid_server_reply: return b"not-json"
            return b'{"Version":"27.0.0","ApiVersion":"1.46"}'
        if "pull" in argv:
            self.assertEqual(argv[-1],images.BASE); self.base_available=True; return b"pulled"
        if argv[-1]==images.BASE:
            if not self.base_available: raise images.PreparationError("IMAGE_PREPARATION_FAILED")
            return json.dumps([{"Os":"linux","Architecture":"amd64","RepoDigests":[images.BASE]}]).encode()
        if "-m" in argv:
            kind=argv[-1].removeprefix("tools.prepare_").removesuffix("_runtime")
            entry=["/usr/local/bin/python","-I","-B",{"fixture":"/opt/gah/fixture_worker.py","guardrail":"/opt/gah/guardrail_worker.py","authority":"/opt/gah/tools/authority_entry.py"}[kind]]
            lock={"schema_version":1,"base_ref":images.BASE,"image_id":"sha256:"+str(len(self.built)+1)*64,"docker_binary_digest":self.digest,"entrypoint":entry,"environment":[]}
            if kind=="fixture": lock["worker_digest"]=images._sha((cwd/"fixtures/runtime/fixture_worker.py").read_bytes())
            else:
                hashes={p:images._sha((cwd/p).read_bytes()) for p in (guardrail if kind=="guardrail" else authority).SOURCES}
                lock.update(source_sha256=hashes,sources_digest=images._sha(json.dumps(hashes,sort_keys=True,separators=(",",":")).encode()))
            (cwd/"config"/(kind+"-runtime.lock.json")).write_text(json.dumps(lock),encoding="utf-8")
            label={"fixture":"org.gah.fixture.worker-sha256","guardrail":"org.gah.guardrail.sources","authority":"org.gah.authority.sources"}[kind]
            self.built[lock["image_id"]]={"Id":lock["image_id"],"Os":"linux","Architecture":"amd64","Config":{"User":"12000:12000" if kind=="authority" else "65532:65532","WorkingDir":"/work","Entrypoint":entry,"Env":[],"Cmd":[],"Labels":{label:lock.get("sources_digest",lock.get("worker_digest"))}}}
            return b"prepared"
        return json.dumps([self.built[argv[-1]]]).encode()

    def test_fresh_build_order_preserves_source(self):
        before=images._inventory(self.root)
        result=images.prepare(self.destination,offline=True,root=self.root,invoke=self.invoke)
        self.assertEqual(result["status"],"PREPARED"); self.assertFalse(result["ci_eligible"])
        self.assertEqual(images._inventory(self.root),before)
        self.assertEqual([a[-1] for a in self.calls if "-m" in a],["tools.prepare_"+k+"_runtime" for k in images.BUILDERS])
        self.assertTrue((self.destination/".ga/image-preparation/receipt.json").is_file())
        self.assertFalse(any("pull" in a for a in self.calls))

    def test_missing_base_pulls_fixed_digest(self):
        self.base_available=False
        result=images.prepare(self.destination,root=self.root,invoke=self.invoke)
        self.assertFalse(result["base_cache_hit"]); self.assertEqual(sum("pull" in a for a in self.calls),1)

    def test_offline_missing_base_keeps_incomplete(self):
        self.base_available=False
        with self.assertRaisesRegex(images.PreparationError,"BASE_IMAGE_UNAVAILABLE"):
            images.prepare(self.destination,offline=True,root=self.root,invoke=self.invoke)
        self.assertFalse(self.built); self.assertTrue((self.destination/".ga/image-preparation/incomplete.json").is_file())
        self.assertFalse((self.destination/".ga/image-preparation/receipt.json").exists())

    def test_engine_unavailable_is_not_reported_as_missing_base(self):
        self.engine_available=False
        with self.assertRaisesRegex(images.PreparationError,"ENGINE_UNAVAILABLE"):
            images.prepare(self.destination,offline=True,root=self.root,invoke=self.invoke)
        self.assertFalse(self.destination.exists())
        self.assertEqual(sum(a[-1] == images.BASE for a in self.calls),0)

    def test_invalid_engine_reply_is_not_reported_as_missing_base(self):
        self.invalid_server_reply=True
        with self.assertRaisesRegex(images.PreparationError,"ENGINE_UNAVAILABLE"):
            images.prepare(self.destination,offline=True,root=self.root,invoke=self.invoke)
        self.assertFalse(self.destination.exists())
        self.assertEqual(sum(a[-1] == images.BASE for a in self.calls),0)

    def test_reachable_engine_and_missing_offline_base_has_specific_reason(self):
        self.base_available=False
        with self.assertRaisesRegex(images.PreparationError,"BASE_IMAGE_UNAVAILABLE"):
            images.prepare(self.destination,offline=True,root=self.root,invoke=self.invoke)
        self.assertTrue(any("version" in a for a in self.calls))
        self.assertTrue((self.destination/".ga/image-preparation/incomplete.json").is_file())

    def test_existing_destination_untouched(self):
        self.destination.mkdir(); (self.destination/"keep").write_bytes(b"keep")
        with self.assertRaisesRegex(images.PreparationError,"DESTINATION_EXISTS"):
            images.prepare(self.destination,root=self.root,invoke=self.invoke)
        self.assertEqual((self.destination/"keep").read_bytes(),b"keep"); self.assertFalse(self.calls)

    def test_image_mismatch_rejected(self):
        def invoke(*args):
            raw=self.invoke(*args)
            if args[0][-1] in self.built:
                data=json.loads(raw); data[0]["Config"]["User"]="0:0"; return json.dumps(data).encode()
            return raw
        with self.assertRaisesRegex(images.PreparationError,"IMAGE_CONFIG_MISMATCH"):
            images.prepare(self.destination,root=self.root,invoke=invoke)
        self.assertFalse((self.destination/".ga/image-preparation/receipt.json").exists())

    def test_source_change_rejected(self):
        def invoke(*args):
            result=self.invoke(*args)
            if "-m" in args[0]: (self.root/"README.md").write_text("changed",encoding="utf-8")
            return result
        with self.assertRaisesRegex(images.PreparationError,"SOURCE_CHANGED"):
            images.prepare(self.destination,root=self.root,invoke=invoke)

    def test_destination_inside_source_rejected(self):
        with self.assertRaisesRegex(images.PreparationError,"DESTINATION_PATH_INVALID"):
            images.prepare(self.root/"tools/new",root=self.root,invoke=self.invoke)
        self.assertFalse(self.calls)

    def test_inventory_bound_excludes_runtime(self):
        (self.root/".ga").mkdir(); (self.root/".ga/secret").write_text("runtime",encoding="utf-8")
        self.assertNotIn(".ga/secret",images._inventory(self.root))
        with patch.object(images,"MAX_BYTES",1):
            with self.assertRaisesRegex(images.PreparationError,"SOURCE_TOO_LARGE"):
                images.prepare(self.destination,root=self.root,invoke=self.invoke)
        self.assertFalse(self.destination.exists())

    def test_source_link_rejected(self):
        try: (self.root/"tools/link.py").symlink_to(self.root/"README.md")
        except OSError: self.skipTest("symlink unavailable")
        with self.assertRaises(ValueError): images.prepare(self.destination,root=self.root,invoke=self.invoke)
        self.assertFalse(self.destination.exists())

    def test_unknown_lock_schema_is_rejected(self):
        def invoke(*args):
            result=self.invoke(*args)
            if "-m" in args[0]:
                kind=args[0][-1].removeprefix("tools.prepare_").removesuffix("_runtime")
                path=args[1]/"config"/(kind+"-runtime.lock.json")
                lock=json.loads(path.read_text(encoding="utf-8"));lock["schema_version"]=999
                path.write_text(json.dumps(lock),encoding="utf-8")
            return result
        with self.assertRaisesRegex(images.PreparationError,"IMAGE_CONFIG_MISMATCH"):
            images.prepare(self.destination,root=self.root,invoke=invoke)
