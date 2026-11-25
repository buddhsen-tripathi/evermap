"""changes: changed files to elements, unmapped and out-of-scope files, renames and deletions (M1.7)."""

import hashlib
import os
import unittest

from tests import support as S
from evermap_lib import evidence as E
from evermap_lib import gitio

COLLECT, RULE, INV = "operation:parcel.collect", "rule:collection.window", "invariant:one-parcel-per-locker"
NOTIFIER, NOTIFY = "component:notifier", "operation:recipient.notify"


class Changes(S.RepoCase):
    def changes(self, *args):
        code, out = S.run_json(self.root, "changes", "--limit", "0", "--max-chars", "0", *args)
        self.assertEqual(code, 0)
        return out

    def append(self, path, text="# Changed.\n"):
        S.put(self.root, path, S.read(self.root, path) + text)

    def test_nothing_changed(self):
        out = self.changes()
        self.assertEqual((out["files"], out["affected"], out["unmapped"], out["renames"]), ([], [], [], []))

    def test_model_changes_are_never_listed(self):
        self.edit(RULE, lambda v: v.update(summary="Changed."))
        S.put(self.root, ".evermap/notes.txt", "x\n")
        self.assertEqual(self.changes()["files"], [])

    def test_modified_file_maps_to_elements(self):
        S.put(self.root, "api/collect.py", S.read(self.root, "api/collect.py").replace("'stored'\"", "'ready'\""))
        out = self.changes()
        self.assertEqual(out["files"], [{"path": "api/collect.py", "status": "modified",
                                         "components": ["component:locker-api"], "elements": [COLLECT, RULE]}])
        self.assertEqual(out["affected"], [{"id": COLLECT, "name": "Collect parcel", "evidence": ["stale"]},
                                           {"id": RULE, "name": "Collection window", "evidence": ["fresh"]}])

    def test_owned_but_unreferenced_file(self):
        S.put(self.root, "api/helpers.py", "X = 1\n")
        out = self.changes()
        self.assertEqual(out["files"], [{"path": "api/helpers.py", "status": "added",
                                         "components": ["component:locker-api"], "elements": []}])
        self.assertEqual((out["affected"], out["unmapped"]), ([], []))

    def test_unmapped_and_out_of_scope(self):
        S.put(self.root, "tools/report.py", "X = 1\n")
        S.put(self.root, "docs/guide.md", "Guide.\n")
        S.put(self.root, "web/node_modules/lib/index.js", "x\n")
        out = self.changes()
        self.assertEqual(out["unmapped"], ["tools/report.py"])
        self.assertEqual(out["out_of_scope"], ["docs/guide.md", "web/node_modules/lib/index.js"])
        self.assertEqual(out["affected"], [])

    def test_test_only_change_maps_to_the_invariant(self):
        self.append("tests/test_deposit.py")
        out = self.changes()
        self.assertEqual(out["files"], [{"path": "tests/test_deposit.py", "status": "modified", "components": [],
                                         "elements": [INV]}])
        self.assertEqual(out["unmapped"], [])

    def test_staged_rename(self):
        S.git(self.root, "mv", "worker/notify.py", "worker/notifier.py")
        out = self.changes()
        self.assertEqual(out["files"], [{"path": "worker/notifier.py", "status": "renamed", "old_path": "worker/notify.py",
                                         "components": [NOTIFIER], "elements": [NOTIFIER, NOTIFY]}])
        self.assertEqual(out["renames"], [{"from": "worker/notify.py", "to": "worker/notifier.py"}])
        self.assertEqual([a["evidence"] for a in out["affected"]], [["moved"], ["moved"]])

    def test_committed_rename_with_edit(self):
        S.git(self.root, "mv", "api/collect.py", "api/pickup.py")
        self.append("api/pickup.py")
        S.commit(self.root, "rename")
        out = self.changes()
        self.assertEqual([(f["path"], f["status"], f.get("old_path")) for f in out["files"]],
                         [("api/pickup.py", "renamed", "api/collect.py")])
        self.assertEqual(out["files"][0]["elements"], [COLLECT, RULE])

    def test_unstaged_move_is_paired(self):
        os.rename(os.path.join(self.root, "worker", "notify.py"), os.path.join(self.root, "worker", "notifier.py"))
        out = self.changes()
        self.assertEqual(out["renames"], [{"from": "worker/notify.py", "to": "worker/notifier.py"}])

    def test_unrelated_untracked_file_is_not_a_rename(self):
        os.remove(os.path.join(self.root, "worker", "notify.py"))
        S.put(self.root, "worker/other.py", "def unrelated():\n    pass\n")
        out = self.changes()
        self.assertEqual([(f["path"], f["status"]) for f in out["files"]],
                         [("worker/notify.py", "deleted"), ("worker/other.py", "added")])
        self.assertEqual(out["renames"], [])

    def test_deletion(self):
        S.git(self.root, "rm", "-q", "db/schema.sql")
        S.commit(self.root, "drop schema")
        out = self.changes()
        self.assertEqual(out["files"], [{"path": "db/schema.sql", "status": "deleted", "components": ["component:locker-db"],
                                         "elements": ["channel:parcel-outbox", "component:locker-db", "entity:locker",
                                                      "entity:parcel"]}])
        self.assertEqual({a["id"]: a["evidence"] for a in out["affected"]},
                         {i: ["missing"] for i in out["files"][0]["elements"]})

    def test_stat_only_change_is_not_a_change(self):
        path = os.path.join(self.root, "api", "deposit.py")
        os.utime(path, (1, 1))
        self.assertEqual(self.changes()["files"], [])

    @unittest.skipIf(os.name == "nt", "Windows file names cannot contain a newline")
    def test_newline_in_a_path_keeps_later_files_in_step(self):
        odd = "a\nodd.py"  # sorts before api/, so a framing error would shift every later blob
        S.put(self.root, odd, "X = 1\n")
        S.commit(self.root, "odd name")
        self.assertEqual(gitio.blobs(self.root, ["HEAD:" + odd, "HEAD:api/deposit.py", "HEAD:nope"]),
                         [None, gitio.show(self.root, "HEAD", "api/deposit.py"), None])
        S.put(self.root, odd, "X = 2\n")
        self.append("api/collect.py")
        os.utime(os.path.join(self.root, "api", "deposit.py"), (1, 1))
        self.assertEqual([f["path"] for f in self.changes()["files"]], [odd, "api/collect.py"])

    LFS_CONTENT = b"\x89PNG\r\n\x1a\n not really an image\n"

    def lfs_commit(self):
        """Commit an LFS pointer, then check out the real content as git-lfs would (filters stay off)."""
        pointer = "version https://git-lfs.github.com/spec/v1\noid sha256:%s\nsize %d\n" % (
            hashlib.sha256(self.LFS_CONTENT).hexdigest(), len(self.LFS_CONTENT))
        S.put(self.root, ".gitattributes", "*.png filter=lfs diff=lfs merge=lfs -text\n")
        S.put(self.root, "api/logo.png", pointer)
        S.commit(self.root, "lfs")
        self.assertEqual(gitio.lfs_pointer(pointer.encode()),
                         (hashlib.sha256(self.LFS_CONTENT).hexdigest(), len(self.LFS_CONTENT)))
        with open(os.path.join(self.root, "api", "logo.png"), "wb") as handle:
            handle.write(self.LFS_CONTENT)

    def test_unchanged_lfs_file_is_not_a_change(self):
        self.lfs_commit()
        self.assertEqual(self.changes("--since", "HEAD")["files"], [])
        self.assertFalse(gitio.is_dirty(self.root))
        _, status = S.run_json(self.root, "status")  # against the synced commit, where the file did not exist
        self.assertFalse(status["working_tree_dirty"])
        self.assertEqual(sorted(status["changed_files_relevant"]), [".gitattributes", "api/logo.png"])
        self.append("worker/notify.py")
        S.commit(self.root, "later")
        self.assertEqual([f["path"] for f in self.changes("--since", "HEAD~1")["files"]], ["worker/notify.py"])
        self.assertEqual([f["path"] for f in self.changes("--since", "HEAD~2")["files"]],
                         [".gitattributes", "api/logo.png", "worker/notify.py"])  # added since then

    def test_changed_lfs_file_is_a_change(self):
        self.lfs_commit()
        with open(os.path.join(self.root, "api", "logo.png"), "wb") as handle:
            handle.write(self.LFS_CONTENT + b"!")
        self.assertEqual([(f["path"], f["status"]) for f in self.changes("--since", "HEAD")["files"]],
                         [("api/logo.png", "modified")])
        self.assertTrue(gitio.is_dirty(self.root))
        S.commit(self.root, "real content")  # without git-lfs the new bytes are committed as they are
        self.assertEqual(self.changes("--since", "HEAD~1")["files"][0]["path"], "api/logo.png")

    def test_lfs_pointer_shape(self):
        oid = "a" * 64
        good = "version https://git-lfs.github.com/spec/v1\noid sha256:%s\nsize 12\n" % oid
        self.assertEqual(gitio.lfs_pointer(good.encode()), (oid, 12))
        for bad in (good.replace("size 12\n", ""), good.replace(oid, oid[:63]), "x" + good,
                    good.replace("v1", "v2"), good.replace("size 12", "size 012")):
            self.assertIsNone(gitio.lfs_pointer(bad.encode()), bad)
        self.assertFalse(gitio.same_content(good.encode(), b"other bytes"))

    def test_since_a_ref(self):
        self.append("worker/notify.py")
        S.commit(self.root, "one")
        self.append("api/collect.py")
        self.assertEqual([f["path"] for f in self.changes("--since", "HEAD")["files"]], ["api/collect.py"])
        self.assertEqual([f["path"] for f in self.changes()["files"]], ["api/collect.py", "worker/notify.py"])

    def test_paging(self):
        for path in ("api/collect.py", "api/deposit.py", "worker/notify.py"):
            self.append(path)
        _, out = S.run_json(self.root, "changes", "--limit", "1", "--offset", "1")
        self.assertEqual(([f["path"] for f in out["files"]], out["total_files"]), (["api/deposit.py"], 3))

    def test_precision_and_recall_against_the_evidence(self):
        """Affected ids are exactly the elements whose file evidence names a changed path."""
        changed = ["api/collect.py", "db/schema.sql", "tests/test_deposit.py"]
        for path in changed:
            self.append(path, "-- changed\n")
        expected = sorted(i for i, e in S.elements().items()
                          if any(E.is_file_evidence(item) and item["path"] in changed for _, item in E.walk(e)))
        self.assertEqual([a["id"] for a in self.changes()["affected"]], expected)


if __name__ == "__main__":
    unittest.main()
