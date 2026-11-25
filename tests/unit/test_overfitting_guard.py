"""The overfitting guard finds fixture vocabulary as whole words, and the skill has none."""

import contextlib
import io
import os
import shutil
import tempfile
import unittest

from tests import overfitting_guard as G
from tests import support as S


class Guard(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="evermap-guard-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_whole_words_only(self):
        S.put(self.root, "a.md", "Bookkeeping in the showroom.\nA Room with a view.\nroom_id and check-in.\n")
        S.put(self.root, "sub/b.py", "def cancelBooking():\n    return 'refunds'\n")
        self.assertEqual(G.scan(self.root), [("a.md", 2, "Room"), ("a.md", 3, "room"), ("a.md", 3, "check-in"),
                                             ("sub/b.py", 1, "cancelBooking"), ("sub/b.py", 2, "refunds")])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(G.main([self.root]), 1)
        self.assertEqual(len(out.getvalue().splitlines()), 5)

    def test_clean_tree(self):
        S.put(self.root, "a.md", "Parcels wait in a locker until the recipient collects them.\n")
        self.assertEqual((G.scan(self.root), G.main([self.root])), ([], 0))

    def test_skill_has_no_fixture_vocabulary(self):
        self.assertEqual(G.scan(G.DEFAULT), [])

    def test_no_skill_md_outside_skills(self):
        found = []
        for folder, dirs, names in os.walk(S.REPO):
            dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
            if "SKILL.md" in names:
                found.append(os.path.relpath(folder, S.REPO).replace(os.sep, "/"))
        self.assertTrue(found and all(f.startswith("skills/") for f in found), found)


if __name__ == "__main__":
    unittest.main()
