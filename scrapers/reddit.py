import asyncio
import os
import requests
import time
import re
from datetime import datetime
from sqlalchemy.sql import func
from sqlalchemy.exc import IntegrityError

from hacker_news import models
from scrapers.base import BaseScraper

class RedditScraper(BaseScraper):
    def scrape_loop(self):
        # Read subreddits.yml
        subreddits_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'subreddits.yml')
        if not os.path.exists(subreddits_file):
            print(f"File {subreddits_file} not found.")
            return

        subreddits = []
        with open(subreddits_file, 'r') as f:
            for line in f:
                line = line.strip()
                if line.startswith('- '):
                    subreddits.append(line[2:].strip())

        session = models.Session()
        new_feed = models.Feed(source='reddit')
        session.add(new_feed)
        session.commit()
        feed_id = new_feed.id

        loop = asyncio.get_event_loop()
        tasks = []
        for sub in subreddits:
            tasks.append(loop.create_task(self.scrape_page(sub, feed_id, loop)))

        if tasks:
            wait_tasks = asyncio.wait(tasks)
            loop.run_until_complete(wait_tasks)

        loop.close()
        session.close()
        print('Scrape completed for Reddit.')

    async def scrape_page(self, subreddit, feed_id, loop):
        session = models.Session()
        print(f'Scrape initiated for subreddit {subreddit}')
        now = int(datetime.utcnow().strftime('%s'))
        
        url = f'http://192.168.1.12:4337/https://www.reddit.com/r/{subreddit}/'
        response = requests.get(url)
        feed_md = response.text
        
        title_regex = re.compile(r"^\[(.*?)\]\((https://www\.reddit\.com/r/[^/]+/comments/([a-z0-9]+)/[^/]+/?)\)", re.MULTILINE)
        matches = list(title_regex.finditer(feed_md))
        
        post_comment_tasks = []
        feed_rank = 1
        
        for i in range(len(matches)):
            title = matches[i].group(1)
            link = matches[i].group(2)
            uid = matches[i].group(3)
            
            start_pos = matches[i].end()
            end_pos = matches[i+1].start() if i + 1 < len(matches) else len(feed_md)
            chunk = feed_md[start_pos:end_pos]
            
            author_match = re.search(r"u/([A-Za-z0-9_-]+)\]\(https://www\.reddit\.com/user/", chunk)
            time_match = re.search(r"•(.*?(?:ago|min\.|hr\.|days?|years?))", chunk)
            
            username = author_match.group(1) if author_match else "unknown"
            created = time.strftime('%Y-%m-%d %H:%M', time.localtime(now)) # Simplify datetime parsing for now
            
            content = ""
            post_body_raw = chunk.split("* * *")[0]
            content_match = re.search(r"•.*?\[.*?\]\(.*?\)(?:\[(.*)\])?", post_body_raw, re.DOTALL)
            
            if content_match and content_match.group(1):
                content = content_match.group(1).strip()
            else:
                # Fallback
                lines = post_body_raw.split('\n')
                found_time = False
                content_lines = []
                for line in lines:
                    if found_time:
                        content_lines.append(line)
                    elif line.startswith('•'):
                        found_time = True
                content = '\n'.join(content_lines).strip()
                if content.startswith('['):
                    closing_idx = content.find(']')
                    if closing_idx != -1:
                        paren_closing_idx = content.find(')', closing_idx)
                        if paren_closing_idx != -1:
                            content = content[paren_closing_idx+1:].strip()
                            if content.startswith('['):
                                content = content[1:]
                            if content.endswith(']'):
                                content = content[:-1]

            post_exists = session.query(models.Post.id).filter_by(uid=uid, source='reddit').scalar()
            
            if not post_exists:
                post = models.Post(created=created, uid=uid, source='reddit',
                                   link=link, title=title, type='article', username=username, website='reddit.com', content=content)
                session.add(post)
                session.commit()
                post_id = post.id
            else:
                post_id = post_exists
                
            feed_post_exists = session.query(models.FeedPost.post_id).filter_by(
                post_id=post_id, feed_id=feed_id).scalar()

            if not feed_post_exists:
                feed_post = models.FeedPost(comment_count=0, feed_id=feed_id,
                                            feed_rank=feed_rank, point_count=0, post_id=post_id)
                session.add(feed_post)
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()

                post_comment_tasks.append(
                    loop.create_task(self.scrape_post(uid, link, feed_id, loop)))
                    
            feed_rank += 1

        if post_comment_tasks:
            await asyncio.wait(post_comment_tasks)
        session.close()

    async def scrape_post(self, post_uid, link, feed_id, loop):
        session = models.Session()
        post_id = session.query(models.Post.id).filter_by(uid=post_uid, source='reddit').scalar()
        if not post_id:
            session.close()
            return
            
        url = f'http://192.168.1.12:4337/{link}'
        headers = {
            'X-Wait-For-Selector': 'shreddit-comment',
            'X-Timeout': '29'
        }
        response = requests.get(url, headers=headers)
        post_md = response.text
        
        post = session.query(models.Post).filter_by(id=post_id).first()
        if post and (not post.content or len(post.content) < 10):
            title_idx = post_md.find(f"# {post.title}")
            if title_idx == -1:
                title_idx = post_md.find("\n# ")
            if title_idx != -1:
                body_start = post_md.find("\n", title_idx)
                if body_start != -1:
                    end_idx = post_md.find("\n Share", body_start)
                    if end_idx == -1:
                        end_idx = post_md.find("\n Sort by:", body_start)
                    if end_idx != -1:
                        extracted_content = post_md[body_start:end_idx].strip()
                        if extracted_content.endswith("Read more"):
                            extracted_content = extracted_content[:-9].strip()
                        if extracted_content:
                            post.content = extracted_content
                            session.commit()
        
        comment_regex = re.compile(r"^\[([A-Za-z0-9_-]+)\]\(https://www\.reddit\.com/user/\1/?\)\s*^•\[(.*?)\]\((https://www\.reddit\.com/r/[^/]+/comments/[^/]+/comment/([a-z0-9]+)/?)\)", re.MULTILINE)
        c_matches = list(comment_regex.finditer(post_md))
        
        comment_feed_rank = 1
        now = int(datetime.utcnow().strftime('%s'))
        
        for i in range(len(c_matches)):
            author = c_matches[i].group(1)
            uid = c_matches[i].group(4)
            
            start_pos = c_matches[i].end()
            end_pos = c_matches[i+1].start() if i + 1 < len(c_matches) else len(post_md)
            chunk = post_md[start_pos:end_pos].strip()
            
            chunk = re.sub(r"\[More replies\].*", "", chunk, flags=re.DOTALL).strip()
            cut_idx = chunk.find("[![Image")
            if cut_idx != -1:
                chunk = chunk[:cut_idx].strip()
                
            comment_content = chunk
            total_word_count = len(comment_content.split())
            
            comment_exists = session.query(models.Comment.id).filter_by(uid=uid).scalar()
            if not comment_exists:
                comment_created = time.strftime('%Y-%m-%d %H:%M', time.localtime(now))
                
                comment = models.Comment(content=comment_content, created=comment_created,
                    uid=uid, level=0, parent_comment=None,
                    post_id=post_id, total_word_count=total_word_count, username=author,
                    word_counts=func.to_tsvector('simple_english', comment_content.lower()))
                session.add(comment)
                session.commit()
                comment_id = comment.id
            else:
                comment_id = comment_exists
                
            feed_comment_exists = session.query(models.FeedComment.comment_id).filter_by(comment_id=comment_id, feed_id=feed_id).scalar()
            if not feed_comment_exists:
                feed_comment = models.FeedComment(comment_id=comment_id, feed_id=feed_id, feed_rank=comment_feed_rank)
                session.add(feed_comment)
            comment_feed_rank += 1
            
        session.commit()
        session.close()

def scrape_reddit_loop():
    scraper = RedditScraper()
    scraper.scrape_loop()
