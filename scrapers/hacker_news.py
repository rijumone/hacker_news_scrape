import asyncio
import os
import requests
import time
from bs4 import BeautifulSoup, UnicodeDammit
from datetime import datetime
from sqlalchemy.sql import func

from hacker_news import models
from scrapers.base import BaseScraper

class HackerNewsScraper(BaseScraper):
    def scrape_loop(self):
        # Connect to database
        session = models.Session()

        # Add feed to database
        new_feed = models.Feed(source='hacker_news')
        session.add(new_feed)
        session.commit()
        feed_id = new_feed.id

        # Create asynchronous tasks to scrape first three pages of Hacker News
        loop = asyncio.get_event_loop()
        tasks = [
            loop.create_task(self.scrape_page(1, feed_id, loop)),
            loop.create_task(self.scrape_page(2, feed_id, loop)),
            loop.create_task(self.scrape_page(3, feed_id, loop))
        ]

        wait_tasks = asyncio.wait(tasks)
        loop.run_until_complete(wait_tasks)
        loop.close()
        session.close()
        print('Scrape completed for first three pages of Hacker News.')

    async def scrape_page(self, page, feed_id, loop):
        session = models.Session()
        print('Scrape initiated for page ' + str(page) + ' of Hacker News.')
        now = int(datetime.utcnow().strftime('%s'))
        feed_html = requests.get('https://news.ycombinator.com/news?p=' + str(page))
        feed_soup = BeautifulSoup(feed_html.content, 'html.parser')
        post_rows = feed_soup.find_all('tr', 'athing')
        post_comment_tasks = []

        for post_row in post_rows:
            subtext_row = post_row.next_sibling
            post_id = int(post_row.get('id'))
            post_uid = str(post_id)

            post_exists = session.query(models.Post.id).filter_by(id=post_id).scalar()

            if not post_exists:
                time_unit = subtext_row.find('span', 'age').a.get_text().split()[1]
                if 'day' in time_unit:
                    created = now - 86400 * int(subtext_row.find('span', 'age').a.get_text().split()[0])
                elif 'hour' in time_unit:
                    created = now - 3600 * int(subtext_row.find('span', 'age').a.get_text().split()[0])
                else:
                    created = now - 60 * int(subtext_row.find('span', 'age').a.get_text().split()[0])

                created = time.strftime('%Y-%m-%d %H:%M', time.localtime(created))

                link_span = post_row.find('span', 'titleline').find('a')
                link = link_span.get('href')
                title = link_span.get_text()

                if 'Show HN:' in title:
                    type = 'show'
                elif 'Ask HN:' in title:
                    type = 'ask'
                else:
                    type = 'article'

                if subtext_row.find('a', 'hnuser'):
                    username = subtext_row.find('a', 'hnuser').get_text()
                else:
                    username = ''

                if post_row.find('span', 'sitestr'):
                    website = post_row.find('span', 'sitestr').get_text()
                else:
                    website = ''

                post = models.Post(created=created, id=post_id, uid=post_uid, source='hacker_news',
                                   link=link, title=title, type=type, username=username, website=website)
                session.add(post)

            if 'comment' in subtext_row.find_all(href='item?id=' + str(post_id))[-1].get_text():
                unicode_count = UnicodeDammit(subtext_row.find_all(href='item?id=' + str(post_id))[-1].get_text())
                comment_count = unicode_count.unicode_markup.split()[0]
            else:
                comment_count = 0

            feed_rank = post_row.find('span', 'rank').get_text()[:-1]

            if subtext_row.find('span', 'score'):
                point_count = subtext_row.find('span', 'score').get_text().split()[0]
            else:
                point_count = 0
                type = 'job'

            feed_post_exists = session.query(models.FeedPost.post_id).filter_by(
                post_id=post_id, feed_id=feed_id).scalar()

            if not feed_post_exists:
                feed_post = models.FeedPost(comment_count=comment_count,
                    feed_id=feed_id, feed_rank=feed_rank, point_count=point_count,
                    post_id=post_id)

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
        session = models.Session()
        now = int(datetime.utcnow().strftime('%s'))
        post_id = int(post_uid)

        if page_number:
            post_html = requests.get('https://news.ycombinator.com/item?id=' + str(post_id) + '&p=' + str(page_number))
        else:
            post_html = requests.get('https://news.ycombinator.com/item?id=' + str(post_id))

        post_soup = BeautifulSoup(post_html.content, 'html.parser')
        next_page_number = None

        if post_soup.find('a', 'morelink'):
            next_page_number = post_soup.find('a', 'morelink').get('href').split('&p=')[1]

        comment_rows = post_soup.select('tr.athing.comtr')
        comment_feed_rank = 0

        for comment_row in comment_rows:
            comment_id = int(comment_row.get('id'))
            comment_uid = str(comment_id)
            comment_exists = session.query(models.Comment.id).filter_by(id=comment_id).scalar()

            if not comment_exists:
                if comment_row.find('div', 'comment').find_all('span'):
                    comment_content = comment_row.find('div', 'comment').find_all('span')[0].get_text()
                    comment_content = comment_content.rsplit(' ', 1)[0].strip()
                    total_word_count = len(comment_content.split())
                else:
                    comment_content = comment_row.find('div', 'comment').get_text().strip()
                    total_word_count = 0

                comment_time_unit = comment_row.find('span', 'age').a.get_text().split()[1]
                if 'day' in comment_time_unit:
                    comment_created = now - 86400 * int(comment_row.find('span', 'age').a.get_text().split()[0])
                elif 'hour' in comment_time_unit:
                    comment_created = now - 3600 * int(comment_row.find('span', 'age').a.get_text().split()[0])
                else:
                    comment_created = now - 60 * int(comment_row.find('span', 'age').a.get_text().split()[0])

                comment_created = time.strftime('%Y-%m-%d %H:%M', time.localtime(comment_created))
                level = int(comment_row.find('td', 'ind').contents[0].get('width')) / 40

                if level == 0:
                    parent_comment = None
                else:
                    parent_comment = session.query(models.Comment).with_entities(
                        models.Comment.id).join(models.FeedComment).filter(
                        models.Comment.level == (level - 1)).filter(
                        models.FeedComment.feed_id == feed_id).filter(
                        models.Comment.post_id == post_id).order_by(
                        models.FeedComment.feed_rank).limit(1).one()[0]

                try:
                    comment_username = comment_row.find('a', 'hnuser').get_text()
                except AttributeError:
                    comment_username = ''

                comment = models.Comment(content=comment_content, created=comment_created,
                    id=comment_id, uid=comment_uid, level=level, parent_comment=parent_comment,
                    post_id=post_id, total_word_count=total_word_count, username=comment_username,
                    word_counts=func.to_tsvector('simple_english', comment_content.lower()))
                session.add(comment)

            comment_feed_rank += 1
            feed_comment_exists = session.query(models.FeedComment.comment_id).filter_by(comment_id=comment_id, feed_id=feed_id).scalar()

            if not feed_comment_exists:
                feed_comment = models.FeedComment(comment_id=comment_id, feed_id=feed_id, feed_rank=comment_feed_rank)
                session.add(feed_comment)

        session.commit()
        session.close()

        if next_page_number:
            await self.scrape_post(post_uid, feed_id, loop, next_page_number)
        else:
            print('Post ' + str(post_id) + ' and its comments scraped')

def scrape_loop():
    scraper = HackerNewsScraper()
    scraper.scrape_loop()
