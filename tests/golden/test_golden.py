"""Golden outputs on the fixture. Regenerate with EVERMAP_UPDATE_GOLDEN=1, then review the diff."""

import os
import re
import unittest

from tests import support as S
from evermap_lib import render

DATA = os.path.join(S.GOLDEN, "data")
UPDATE = os.environ.get("EVERMAP_UPDATE_GOLDEN") == "1"
DEPOSIT, COLLECT, FLOW = "operation:parcel.deposit", "operation:parcel.collect", "workflow:deposit-parcel"


def normalise(text):
    """Remove what varies by machine or by the synced commit: commit SHAs, hashes, line endings."""
    text = re.sub(r"\b[0-9a-f]{40}\b", "<commit>", text.replace("\r\n", "\n"))
    return re.sub(r"sha256:[0-9a-f]{16}\b", "sha256:<hash>", text)


def golden_files():
    found = {}
    for folder, _, names in os.walk(DATA):
        for name in names:
            if name != ".gitattributes":
                full = os.path.join(folder, name)
                with open(full, encoding="utf-8", newline="") as handle:
                    found[os.path.relpath(full, DATA).replace(os.sep, "/")] = handle.read().replace("\r\n", "\n")
    return found


class Golden(S.RepoCase):
    def outputs(self):
        out = {"views/" + p[len(".evermap/views/"):]: d.decode("utf-8")
               for p, d in S.tree(self.root, ".evermap/views").items()}
        out["impact.json"] = S.run(self.root, "query", "impact", DEPOSIT)[1].decode("utf-8")
        model = S.load(self.root)
        model.manifest = dict(model.manifest, views={"diagrams": ["excalidraw", "eraser"]})
        extra = render.build(model)
        for name in ("technical/locker-api-1.excalidraw", "workflows/deposit-parcel-1.eraserdiagram"):
            out["diagrams/" + name] = extra[render.DIAGRAMS + name]
        out.update(self.kinds_outputs())
        self.edit(DEPOSIT, lambda v: [v.update(summary="Stores the parcel in a free locker."),
                                      v["auth"].update(authentication="optional")])
        self.edit(COLLECT, lambda v: v["relations"].append({"type": "reads", "target": "entity:locker"}))
        self.edit(FLOW, lambda v: v["steps"][1].update(plain="Put the parcel in the nearest free locker."))
        S.write_element(self.root, S.element("role:recipient", "Recipient", "Person collecting a parcel.",
                                             plain="Someone picking up a parcel.", type="user-role",
                                             provenance="inferred", rationale="Named in the collect handler."))
        S.run(self.root, "render")
        S.commit(self.root, "change")
        out["diff.md"] = S.run(self.root, "diff", "HEAD~1", "HEAD", "--format", "md")[1].decode("utf-8")
        return {name: normalise(text) for name, text in out.items()}

    def kinds_outputs(self):
        """Every page with all four kinds chosen on the extended model, and two of its diagram files.
        The working tree is restored afterwards."""
        S.extend_for_kinds(self.root)
        model = S.load(self.root)
        out = {"kinds/views/" + p[len(render.VIEWS):]: text for p, text in render.build(model).items()}
        model.manifest = dict(model.manifest, views=dict(model.manifest["views"], diagrams=["excalidraw", "eraser"]))
        extra = render.build(model)
        for name in ("workflows/deposit-parcel-2.excalidraw", "data-model-1.eraserdiagram"):
            out["kinds/diagrams/" + name] = extra[render.DIAGRAMS + name]
        S.git(self.root, "checkout", "--", ".")
        S.git(self.root, "clean", "-fdq")
        return out

    def test_outputs_match_golden_files(self):
        produced = self.outputs()
        if UPDATE:
            for name in set(golden_files()) - set(produced):
                os.remove(os.path.join(DATA, *name.split("/")))
            for name, text in produced.items():
                S.put(DATA, name, text)
        expected = golden_files()
        missing = sorted(set(produced) - set(expected))
        self.assertFalse(missing, "golden files missing (run with EVERMAP_UPDATE_GOLDEN=1 to write them):\n"
                         + "".join("==> %s <==\n%s" % (name, produced[name]) for name in missing))
        self.assertEqual(sorted(set(expected) - set(produced)), [], "stale golden files")
        for name in sorted(produced):
            self.assertEqual(produced[name], expected[name], name)

    def test_normalise(self):
        self.assertEqual(normalise("commit `%s`\r\nsha256:0123456789abcdef\n" % ("a" * 40)),
                         "commit `<commit>`\nsha256:<hash>\n")


if __name__ == "__main__":
    unittest.main()
