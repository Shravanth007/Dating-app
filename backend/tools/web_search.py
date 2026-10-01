"""Tool: search the web (OpenRouter's web search plugin) and return results with real URLs."""
from ..llm import chat


def web_search(query):
    msg = chat([{"role": "user", "content": f"Search the web for: {query}\nList the most relevant results: title, exact "
                 "URL, one-line snippet. Include LinkedIn (linkedin.com/in/...) and Instagram (instagram.com/...) "
                 "profile URLs when you find them. Only list URLs you actually found."}], web=True, max_tokens=3000)
    urls = [a["url_citation"]["url"] for a in msg.get("annotations") or [] if a.get("type") == "url_citation"]
    return {"results": (msg.get("content") or "")[:6000], "urls": list(dict.fromkeys(urls))[:25]}
