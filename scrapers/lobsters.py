import asyncio
import os
import requests
import time
from bs4 import BeautifulSoup
from datetime import datetime
from sqlalchemy.sql import func

from hacker_news import models
from scrapers.base import BaseScraper

class LobstersScraper(BaseScraper):
    def scrape_loop(self):
        session = models.Session()
        new_feed = models.Feed(source='lobsters')
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
        print('Scrape completed for first two pages of Lobsters.')

    async def scrape_page(self, page, feed_id, loop):
        session = models.Session()
        print('Scrape initiated for page ' + str(page) + ' of Lobsters.')
        now = int(datetime.utcnow().strftime('%s'))
        feed_html = requests.get('https://lobste.rs/page/' + str(page))
        feed_soup = BeautifulSoup(feed_html.content, 'html.parser')
        
        post_rows = feed_soup.find_all('li', class_='story')
        post_comment_tasks = []

        for post_row in post_rows:
            post_uid = post_row.get('data-shortid')
            post_exists = session.query(models.Post.id).filter_by(uid=post_uid, source='lobsters').scalar()

            if not post_exists:
                link_tag = post_row.find('a', class_='u-url')
                link = link_tag.get('href') if link_tag else ''
                title = link_tag.get_text() if link_tag else ''
                
                type = 'article' # simplify type for lobsters
                
                user_tag = post_row.find('div', class_='byline').find('a', href=lambda x: x and x.startswith('/~'))
                username = user_tag.get_text() if user_tag else ''
                
                domain_tag = post_row.find('a', class_='domain')
                website = domain_tag.get_text() if domain_tag else ''
                
                time_tag = post_row.find('time')
                if time_tag and time_tag.get('datetime'):
                    created_dt = datetime.strptime(time_tag.get('datetime'), '%Y-%m-%d %H:%M:%S')
                    created = created_dt.strftime('%Y-%m-%d %H:%M')
                else:
                    created = time.strftime('%Y-%m-%d %H:%M', time.localtime(now))
                
                post = models.Post(created=created, uid=post_uid, source='lobsters',
                                   link=link, title=title, type=type, username=username, website=website)
                session.add(post)
                session.commit()
                post_id = post.id
            else:
                post_id = post_exists
            
            comments_tag = post_row.find('span', class_='comments_label')
            if comments_tag and comments_tag.find('a'):
                comment_text = comments_tag.find('a').get_text().strip()
                comment_count = int(comment_text.split()[0]) if comment_text.split()[0].isdigit() else 0
            else:
                comment_count = 0
                
            point_tag = post_row.find('a', class_='upvoter')
            point_count = int(point_tag.get_text()) if point_tag and point_tag.get_text().isdigit() else 0
            
            feed_post_exists = session.query(models.FeedPost.post_id).filter_by(
                post_id=post_id, feed_id=feed_id).scalar()

            if not feed_post_exists:
                feed_post = models.FeedPost(comment_count=comment_count, feed_id=feed_id,
                                            feed_rank=0, point_count=point_count, post_id=post_id)
                session.add(feed_post)
                from sqlalchemy.exc import IntegrityError
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()

                post_comment_tasks.append(
                    loop.create_task(self.scrape_post(post_uid, feed_id, loop, None)))

        if post_comment_tasks:
            await asyncio.wait(post_comment_tasks)
        session.close()

    async def scrape_post(self, post_uid, feed_id, loop, page_number):
        # simplified for demonstration, just fetch the post to get the structure
        session = models.Session()
        post_id = session.query(models.Post.id).filter_by(uid=post_uid, source='lobsters').scalar()
        if not post_id:
            session.close()
            return
            
        post_html = requests.get('https://lobste.rs/s/' + post_uid)
        post_soup = BeautifulSoup(post_html.content, 'html.parser')
        
        # Lobsters doesn't paginate comments usually, so we parse all `li.comments_subtree`
        comment_rows = post_soup.find_all('div', class_='comment')
        
        comment_feed_rank = 0
        for comment_row in comment_rows:
            comment_uid = comment_row.get('data-shortid')
            if not comment_uid:
                continue
                
            comment_exists = session.query(models.Comment.id).filter_by(uid=comment_uid).scalar()
            if not comment_exists:
                text_div = comment_row.find('div', class_='comment_text')
                comment_content = text_div.get_text().strip() if text_div else ''
                total_word_count = len(comment_content.split())
                
                user_tag = comment_row.find('div', class_='byline').find('a', href=lambda x: x and x.startswith('/~'))
                comment_username = user_tag.get_text() if user_tag else ''
                
                time_tag = comment_row.find('time')
                if time_tag and time_tag.get('datetime'):
                    created_dt = datetime.strptime(time_tag.get('datetime'), '%Y-%m-%d %H:%M:%S')
                    comment_created = created_dt.strftime('%Y-%m-%d %H:%M')
                else:
                    comment_created = time.strftime('%Y-%m-%d %H:%M', time.localtime(int(datetime.utcnow().strftime('%s'))))
                
                comment = models.Comment(content=comment_content, created=comment_created,
                    uid=comment_uid, level=0, parent_comment=None, # Simplifying level/parent for lobsters
                    post_id=post_id, total_word_count=total_word_count, username=comment_username,
                    word_counts=func.to_tsvector('simple_english', comment_content.lower()))
                session.add(comment)
                session.commit()
                comment_id = comment.id
            else:
                comment_id = comment_exists
                
            comment_feed_rank += 1
            feed_comment_exists = session.query(models.FeedComment.comment_id).filter_by(comment_id=comment_id, feed_id=feed_id).scalar()
            if not feed_comment_exists:
                feed_comment = models.FeedComment(comment_id=comment_id, feed_id=feed_id, feed_rank=comment_feed_rank)
                session.add(feed_comment)
                
        session.commit()
        session.close()

def scrape_lobsters_loop():
    scraper = LobstersScraper()
    scraper.scrape_loop()
