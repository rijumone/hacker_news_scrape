import asyncio
import os
import requests
import time
from bs4 import BeautifulSoup
from datetime import datetime
from sqlalchemy.sql import func

from hacker_news import models
from scrapers.base import BaseScraper

class HighScalabilityScraper(BaseScraper):
    def scrape_loop(self):
        session = models.Session()
        new_feed = models.Feed(source='high_scalability')
        session.add(new_feed)
        session.commit()
        feed_id = new_feed.id

        loop = asyncio.get_event_loop()
        tasks = [
            loop.create_task(self.scrape_page(1, feed_id, loop)),
            loop.create_task(self.scrape_page(2, feed_id, loop)),
            loop.create_task(self.scrape_page(3, feed_id, loop)),
        ]

        wait_tasks = asyncio.wait(tasks)
        loop.run_until_complete(wait_tasks)
        loop.close()
        session.close()
        print('Scrape completed for High Scalability.')

    async def scrape_page(self, page, feed_id, loop):
        session = models.Session()
        print('Scrape initiated for page ' + str(page) + ' of High Scalability.')
        now = int(datetime.utcnow().strftime('%s'))
        
        url = 'https://highscalability.com/' if page == 1 else f'https://highscalability.com/page/{page}/'
        feed_html = requests.get(url)
        feed_soup = BeautifulSoup(feed_html.content, 'html.parser')
        
        # Ghost blog post card structure
        post_rows = feed_soup.find_all('article', class_='gh-card')
        post_comment_tasks = []

        feed_rank = 0
        for post_row in post_rows:
            link_tag = post_row.find('a', class_='gh-card-link')
            if not link_tag:
                continue
                
            link = link_tag.get('href') if link_tag else ''
            if link and link.startswith('/'):
                link = 'https://highscalability.com' + link
                
            post_uid = link.strip('/')
            post_exists = session.query(models.Post.id).filter_by(uid=post_uid, source='high_scalability').scalar()

            title_tag = link_tag.find('h2', class_='gh-card-title') or link_tag.find('h3', class_='gh-card-title')
            title = title_tag.get_text().strip() if title_tag else link.split('/')[-2].replace('-', ' ').title()

            if not post_exists:
                type = 'article' 
                
                user_tag = post_row.find('span', class_='gh-card-author')
                username = user_tag.get_text().strip() if user_tag else 'High Scalability'
                website = 'highscalability.com'
                
                time_tag = post_row.find('time')
                if time_tag and time_tag.get('datetime'):
                    # e.g., 2026-06-23
                    created_dt = datetime.strptime(time_tag.get('datetime'), '%Y-%m-%d')
                    created = created_dt.strftime('%Y-%m-%d 00:00')
                else:
                    created = time.strftime('%Y-%m-%d %H:%M', time.localtime(now))
                
                post = models.Post(created=created, uid=post_uid, source='high_scalability',
                                   link=link, title=title, type=type, username=username, website=website)
                session.add(post)
                session.commit()
                post_id = post.id
            else:
                post_id = post_exists
            
            comment_count = 0
            point_count = 0
            
            feed_post_exists = session.query(models.FeedPost.post_id).filter_by(
                post_id=post_id, feed_id=feed_id).scalar()

            if not feed_post_exists:
                feed_rank += 1
                feed_post = models.FeedPost(comment_count=comment_count, feed_id=feed_id,
                                            feed_rank=feed_rank, point_count=point_count, post_id=post_id)
                session.add(feed_post)
                
                from sqlalchemy.exc import IntegrityError
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()

            # Optional: scrape comments. Not fully implementing Ghost comment API here to keep it simple.
            # post_comment_tasks.append(loop.create_task(self.scrape_post(post_uid, feed_id, loop, None)))

        session.close()

    async def scrape_post(self, post_uid, feed_id, loop, page_number):
        # High Scalability comments implementation omitted for brevity / complexity
        pass

def scrape_high_scalability_loop():
    scraper = HighScalabilityScraper()
    scraper.scrape_loop()
