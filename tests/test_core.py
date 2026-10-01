"""Smallest checks that fail if ranking, URL validation, schema strictifying or trimming break.  python tests/test_core.py"""
import os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATA_FILE"] = os.path.join(tempfile.mkdtemp(), "t.json")

from app.agents import Profile, ranking
from app.llm import strict
from app.scrapers import ig_handle, li_slug, trim


def test_core():
    prof = {"summary": ""}
    ppl = {"a": {"id": "a", "profile": prof, "seeking": "everyone", "gender": "men",
                 "search": {"matches": [{"id": "b", "fit": 70, "reason": "r"}, {"id": "c", "fit": 20, "reason": "r"}]}},
           "b": {"id": "b", "profile": prof, "seeking": "everyone", "gender": ""},
           "c": {"id": "c", "profile": prof, "seeking": "everyone", "gender": ""},
           "d": {"id": "d", "profile": prof, "seeking": "women", "gender": "men"}}
    v = lambda s: {"score": s, "summary": "", "second_date": True}
    dates = {"x": {"id": "x", "a": "a", "b": "c", "status": "done", "verdicts": {"a": v(80), "c": v(90)}}}
    r = ranking("a", ppl, dates)
    assert [(x["id"], x["score"], x["stage"]) for x in r] == [("c", 84, "dated"), ("b", 70, "pre-date")], r  # d excluded
    assert li_slug("https://www.linkedin.com/in/jane-doe/") == "jane-doe" and not li_slug("https://evil.com/in/x")
    assert ig_handle("https://instagram.com/jane.doe") == "jane.doe" and not ig_handle("https://instagram.com/p/abc")
    s = strict(Profile.model_json_schema())
    assert s["$defs"]["PartnerBrief"]["additionalProperties"] is False and "evidence" in s["required"]
    assert trim({"a": "x", "profilePicUrl": "u", "id": 1, "n": None, "l": list(range(30))}) == {"a": "x", "l": list(range(15))}
    print("selftest ok")


if __name__ == "__main__":
    test_core()
