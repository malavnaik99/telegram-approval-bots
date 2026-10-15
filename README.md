# Telegram approval bots

Two small AI automation bots by [Malav Naik](https://malavnaik99.github.io). They share one idea: **the bot does the work, but nothing goes out until I tap ✅ on Telegram.**

| Bot | What it does | Runs on |
|---|---|---|
| [Blog publisher](blog/blogbot.py) | Every Monday it sends me previews of my next 2 blog posts. On ✅ it publishes the post to my GitHub Pages blog, then [DEV](https://dev.to) and LinkedIn, and sends me the Medium import steps. | GitHub Actions, every 15 minutes |
| [Gmail auto-reply](autoreply.py) | Claude drafts a reply to each new email and saves it as a Gmail draft. On ✅ the draft is sent; on ❌ it's deleted. | Any computer that stays on |

Write-ups with diagrams and demos: [blog publisher](https://malavnaik99.github.io/projects/blog-automation-bot/) · [Gmail auto-reply](https://malavnaik99.github.io/projects/gmail-auto-reply/)

## Blog publisher: how it works

```
blog/posts/*.md  (status: ready, no "verify"/"pitch" field)
      │  Monday 09:00 Dublin: next 2 posts by id
      ▼
Telegram preview  [✅ Post] [❌ Skip]
      │  ✅  (only from the owner's chat)
      ▼
1. Blog: write _posts/DATE-slug.md to the Pages repo, push, wait for HTTP 200
2. DEV: Forem API, canonical_url → blog, main_image → blog photo
3. LinkedIn: Posts API with the photo, an intro and the blog link
4. Medium: no API, so a Telegram message with the import steps
      │  each result is written into the post's front matter and committed at once
      ▼
status: published   (a re-run skips every platform that's already done, so nothing is posted twice)
```

- **Safety:** taps from any chat other than the owner's are ignored. Posts with a `verify` (facts to check) or `pitch` field are never published. A failure on one platform is reported on Telegram in plain words, and the others carry on.
- **LinkedIn login renewal:** the owner logs in through a small callback page on the blog and sends the code to the bot. The bot swaps it for a token and stores it as a GitHub Actions secret. A reminder goes out a week before it expires.
- **Status table:** after each post, the bot sends a table of where every post is (✅ / ⏳), and writes public counts to the site (`_data/automation.json`) for a live "Automation Lab" box.

### Setup (GitHub Actions secrets)
`BLOG_TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `DEVTO_API_KEY`, `BLOG_REPO_TOKEN` (fine-grained token: Contents read/write on the Pages repo, Secrets read/write on this repo), `LINKEDIN_CLIENT_SECRET`, and the repository variable `LINKEDIN_CLIENT_ID`. Change the Pages repo name in `.github/workflows/blog.yml` and `BLOG` in `blog/blogbot.py`, then uncomment the schedule.

## Gmail auto-reply: how it works

1. Polls Gmail for unread Primary-inbox mail. Newsletters, receipts and `noreply` senders are skipped.
2. Claude drafts a reply. The email is treated as untrusted data: the model only replies to it and never follows instructions inside it.
3. The reply is saved as a Gmail draft, threaded onto the original, and sent to Telegram with ✅ Send / ❌ Discard buttons.
4. Only the owner's chat can approve. You can also edit the draft in Gmail before tapping Send.

Setup: copy `.env.example` to `.env`, add Google OAuth `credentials.json` (Gmail API, Desktop app), then `pip install -r requirements.txt` and `python autoreply.py`.

## Tests

```bash
pip install -r requirements.txt pyyaml pillow
python -m unittest test_autoreply            # Gmail bot
python -m unittest discover -s blog          # blog publisher: post selection, site transform, never-twice, LinkedIn text, reminders
```

## Shared code
`tgbot.py` is a 10-line Telegram Bot API helper that both bots use. Everything else is Python's standard library, plus PyYAML for the blog bot.
