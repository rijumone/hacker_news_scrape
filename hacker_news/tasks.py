import os
import requests
import time
import re
from datetime import datetime
from sqlalchemy.sql import func
from sqlalchemy.exc import IntegrityError

from hacker_news import models
from hacker_news.celery_app import app

@app.task
def process_post_task(feed_id, post_data):
    session = models.Session()
    try:
        uid = post_data['uid']
        link = post_data['link']
        title = post_data['title']
        username = post_data['username']
        content = post_data['content']
        created = post_data['created']
        feed_rank = post_data['feed_rank']
        
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
            
            # Spawn secondary task
            process_comments_task.delay(feed_id, post_id, uid, link)
    finally:
        session.close()

@app.task
def process_comments_task(feed_id, post_id, post_uid, link):
    session = models.Session()
    try:
        url = f'{os.getenv("JINA_URL", "http://192.168.1.17:4337")}/{link}'
        headers = {
            'X-Wait-For-Selector': 'shreddit-comment',
            'X-Timeout': '29',
            'X-With-Shadow-Dom': 'true'
        }
        
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            return
            
        post_md = response.text
        now = int(datetime.utcnow().strftime('%s'))
        
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
    finally:
        session.close()
