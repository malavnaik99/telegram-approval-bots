import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blogbot  # noqa: E402

POST = """---
id: 7
title: "Hello"
slug: hello
image: "images/a.jpg"
platforms: [blog, devto, linkedin, medium]
canonical_url: "https://malavnaik99.github.io/blog/hello/"
status: ready
---

# Hello

![alt](../images/a.jpg)

Body text.
"""


def post(id, **meta):
    return (f"{id}.md", {"id": id, "status": "ready", **meta}, "")


class Tests(unittest.TestCase):
    def test_next_posts_skips_unready_verify_pitch_and_published(self):
        posts = [post(1, blog_url="x"), post(2, pitch="tds"), post(3, verify="check"), post(4, status="draft"),
                 post(5), post(6), post(7)]
        self.assertEqual([m["id"] for _, m, _ in blogbot.next_posts(posts)], [5, 6])

    def test_site_markdown(self):
        out = blogbot.to_site_markdown(POST)
        self.assertIn('image: "/images/a.jpg"', out)
        self.assertIn("![alt](/images/a.jpg)", out)
        self.assertNotIn("# Hello", out)
        self.assertIn('title: "Hello"', out)
        self.assertTrue(out.startswith("---\nid: 7\n"))

    def test_site_markdown_adds_photo_size(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        os.makedirs(os.path.join(d, "images"))
        from PIL import Image
        Image.new("RGB", (30, 40)).save(os.path.join(d, "images", "a.jpg"))
        with mock.patch.object(blogbot, "BLOG_DIR", d):
            out = blogbot.to_site_markdown(POST)
        self.assertIn('![alt](/images/a.jpg){: width="30" height="40"}', out)

    def test_set_field_adds_and_replaces(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        path = os.path.join(d, "p.md")
        open(path, "w").write(POST)
        blogbot.set_field(path, "devto_url", "https://dev.to/x")
        blogbot.set_field(path, "status", "published")
        meta, body = blogbot.read_post(path)
        self.assertEqual((meta["devto_url"], meta["status"]), ("https://dev.to/x", "published"))
        self.assertIn("Body text.", body)

    def test_publish_skips_platforms_already_done(self):
        lab = mock.patch.object(blogbot, "update_lab")
        lab.start()
        self.addCleanup(lab.stop)
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        path = os.path.join(d, "p.md")
        open(path, "w").write(POST.replace("status: ready\n", 'status: ready\nblog_url: "b"\ndevto_url: "d"\n'))
        said = []
        with mock.patch.object(blogbot, "publish_blog") as blog, mock.patch.object(blogbot, "publish_devto") as dev, \
                mock.patch.object(blogbot, "publish_linkedin", return_value="urn:li:share:1") as li:
            blogbot.publish(path, lambda m, **kw: said.append(m))
        blog.assert_not_called()
        dev.assert_not_called()
        li.assert_called_once()
        meta, _ = blogbot.read_post(path)
        self.assertEqual((meta["linkedin_urn"], meta["status"]), ("urn:li:share:1", "published"))
        # A second run posts nothing again.
        said.clear()
        with mock.patch.object(blogbot, "publish_linkedin") as li2:
            blogbot.publish(path, lambda m, **kw: said.append(m))
        li2.assert_not_called()
        self.assertEqual(len(said), 1)  # only the status table
        self.assertTrue(said[0].startswith("📊"))

    def test_publish_refuses_verify_and_stops_when_blog_fails(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        path = os.path.join(d, "p.md")
        open(path, "w").write(POST.replace("status: ready\n", 'status: ready\nverify: "x"\n'))
        said = []
        with mock.patch.object(blogbot, "publish_blog") as blog:
            blogbot.publish(path, lambda m, **kw: said.append(m))
        blog.assert_not_called()
        self.assertIn("verify", said[0])
        open(path, "w").write(POST)
        with mock.patch.object(blogbot, "publish_blog", side_effect=RuntimeError("down")), \
                mock.patch.object(blogbot, "publish_devto") as dev:
            blogbot.publish(path, lambda m, **kw: said.append(m))
        dev.assert_not_called()


    def test_publish_blog_pulls_before_pushing(self):
        site = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, site)
        path = os.path.join(site, "p.md")
        open(path, "w").write(POST)
        calls = []
        with mock.patch.object(blogbot, "SITE", site), mock.patch.object(blogbot, "git", lambda cwd, *a: calls.append(a[0])), \
                mock.patch.object(blogbot.shutil, "copytree"), mock.patch.object(blogbot.urllib.request, "urlopen"):
            blogbot.publish_blog(path, blogbot.read_post(path)[0])
        self.assertEqual(calls, ["add", "commit", "pull", "push"])

    def test_lab_summary_counts_and_holds_no_ids(self):
        posts = [post(1, title="One", canonical_url="https://x/1/", blog_url="b", linkedin_urn="urn:li:share:9"),
                 post(3, title="Three", canonical_url="https://x/3/", blog_url="b", devto_url="d"), post(4, title="Four")]
        s = blogbot.lab_summary(posts, posts[1][1])
        self.assertEqual((s["posts_published"], s["posts_queued"]), (2, 1))
        self.assertEqual(s["platforms"], {"Blog": 2, "DEV": 1, "LinkedIn": 1, "Medium": 0})
        self.assertEqual(s["last_post"], {"title": "Three", "url": "https://x/3/"})
        self.assertNotIn("urn:li", blogbot.json.dumps(s))

    def test_status_table_lists_only_started_posts_and_their_platforms(self):
        posts = [post(1, title="One: x", blog_url="b", linkedin_urn="u", medium_done=True,
                      platforms=["blog", "linkedin", "medium"]),
                 post(3, title="Three", blog_url="b", platforms=["blog", "devto", "linkedin"]), post(4, title="Four")]
        table = blogbot.status_table(posts)
        self.assertIn("1 · One\n    Blog ✅ · LinkedIn ✅ · Medium ✅", table)
        self.assertIn("3 · Three\n    Blog ✅ · DEV ⏳ · LinkedIn ⏳", table)
        self.assertNotIn("Four", table)

    def test_linkedin_text_escapes_markup(self):
        self.assertEqual(blogbot.little_text("C# (data) @me"), r"C\# \(data\) \@me")

    def test_linkedin_reminder_once_within_a_week(self):
        soon = (blogbot.datetime.date.today() + blogbot.datetime.timedelta(days=5)).isoformat()
        state, said = {"linkedin_expires": soon}, []
        with mock.patch.object(blogbot, "save_state"):
            blogbot.linkedin_reminder(state, said.append)
            blogbot.linkedin_reminder(state, said.append)
        self.assertEqual(len(said), 1)
        later = (blogbot.datetime.date.today() + blogbot.datetime.timedelta(days=30)).isoformat()
        said.clear()
        blogbot.linkedin_reminder({"linkedin_expires": later}, said.append)
        self.assertEqual(said, [])


if __name__ == "__main__":
    unittest.main()
