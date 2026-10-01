"""Quick checks for the logic that must not break.  Run:  python tests/test_core.py"""
import os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATA_FILE"] = os.path.join(tempfile.mkdtemp(), "test.json")  # never touch the real data

from backend.agents.matcher import ranking
from backend.agents.reader import Profile
from backend.llm import strict
from backend.server import onboard
from backend.tools.fetch import trim
from backend.tools.instagram import ig_handle
from backend.tools.linkedin import li_slug


def test_ranking():
    prof = {"summary": ""}
    people = {
        "a": {"id": "a", "profile": prof, "seeking": "everyone", "gender": "man",
              "search": {"matches": [{"id": "b", "fit": 70, "reason": "r"}, {"id": "c", "fit": 20, "reason": "r"}]}},
        "b": {"id": "b", "profile": prof, "seeking": "everyone", "gender": ""},
        "c": {"id": "c", "profile": prof, "seeking": "everyone", "gender": ""},
        "d": {"id": "d", "profile": prof, "seeking": "women", "gender": "man"}}  # d only dates women → excluded for a
    verdict = lambda s: {"score": s, "summary": "", "wants_to_meet": True}
    dates = {"x": {"id": "x", "a": "a", "b": "c", "status": "done", "verdicts": {"a": verdict(80), "c": verdict(90)}}}
    rows = ranking("a", people, dates)
    assert [(r["id"], r["score"], r["stage"]) for r in rows] == [("c", 84, "dated"), ("b", 70, "pre-date")], rows


def test_links_and_helpers():
    assert li_slug("https://www.linkedin.com/in/jane-doe/") == "jane-doe" and not li_slug("https://evil.com/in/x")
    assert ig_handle("https://instagram.com/jane.doe") == "jane.doe" and not ig_handle("https://instagram.com/p/abc")
    s = strict(Profile.model_json_schema())
    assert s["$defs"]["PartnerBrief"]["additionalProperties"] is False and "evidence" in s["required"]
    assert trim({"a": "x", "profilePicUrl": "u", "e": [{"name": ""}, {}], "l": list(range(30))}) == \
        {"a": "x", "l": list(range(15))}


def test_onboarding_validation():
    base = {"name": "Sam", "age": 29, "gender": "woman", "interested_in": "men"}
    for bad in ({**base, "age": 17}, {**base, "gender": "x"}, {**base, "name": ""},
                {**base, "photo": "javascript:alert(1)"}, {**base, "linkedin": "https://evil.com"}):
        try:
            onboard(bad)
            raise AssertionError(f"accepted {bad}")
        except ValueError:
            pass


if __name__ == "__main__":
    test_ranking()
    test_links_and_helpers()
    test_onboarding_validation()
    print("all tests passed")
