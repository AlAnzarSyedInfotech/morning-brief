# The Morning Brief

A free news bot that builds a newspaper-style front page every morning from
trusted RSS feeds and emails you the top 3 headlines with a link to the page.

How it works: GitHub runs `bot/build_paper.py` every day at 10:00 PKT. The
script reads the feeds in `config/sources.yaml`, keeps stories from the last
25 hours, removes duplicates, sorts them into sections and publishes the page
with GitHub Pages. Then `bot/send_alert.py` emails you.

## One-time setup (about 30 minutes)

### 1. Create a GitHub account
Sign up for a free account at github.com if you don't have one.

### 2. Create the repository
1. Click **New repository**.
2. Name it `morning-brief`.
3. Choose **Public**. On a free account, GitHub Pages works with public
   repositories (check GitHub's current plan terms). Anyone with the link can
   see the page; it only contains public news.
4. Click **Create repository**.

### 3. Upload the files
1. Unzip `morning-brief.zip` on your computer.
2. In the new repository, click **Add file > Upload files**.
3. Drag in everything inside the unzipped folder (the `bot`, `config`,
   `docs`, `templates` and `.github` folders, plus `README.md` and
   `requirements.txt`), then click **Commit changes**.
4. Check that the `.github/workflows` folder appears in the repository. Some
   computers hide folders that start with a dot. If it is missing, click
   **Add file > Create new file**, type `.github/workflows/morning-brief.yml`
   as the name, paste in that file's contents, and commit. Do the same for
   `check-feeds.yml`.

### 4. Let the bot save its work
**Settings > Actions > General > Workflow permissions**: choose
**Read and write permissions** and click **Save**.

### 5. Turn on the web page
**Settings > Pages**: under **Build and deployment**, choose
**Deploy from a branch**, then branch **main** and folder **/docs**, and click
**Save**. After a minute or two, the page address appears at the top:
`https://YOUR-USERNAME.github.io/morning-brief/`. Bookmark it.

### 6. Set up the email alert
The alert is sent from a Gmail account using an app password (a separate
password just for this bot, so your real password is never stored).

1. In your Google account, turn on 2-Step Verification, then create an
   **App password** (Google Account > Security). Copy it.
2. In the repository: **Settings > Secrets and variables > Actions > New
   repository secret**. Add these six secrets:

| Name | Value |
|---|---|
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | the Gmail address that sends the alert |
| `SMTP_PASSWORD` | the app password from step 1 |
| `ALERT_TO` | the address that receives the alert |
| `PAGE_URL` | your page address from step 5 |

Secrets are stored encrypted by GitHub and are never shown in the page or logs.
If you skip this step, the paper is still built; only the email is skipped.

### 7. Run it for the first time
1. Open the **Actions** tab. If asked, click to enable workflows.
2. Click **Check feeds > Run workflow**. When it finishes, open the run to
   see which feeds work.
3. Click **Morning Brief > Run workflow**. When it finishes, refresh your page
   and check your inbox.

From then on it runs by itself every morning.

## Everyday changes

- **Add, remove or switch off a source:** edit `config/sources.yaml` in
  GitHub (click the file, then the pencil icon). Set `enabled: true` or
  `false`. Run **Check feeds** after adding a new address.
- **Change the time:** edit the `cron` line in
  `.github/workflows/morning-brief.yml`. Times are in UTC (PKT minus 5 hours).
- **Past editions:** each day's paper is kept in `docs/archive/`, for example
  `https://YOUR-USERNAME.github.io/morning-brief/archive/2026-11-10.html`.

## Events & Certification Desk

Shows Microsoft AI and data events (webinars, Virtual Training Days, skills
challenges, conferences) and certification exam voucher offers found in the
day's stories. It also watches the Virtual Training Days page and lists any
new AI or data line since the previous run. The page watch records a starting
point on its first run, so its results begin on day 2. Words that trigger the
desk are in `config/sources.yaml` under `event_keywords` and `voucher_keywords`.

## Known limits of the free version

- Summaries are the first sentences each source publishes in its own feed.
  AI-written summaries and "why it matters" notes come in a later version.
- GitHub can start scheduled runs late when it is busy, which is why the bot
  starts at 10:00 for a 10:30 read.
- GitHub may pause scheduled workflows in repositories with no activity for a
  long period. The bot's daily commit should count as activity, but if runs
  ever stop, re-enable the workflow from the Actions tab.
- Feed addresses change over time. The footer of each paper lists any source
  that could not be reached that day.
