"""Blog bot: weekly Telegram previews; on ✅ publishes a post to the blog, DEV and LinkedIn.

Run by .github/workflows/blog.yml every 15 minutes:
  python blog/blogbot.py           handle button taps, and on Mondays from 09:00 Dublin send the week's previews
  python blog/blogbot.py dry-run   send next week's previews without buttons, publish nothing
  python blog/blogbot.py send-now [id ...]  send previews with buttons now (used during setup)
"""
import datetime
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tgbot import tg  # noqa: E402

BLOG_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(BLOG_DIR)
STATE_FILE = os.path.join(BLOG_DIR, "bot_state.json")
SITE = os.environ.get("SITE_DIR", os.path.join(REPO, "site"))  # checkout of malavnaik99.github.io
BLOG = "https://malavnaik.com"
DUBLIN = ZoneInfo("Europe/Dublin")
PER_WEEK = 2
NAMES = {"blog": "your blog", "devto": "DEV", "linkedin": "LinkedIn", "medium": "Medium (you import it)"}


# ---------- posts ----------

def read_post(path):
    text = open(path, encoding="utf-8").read()
    _, fm, body = text.split("---\n", 2)
    meta = yaml.safe_load(fm)
    if "slug" in meta:  # the site's permalink is /blog/:slug/, so DEV and LinkedIn always point at the original
        meta["canonical_url"] = f"{BLOG}/blog/{meta['slug']}/"
    return meta, body


def load_posts():
    posts = [(p, *read_post(p)) for p in glob.glob(os.path.join(BLOG_DIR, "posts", "*.md"))]
    return sorted(posts, key=lambda p: p[1]["id"])


def next_posts(posts, n=PER_WEEK):
    """Lowest-id posts that are ready, fact-checked, not a pitch and not yet on the blog."""
    return [p for p in posts if p[1].get("status") == "ready" and "verify" not in p[1]
            and "pitch" not in p[1] and not p[1].get("blog_url")][:n]


def set_field(path, key, value):
    """Set one front-matter field in place, leaving the rest of the file untouched."""
    text = open(path, encoding="utf-8").read()
    _, fm, body = text.split("---\n", 2)
    line = f"{key}: {json.dumps(value, ensure_ascii=False)}\n"
    lines = [line if l.startswith(f"{key}:") else l for l in fm.splitlines(keepends=True)]
    if line not in lines:
        lines.append(line)
    open(path, "w", encoding="utf-8").write(f"---\n{''.join(lines)}---\n{body}")


def record(path, key, value):
    """Save a platform result and commit it at once, so a crash can never cause a double post."""
    set_field(path, key, value)
    if os.environ.get("COMMIT") == "1":
        git(REPO, "commit", "-qam", f"blog bot: {key} for {os.path.basename(path)}")
        git(REPO, "push", "-q")


def to_site_markdown(text):
    """Post file -> Jekyll post: absolute image paths, drop the '# Title' line (the layout prints it)."""
    _, fm, body = text.split("---\n", 2)
    fm = re.sub(r'^(image: "?)images/', r"\1/images/", fm, flags=re.M)
    body = re.sub(r"^# .*\n\n?", "", body, count=1, flags=re.M).replace("](../images/", "](/images/")
    body = re.sub(r"(!\[[^\]]*\]\(/images/([^)]+)\))", with_size, body)
    return f"---\n{fm}---\n{body}"


def with_size(m):
    """Add the photo's width and height (kramdown attributes) so the page doesn't jump while it loads."""
    path = os.path.join(BLOG_DIR, "images", m.group(2))
    if not os.path.exists(path):
        return m.group(1)
    from PIL import Image
    with Image.open(path) as im:
        w, h = im.size
    return f'{m.group(1)}{{: width="{w}" height="{h}"}}'


def plain_body(body):
    """Body without the title and image lines, for previews and DEV."""
    body = re.sub(r"^# .*\n\n?", "", body, count=1, flags=re.M)
    return re.sub(r"^!\[.*\n\n?", "", body, count=1, flags=re.M).strip()


# ---------- platforms ----------

def http(url, data=None, headers=None, method=None, raw=None):
    hdrs = {"User-Agent": "malav-blog-bot", **(headers or {})}
    if data is not None:
        raw, hdrs["Content-Type"] = json.dumps(data).encode(), "application/json"
    req = urllib.request.Request(url, data=raw, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read()
        return r.headers, (json.loads(body) if body.strip() else None)


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True)


def publish_blog(path, meta):
    slug = meta["slug"]
    if not glob.glob(os.path.join(SITE, "_posts", f"*-{slug}.md")):
        day = datetime.datetime.now(DUBLIN).date().isoformat()
        os.makedirs(os.path.join(SITE, "_posts"), exist_ok=True)
        with open(os.path.join(SITE, "_posts", f"{day}-{slug}.md"), "w", encoding="utf-8") as f:
            f.write(to_site_markdown(open(path, encoding="utf-8").read()))
        # Never overwrite: the site's copies may have been compressed or edited since the bot first published them.
        shutil.copytree(os.path.join(BLOG_DIR, "images"), os.path.join(SITE, "images"), dirs_exist_ok=True,
                        copy_function=lambda src, dst: os.path.exists(dst) or shutil.copy2(src, dst))
        git(SITE, "add", "-A")
        git(SITE, "commit", "-qm", f"Publish: {meta['title']}")
        git(SITE, "pull", "-q", "--rebase")  # the site also gets edits from Claude Code; never push over them
        git(SITE, "push", "-q")
    url = meta["canonical_url"]
    for _ in range(60):  # GitHub Pages usually needs 1-3 minutes
        try:
            urllib.request.urlopen(url, timeout=20)
            return url
        except urllib.error.URLError:
            time.sleep(15)
    raise RuntimeError("the blog page didn't come online within 15 minutes")


def publish_devto(meta, body):
    tags = [re.sub(r"[^a-z0-9]", "", t.lower()) for t in meta.get("tags", [])][:4]
    _, art = http("https://dev.to/api/articles", {"article": {
        "title": meta["title"], "body_markdown": plain_body(body).replace("](../images/", f"]({BLOG}/images/"),
        "published": True, "canonical_url": meta["canonical_url"], "main_image": f"{BLOG}/{meta['image']}",
        "description": meta.get("description", ""), "tags": tags,
    }}, {"api-key": os.environ["DEVTO_API_KEY"], "Accept": "application/vnd.forem.api-v1+json"})
    return art["url"]


LINKEDIN_CLIENT_ID = os.environ.get("LINKEDIN_CLIENT_ID", "")
LINKEDIN_REDIRECT = "https://malavnaik99.github.io/linkedin-callback/"  # must match the LinkedIn app exactly
LINKEDIN_API = {"Linkedin-Version": "202607", "X-Restli-Protocol-Version": "2.0.0"}


def linkedin_login_link():
    return ("https://www.linkedin.com/oauth/v2/authorization?response_type=code"
            f"&client_id={LINKEDIN_CLIENT_ID}&redirect_uri={urllib.parse.quote(LINKEDIN_REDIRECT, safe='')}"
            "&scope=openid%20profile%20w_member_social")


def little_text(text):
    """LinkedIn post text treats these characters as markup unless they're escaped."""
    return re.sub(r"([\\|{}@\[\]()<>#*_~])", r"\\\1", text)


def publish_linkedin(meta):
    state = load_state()
    token = os.environ.get("LINKEDIN_ACCESS_TOKEN")
    if not token or not state.get("linkedin_person"):
        raise RuntimeError("LinkedIn isn't connected yet")
    if state.get("linkedin_expires", "") <= datetime.date.today().isoformat():
        raise RuntimeError(f"your LinkedIn login has expired. Log in again here and send me the code:\n{linkedin_login_link()}")
    auth = {"Authorization": f"Bearer {token}", **LINKEDIN_API}
    person = f"urn:li:person:{state['linkedin_person']}"
    _, up = http("https://api.linkedin.com/rest/images?action=initializeUpload",
                 {"initializeUploadRequest": {"owner": person}}, auth)
    with open(os.path.join(BLOG_DIR, meta["image"]), "rb") as f:
        photo = f.read()
    try:  # urllib would otherwise label the bytes as a web form, which the upload endpoint rejects
        http(up["value"]["uploadUrl"], headers={"Authorization": f"Bearer {token}",
             "Content-Type": "application/octet-stream"}, method="PUT", raw=photo)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"the photo upload failed: {friendly(e)}") from e
    text = f"{meta.get('linkedin_intro') or meta.get('description', '')}\n\n{meta['canonical_url']}"
    headers, _ = http("https://api.linkedin.com/rest/posts", {
        "author": person, "commentary": little_text(text), "visibility": "PUBLIC",
        "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
        "content": {"media": {"title": meta["title"], "id": up["value"]["image"], "altText": meta.get("image_alt", "")}},
        "lifecycleState": "PUBLISHED", "isReshareDisabledByAuthor": False,
    }, auth)
    return headers["x-restli-id"]


def connect_linkedin(code, state, say):
    """Swap the code from the callback page for a token and keep it in GitHub's secret store (Malav approved this)."""
    form = urllib.parse.urlencode({"grant_type": "authorization_code", "code": code, "redirect_uri": LINKEDIN_REDIRECT,
                                   "client_id": LINKEDIN_CLIENT_ID,
                                   "client_secret": os.environ["LINKEDIN_CLIENT_SECRET"]}).encode()
    try:
        _, tok = http("https://www.linkedin.com/oauth/v2/accessToken", raw=form,
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
        _, me = http("https://api.linkedin.com/v2/userinfo", headers={"Authorization": f"Bearer {tok['access_token']}"})
        save_github_secret("LINKEDIN_ACCESS_TOKEN", tok["access_token"])
    except Exception as e:
        return say(f"⚠️ That LinkedIn code didn't work ({friendly(e)}). Codes only last 30 minutes. "
                   f"Log in again here and send me the new code:\n{linkedin_login_link()}")
    os.environ["LINKEDIN_ACCESS_TOKEN"] = tok["access_token"]
    expires = datetime.date.today() + datetime.timedelta(seconds=tok["expires_in"])
    state.update(linkedin_person=me["sub"], linkedin_expires=expires.isoformat())
    save_state(state)
    say(f"✅ LinkedIn connected! It stays connected until {expires:%-d %B %Y}. I'll remind you a week before.")


def save_github_secret(name, value):
    from nacl import encoding, public
    repo = os.environ["GITHUB_REPOSITORY"]
    gh = {"Authorization": f"Bearer {os.environ['BLOG_REPO_TOKEN']}", "Accept": "application/vnd.github+json"}
    _, key = http(f"https://api.github.com/repos/{repo}/actions/secrets/public-key", headers=gh)
    box = public.SealedBox(public.PublicKey(key["key"].encode(), encoding.Base64Encoder()))
    sealed = encoding.Base64Encoder().encode(box.encrypt(value.encode())).decode()
    http(f"https://api.github.com/repos/{repo}/actions/secrets/{name}",
         {"encrypted_value": sealed, "key_id": key["key_id"]}, gh, method="PUT")


def linkedin_reminder(state, say):
    """Once, a week before the LinkedIn login runs out."""
    expires = state.get("linkedin_expires")
    if not expires or state.get("linkedin_reminded") == expires:
        return
    if datetime.date.fromisoformat(expires) - datetime.date.today() <= datetime.timedelta(days=7):
        say(f"🔑 Your LinkedIn login for the blog bot runs out on {expires}. Tap this link, log in, then copy the "
            f"code it shows and send it to me here:\n{linkedin_login_link()}")
        state["linkedin_reminded"] = expires
        save_state(state)


def medium_steps(url):
    return (f"📰 Medium: open Medium → your profile picture → Stories → Import a story → paste\n{url}\n"
            "→ Import → Publish.")


def friendly(e):
    if isinstance(e, urllib.error.HTTPError):
        detail = e.read().decode("utf-8", "replace")[:300]
        if detail.lstrip().startswith("<"):
            detail = "(the site sent back a web page instead of an explanation)"
        hint = {401: "the key or login was refused", 403: "permission was refused",
                422: "the post was rejected", 429: "too many requests, try again later"}.get(e.code, "")
        return f"error {e.code} {hint}. Details: {detail}"
    return str(e)


def publish(path, say):
    """Publish one approved post everywhere it isn't yet. Each platform is recorded as soon as it succeeds."""
    meta, body = read_post(path)
    title = meta["title"]
    if "verify" in meta or "pitch" in meta:
        return say(f"⛔ Not posting “{title}”: it still has a "
                   f"{'verify' if 'verify' in meta else 'pitch'} note. Ask Claude Code to sort it out.")
    platforms = meta.get("platforms", [])
    if not meta.get("blog_url"):
        try:
            record(path, "blog_url", publish_blog(path, meta))
        except Exception as e:
            return say(f"⚠️ Couldn't publish “{title}” on your blog, so nothing else was posted: {friendly(e)}")
        say(f"✅ On your blog: {meta['canonical_url']}")
    steps = [("devto", "devto_url", lambda: publish_devto(meta, body)),
             ("linkedin", "linkedin_urn", lambda: publish_linkedin(meta))]
    for platform, field, fn in steps:
        if platform not in platforms or meta.get(field):
            continue
        try:
            result = fn()
            record(path, field, result)
            say(f"✅ On {NAMES[platform]}" + (f": {result}" if result.startswith("http") else ""))
        except Exception as e:
            say(f"⚠️ {NAMES[platform]} didn't work for “{title}”: {friendly(e)}\n"
                "Everything else carried on. Tap ✅ on the preview again later to retry just this part.")
    if "medium" in platforms and not meta.get("medium_reminder_sent"):
        say(medium_steps(meta["canonical_url"]), reply_markup={"inline_keyboard": [[
            {"text": "✅ Done on Medium", "callback_data": f"medium:{meta['id']}"}]]})
        record(path, "medium_reminder_sent", True)
    meta = read_post(path)[0]
    done = {"blog": "blog_url", "devto": "devto_url", "linkedin": "linkedin_urn", "medium": "medium_reminder_sent"}
    if all(meta.get(done[p]) for p in platforms) and meta.get("status") != "published":
        record(path, "status", "published")
    say(status_table(load_posts()))
    update_lab(load_posts(), meta)


def lab_summary(posts, latest):
    """Public numbers for the site's Automation Lab box. Built only from public front matter: no IDs or secrets."""
    live = [m for _, m, _ in posts if m.get("blog_url")]
    return {
        "posts_published": len(live),
        "posts_queued": sum(1 for _, m, _ in posts if not m.get("blog_url")),
        "platforms": {label: sum(1 for m in live if m.get(field)) for _, label, field in TABLE},
        "last_post": {"title": latest["title"], "url": latest["canonical_url"]},
        "updated": datetime.datetime.now(DUBLIN).date().isoformat(),
    }


def update_lab(posts, latest):
    path = os.path.join(SITE, "_data", "automation.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(lab_summary(posts, latest), f, indent=1, ensure_ascii=False)
    if os.environ.get("COMMIT") == "1":
        git(SITE, "add", "_data/automation.json")
        if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=SITE).returncode:
            git(SITE, "commit", "-qm", "Automation Lab: update numbers")
            git(SITE, "pull", "-q", "--rebase")
            git(SITE, "push", "-q")


TABLE = [("blog", "Blog", "blog_url"), ("devto", "DEV", "devto_url"),
         ("linkedin", "LinkedIn", "linkedin_urn"), ("medium", "Medium", "medium_done")]


def status_table(posts):
    """Where every post that has started publishing is; ⏳ = not yet."""
    lines = ["📊 Where your posts are"]
    for _, m, _ in posts:
        if m.get("blog_url"):
            cells = " · ".join(f"{label} {'✅' if m.get(field) else '⏳'}"
                               for p, label, field in TABLE if p in m.get("platforms", []))
            lines.append(f"\n{m['id']} · {m['title'].split(':')[0]}\n    {cells}")
    return "\n".join(lines)


# ---------- telegram ----------

def preview_text(meta, body):
    where = ", ".join(NAMES[p] for p in meta.get("platforms", []))
    text = (f"📝 Post {meta['id']} · {meta['title']}\n\n{meta.get('description', '')}\n\n"
            f"{plain_body(body)[:900].rsplit(chr(10) * 2, 1)[0]}\n\n[…]\n\nGoes to: {where}\nBlog link: {meta['canonical_url']}")
    if "linkedin" in meta.get("platforms", []):
        text += f"\n\nLinkedIn intro:\n{meta.get('linkedin_intro') or meta.get('description', '')}"
    return text


def send_preview(chat, meta, body, buttons=True):
    markup = {"inline_keyboard": [[{"text": "✅ Post", "callback_data": f"post:{meta['id']}"},
                                   {"text": "❌ Skip", "callback_data": f"skip:{meta['id']}"}]]}
    tg("sendMessage", chat_id=chat, text=preview_text(meta, body), **({"reply_markup": markup} if buttons else {}))


def send_previews(chat, buttons=True):
    posts = load_posts()
    picked = next_posts(posts)
    if not picked:
        tg("sendMessage", chat_id=chat, text="📭 No posts are ready this week. Ask Claude Code to get the next ones ready.")
    for _, meta, body in picked:
        send_preview(chat, meta, body, buttons)
    last = picked[-1][1]["id"] if picked else 10**9
    for _, meta, _ in posts:
        if "pitch" in meta and meta["id"] < last and meta.get("status") != "published":
            tg("sendMessage", chat_id=chat, text=f"💡 Post {meta['id']} “{meta['title']}” is set aside for you to pitch to "
               f"{meta['pitch']}. Pitch it yourself, or ask Claude Code to remove the pitch so it posts normally.")
    return picked


def handle_taps(state, chat):
    handled = set()
    say = lambda t, **kw: tg("sendMessage", chat_id=chat, text=t, **kw)  # noqa: E731
    for upd in tg("getUpdates", offset=state["offset"], timeout=0, allowed_updates='["callback_query","message"]'):
        state["offset"] = upd["update_id"] + 1
        save_state(state)
        msg = upd.get("message", {})
        # A pasted LinkedIn code: one long word with no spaces, sent from Malav's chat.
        if str(msg.get("chat", {}).get("id")) == chat and re.fullmatch(r"[A-Za-z0-9_\-]{40,}", msg.get("text", "").strip()):
            connect_linkedin(msg["text"].strip(), state, say)
            continue
        if str(msg.get("chat", {}).get("id")) == chat and msg.get("text") and msg["text"] != "/start":
            say("🤖 I only understand two things: the ✅/❌ buttons, and a LinkedIn code. To send a code, paste only "
                "the code from your blog's “Your LinkedIn code” page, with nothing else in the message.")
            continue
        cq = upd.get("callback_query")
        # Only Malav's chat may approve: anyone can find a public bot and press buttons.
        if not cq or str(cq["message"]["chat"]["id"]) != chat:
            continue
        action, post_id = cq["data"].split(":", 1)
        try:
            tg("answerCallbackQuery", callback_query_id=cq["id"], text="Got it")
        except urllib.error.HTTPError:
            pass  # taps older than a few minutes can't be answered; harmless
        if cq["message"]["message_id"] in handled:
            continue  # a double tap on the same preview
        handled.add(cq["message"]["message_id"])
        mark = {"post": "\n\n✅ Approved, publishing…", "skip": "\n\n❌ Skipped this week",
                "medium": "\n\n✅ Marked as on Medium"}[action]
        try:
            # Keep the buttons, so a platform that failed can be retried with another ✅.
            tg("editMessageText", chat_id=chat, message_id=cq["message"]["message_id"], text=cq["message"]["text"] + mark,
               reply_markup=cq["message"].get("reply_markup", {}))
        except urllib.error.HTTPError as e:  # cosmetic only, e.g. the message already shows this mark
            print("couldn't edit preview:", e.read().decode("utf-8", "replace"))
        path = next((p for p, m, _ in load_posts() if str(m["id"]) == post_id), None)
        if path and action == "post":
            publish(path, say)
        elif path and action == "medium":
            record(path, "medium_done", True)
            say(status_table(load_posts()))


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"offset": 0, "weekly_sent": ""}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=1)


def main():
    chat = os.environ["TELEGRAM_CHAT_ID"]
    if sys.argv[1:] == ["dry-run"]:
        picked = send_previews(chat, buttons=False)
        tg("sendMessage", chat_id=chat, text="🧪 That was a test run. Nothing was published and there are no "
           "buttons. In the real Monday message, ✅ Post would publish each post to the places listed under "
           "“Goes to”, and ❌ Skip would hold it until next week.")
        return print("dry run sent:", [m["id"] for _, m, _ in picked])
    if sys.argv[1:2] == ["send-now"]:  # setup only: previews with buttons right away (this week's, or the given ids)
        if sys.argv[2:]:
            return [send_preview(chat, m, b) for _, m, b in load_posts() if str(m["id"]) in sys.argv[2:]]
        return send_previews(chat)
    state = load_state()
    handle_taps(state, chat)
    linkedin_reminder(state, lambda t: tg("sendMessage", chat_id=chat, text=t))
    now = datetime.datetime.now(DUBLIN)
    if now.weekday() == 0 and now.hour >= 9 and state["weekly_sent"] != now.date().isoformat():
        send_previews(chat)
        state["weekly_sent"] = now.date().isoformat()
        save_state(state)


if __name__ == "__main__":
    main()
