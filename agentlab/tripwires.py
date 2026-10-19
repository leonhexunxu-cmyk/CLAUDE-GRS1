"""Hallucination Tripwires: plant verifiable canary facts, then audit AI answers against them."""

import hashlib
import json
import re

_COLORS = ["amber", "cobalt", "ivory", "scarlet", "teal", "violet", "umber", "jade"]
_THINGS = ["lighthouse", "archive", "foundry", "observatory", "harbor", "greenhouse", "workshop", "depot"]


class Tripwires:
    """Deterministic canaries derived from a secret seed, so you never need to store them to verify."""

    def __init__(self, seed):
        self.seed = str(seed)
        self.canaries = []  # [{'subject', 'value', 'sentence'}]

    def _h(self, label, n):
        d = hashlib.sha256(f"{self.seed}:{label}".encode()).digest()
        return int.from_bytes(d[:8], "big") % n

    def make(self, n=3):
        """Create n canary facts with unlikely-to-be-guessed values."""
        for i in range(n):
            subject = f"the {_COLORS[self._h(f's{i}', 8)]} {_THINGS[self._h(f't{i}', 8)]} code"
            value = f"{self._h(f'v{i}', 10**6):06d}-{chr(65 + self._h(f'l{i}', 26))}"
            self.canaries.append({"subject": subject, "value": value,
                                  "sentence": f"Note: {subject} is {value}."})
        return self.canaries

    def plant(self, document):
        """Insert each canary sentence at a seed-chosen paragraph boundary."""
        paras = document.split("\n\n")
        for i, c in enumerate(self.canaries):
            paras.insert(self._h(f'p{i}', len(paras) + 1), c["sentence"])
        return "\n\n".join(paras)

    def questions(self):
        return [(c, f"What is {c['subject']}?") for c in self.canaries]

    def audit(self, answer):
        """Classify an answer for each canary: correct / contradicted / unused / leaked-in-unrelated."""
        report = []
        low = answer.lower()
        for c in self.canaries:
            has_value = c["value"].lower() in low
            has_subject = c["subject"].lower() in low or c["subject"].replace("the ", "", 1).lower() in low
            if has_value:
                status = "correct"
            elif has_subject and re.search(r"\b\d{3,}-?[A-Za-z]?\b", answer):
                status = "contradicted"  # mentions the subject but with a different code
            elif has_subject:
                status = "unsupported"
            else:
                status = "unused"
            report.append({"subject": c["subject"], "status": status})
        return report

    def verdict(self, answer, expect_all=True):
        """True if retrieval looks healthy: no contradictions and (optionally) every canary recalled."""
        statuses = [r["status"] for r in self.audit(answer)]
        if "contradicted" in statuses or "unsupported" in statuses:
            return False
        return all(s == "correct" for s in statuses) if expect_all else True

    def dumps(self):
        return json.dumps({"seed": self.seed, "canaries": self.canaries})

    @classmethod
    def loads(cls, s):
        d = json.loads(s)
        t = cls(d["seed"])
        t.canaries = d["canaries"]
        return t
