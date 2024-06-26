import os
import re
from datetime import datetime, timedelta

import feedparser
import yaml
from bs4 import BeautifulSoup
from dateutil import parser
from github import Github, GithubException


class feed_bot:
    def __init__(self):
        feed_config_file = os.environ.get("FEED_CONFIG_FILE")
        access_token = os.environ.get("GALAXY_SOCIAL_BOT_TOKEN")
        repo_name = os.environ.get("REPO")
        self.feed_bot_path = os.environ.get("FEED_BOT_PATH", "posts/feed_bot")

        with open(feed_config_file, "r") as file:
            self.configs = yaml.safe_load(file)

        g = Github(access_token)
        self.repo = g.get_repo(repo_name)

        self.existing_files = set(
            pr_file.filename
            for pr in self.repo.get_pulls(state="open")
            for pr_file in pr.get_files()
            if pr_file.filename.startswith(self.feed_bot_path)
        )
        git_tree = self.repo.get_git_tree(self.repo.default_branch, recursive=True)
        self.existing_files.update(
            file.path
            for file in git_tree.tree
            if file.path.startswith(self.feed_bot_path) and file.path.endswith(".md")
        )

    def create_pr(self):
        now = datetime.now()
        start_date = now.date() - timedelta(days=1)

        branch_name = f"feed-update-{now.strftime('%Y%m%d%H%M%S')}"
        self.repo.create_git_ref(
            ref=f"refs/heads/{branch_name}",
            sha=self.repo.get_branch("main").commit.sha,
        )

        feed_list = self.configs.get("feeds")
        if feed_list is None:
            raise ValueError("No feeds found in the file")
        for feed in feed_list:
            if feed.get("url") is None:
                raise ValueError(f"No url found in the file for feed {feed}")
            elif feed.get("media") is None:
                raise ValueError(f"No media found in the file for feed {feed}")
            elif feed.get("format") is None:
                raise ValueError(f"No format found in the file for feed {feed}")
            try:
                feed_data = feedparser.parse(feed.get("url"))
            except Exception as e:
                print(f"Error in parsing feed {feed.get('url')}: {e}")
                continue

            folder = feed_data.feed.title.replace(" ", "_").lower()
            feeds_processed = []
            for entry in feed_data.entries:
                date_entry = (
                    entry.get("published")
                    or entry.get("pubDate")
                    or entry.get("updated")
                )
                published_date = parser.isoparse(date_entry).date()

                if entry.link is None:
                    print(f"No link found: {entry.title}")
                    continue

                file_name = entry.link.split("/")[-1] or entry.link.split("/")[-2]
                file_path = f"{self.feed_bot_path}/{folder}/{file_name}.md"

                if published_date < start_date:
                    print(f"Skipping as it is older: {entry.link}")
                    continue

                if file_path in self.existing_files:
                    print(
                        f"Skipping as file already exists: {file_path} for {entry.link}"
                    )
                    continue

                print(f"Processing {file_name} from {entry.link}")

                md_config = yaml.dump(
                    {
                        key: feed[key]
                        for key in ["media", "mentions", "hashtags"]
                        if key in feed
                    }
                )

                format_string = feed.get("format")
                placeholders = re.findall(r"{(.*?)}", format_string)
                values = {}
                for placeholder in placeholders:
                    if placeholder in entry:
                        if "<p>" in entry[placeholder]:
                            soup = BeautifulSoup(entry[placeholder], "html.parser")
                            first_paragraph = soup.find("p")
                            values[placeholder] = first_paragraph.get_text().replace(
                                "\n", " "
                            )
                        else:
                            values[placeholder] = entry[placeholder]
                    else:
                        print(
                            f"Placeholder {placeholder} not found in entry {entry.title}"
                        )
                formatted_text = format_string.format(**values)

                md_content = f"---\n{md_config}---\n{formatted_text}"

                self.repo.create_file(
                    path=file_path,
                    message=f"Add {entry.title} to feed",
                    content=md_content,
                    branch=branch_name,
                )

                feeds_processed.append(entry.title)

        base_sha = self.repo.get_branch("main").commit.sha
        compare_sha = self.repo.get_branch(branch_name).commit.sha
        comparison = self.repo.compare(base_sha, compare_sha).total_commits
        if comparison == 0:
            self.repo.get_git_ref(f"heads/{branch_name}").delete()
            print(f"No new feed found.\nRemoving branch {branch_name}")
            return

        try:
            title = (
                f"Update from feeds input bot since {start_date.strftime('%Y-%m-%d')}"
            )
            feeds_processed_str = "- " + "\n- ".join(feeds_processed)
            body = f"This PR created automatically by feed bot.\n\nFeeds processed:\n{feeds_processed_str}"
            self.repo.create_pull(
                title=title,
                body=body,
                base="main",
                head=branch_name,
            )
        except GithubException as e:
            self.repo.get_git_ref(f"heads/{branch_name}").delete()
            print(
                f"Error in creating PR: {e.data.get('errors')[0].get('message')}\nRemoving branch {branch_name}"
            )


if __name__ == "__main__":
    feed_bot_cls = feed_bot()
    feed_bot_cls.create_pr()
