import asyncio
import json
import os
import requests
import time

from bs4 import BeautifulSoup, UnicodeDammit
from datetime import date, datetime, timedelta
from flask import jsonify, make_response, request
from sqlalchemy import desc, inspect
from sqlalchemy.orm.exc import NoResultFound
from sqlalchemy.sql import func

from hacker_news import models




def get_post_comments(session, post_id):
    # Get latest feed-based data for each comment associated with post
    latest_comment_feed = session.query(
        models.FeedComment.comment_id.label('comment_id'),
        func.max(models.FeedComment.feed_id).label(
            'latest_feed_id')).group_by(
        models.FeedComment.comment_id).subquery()

    return session.query(models.Comment).with_entities(
        models.Comment.id, models.Comment.content, models.Comment.created, models.Comment.uid,
        models.Comment.level, models.Comment.parent_comment,
        models.Comment.post_id, models.Comment.username,
        models.FeedComment.feed_rank).join(models.FeedComment).join(
        latest_comment_feed, (models.FeedComment.comment_id ==
        latest_comment_feed.c.comment_id) & (models.FeedComment.feed_id ==
        latest_comment_feed.c.latest_feed_id)).filter(
        models.Comment.post_id == post_id).order_by(
        models.Comment.created, models.Comment.uid.asc()).all()


def get_comment(comment_id):
    # Connect to database
    session = models.Session()

    # Get comment from database
    try:
        comment = session.query(models.Comment).with_entities(
            models.Comment.content, models.Comment.created, models.Comment.uid,
            models.Comment.level, models.Comment.parent_comment,
            models.Comment.post_id, models.Comment.username,
            models.FeedComment.feed_rank).join(models.FeedComment).filter(
            models.Comment.id == comment_id).order_by(
            models.FeedComment.feed_id.desc()).limit(1).one()

        session.close()

        return jsonify(comment._asdict())

    # Return error if comment not returned from query
    except NoResultFound:
        session.close()

        return make_response('Comment not found', 404)


def get_post(post_id, source='all'):
    # Connect to database
    session = models.Session()

    # Get post from database
    try:
        query = session.query(models.Post).with_entities(models.Post.created,
            models.Post.uid, models.Post.source, models.Post.link, models.Post.title, models.Post.type,
            models.Post.username, models.Post.website,
            models.FeedPost.comment_count, models.FeedPost.feed_rank,
            models.FeedPost.point_count,
            models.FeedPost.feed_id.label('feed_id')).join(
            models.FeedPost)
            
        if str(post_id).isdigit():
            query = query.filter(models.Post.id == int(post_id))
        else:
            query = query.filter(models.Post.uid == post_id)
            
        if source != 'all':
            query = query.filter(models.Post.source == source)
            
        post = query.order_by(models.FeedPost.feed_id.desc()).limit(1).one()

        comments = get_post_comments(session, post.id if str(post_id).isdigit() else post_id) # Simplify comment backfilling for non-HN sources for now

        post_data = post._asdict()
        feed_id = post_data.pop('feed_id')

        if not comments and post_data['comment_count'] > 0 and post_data['source'] == 'hacker_news':
            session.close()

            loop = asyncio.new_event_loop()

            try:
                from scrapers.hacker_news import HackerNewsScraper
                scraper = HackerNewsScraper()
                asyncio.set_event_loop(loop)

                loop.run_until_complete(scraper.scrape_post(str(post_id), feed_id, loop,
                    None))

            except Exception as error:
                print('Failed to backfill comments for post ' + str(post_id) +
                    ': ' + str(error))

            finally:
                loop.close()
                asyncio.set_event_loop(None)

            session = models.Session()
            comments = get_post_comments(session, post_id)

        post_data['comments'] = [comment._asdict() for comment in comments]

        session.close()

        return jsonify(post_data)

    # Return error if post not returned from query
    except NoResultFound:
        session.close()

        return make_response('Post not found', 404)


def get_posts(source='all'):
    # Connect to database
    session = models.Session()

    base_feed_query = session.query(models.FeedPost.post_id.label('post_id'),
        func.max(models.FeedPost.feed_id).label('latest_feed_id'))
        
    if source != 'all':
        base_feed_query = base_feed_query.join(models.Feed).filter(models.Feed.source == source)

    # Get latest feed-based data for each post from database
    latest_post_feed = base_feed_query.group_by(models.FeedPost.post_id).subquery()

    post_query = session.query(models.Post).with_entities(models.Post.id,
        models.Post.uid, models.Post.source, models.Post.created, models.Post.uid, models.Post.source, models.Post.link, models.Post.title,
        models.Post.type, models.Post.username, models.Post.website,
        models.FeedPost.comment_count, models.FeedPost.feed_rank,
        models.FeedPost.point_count).join(models.FeedPost).join(
        latest_post_feed, (models.FeedPost.post_id ==
        latest_post_feed.c.post_id) & (models.FeedPost.feed_id ==
        latest_post_feed.c.latest_feed_id))
        
    if source != 'all':
        post_query = post_query.filter(models.Post.source == source)

    posts = post_query.order_by(models.Post.id.asc()).all()

    session.close()

    return jsonify([post._asdict() for post in posts])


def get_feeds(time_period, source='all'):
    # Return time period if there is no database connection
    if not os.environ['DB_CONNECTION']:
        return time_period

    # Connect to database
    session = models.Session()
    
    base_query = session.query(models.Feed)
    if source != 'all':
        base_query = base_query.filter(models.Feed.source == source)

    # Get requested feed(s) from database based on passed time value
    if time_period == 'day':
        feed_ids = [row.id for row in base_query.filter(
            models.Feed.created > date.today()).all()]

    # Get one feed per day in past week if 'week' is specified
    elif time_period == 'week':
        feed_ids = []

        for i in range(7):
            try:
                feed_ids.append(base_query.filter(
                    models.Feed.created > date.today() - timedelta(days=i)).limit(
                    1).one()[0])
            except NoResultFound:
                continue

    # Return no feed_ids if 'all' is specified so all data can be queried
    elif time_period == 'all':
        feed_ids = None

    # Return most recent feed_id if time_period is 'hour' or unspecified
    else:
        feed_ids = [row.id for row in base_query.order_by(
            models.Feed.created.desc()).limit(1)]

    return feed_ids


def get_average_comment_count(feed_ids):
    # Connect to database
    session = models.Session()

    # Get average comment count, filtering by feed_ids if specified
    if feed_ids:
        average = round(session.query(
            func.avg(models.FeedPost.comment_count)).filter(
            models.FeedPost.feed_id.in_(feed_ids)).one()[0])

    else:
        average = round(session.query(
            func.avg(models.FeedPost.comment_count)).one()[0])

    session.close()

    return jsonify(average)


def get_average_comment_tree_depth(feed_ids):
    # Connect to database
    session = models.Session()

    # Get average comment level, filtering by feed_ids if specified
    if feed_ids:
        average = round(session.query(func.avg(models.Comment.level)).join(
            models.FeedComment).filter(
            models.FeedComment.feed_id.in_(feed_ids)).one()[0])

    else:
        average = round(session.query(func.avg(models.Comment.level)).one()[0])

    session.close()

    return jsonify(average)


def get_average_comment_word_count(feed_ids):
    # Connect to database
    session = models.Session()

    # Get average comment word count, filtering by feed_ids if specified
    if feed_ids:
        average = round(session.query(func.avg(
            models.Comment.total_word_count)).join(models.FeedComment).filter(
            models.FeedComment.feed_id.in_(feed_ids)).one()[0])

    else:
        average = round(session.query(func.avg(
            models.Comment.total_word_count)).one()[0])

    session.close()

    return jsonify(average)


def get_average_point_count(feed_ids):
    # Connect to database
    session = models.Session()

    # Get average post point count, filtering by feed_ids if specified
    if feed_ids:
        average = round(session.query(func.avg(
            models.FeedPost.point_count)).filter(
            models.FeedPost.feed_id.in_(feed_ids)).one()[0])

    else:
        average = round(session.query(func.avg(
            models.FeedPost.point_count)).one()[0])

    session.close()

    return jsonify(average)


def get_comments_with_highest_word_counts(feed_ids):
    # Get number of requested comments from query parameter, using default if
    # null
    count = int(request.args.get('count', 1))

    # Connect to database
    session = models.Session()

    # Get comments with highest word counts, filtering by feed_ids if specified
    if feed_ids:
        subquery = session.query(models.Comment).with_entities(
            models.Comment.content, models.Comment.created, models.Comment.uid, models.Comment.id,
            models.Comment.level, models.Comment.parent_comment,
            models.Comment.post_id, models.Comment.username,
            models.Comment.total_word_count).join(models.FeedComment).filter(
            models.FeedComment.feed_id.in_(feed_ids)).order_by(
            models.Comment.id,
            models.Comment.total_word_count.desc()).distinct(
            models.Comment.id).subquery()

        query = session.query(subquery).order_by(
            subquery.columns.get('total_word_count').desc()).limit(count)

    else:
        query = session.query(models.Comment).with_entities(
            models.Comment.content, models.Comment.created, models.Comment.uid, models.Comment.id,
            models.Comment.level, models.Comment.parent_comment,
            models.Comment.post_id, models.Comment.username,
            models.Comment.total_word_count).order_by(
            models.Comment.total_word_count.desc()).limit(count)

    session.close()

    comments = []

    for row in query:
        comments.append(row._asdict())

    return jsonify(comments)


def get_most_frequent_comment_words(feed_ids):
    # Return sample data if there is no database connection
    if not os.environ['DB_CONNECTION']:
        with open(os.path.dirname(os.path.dirname(os.path.abspath(__file__))) +
            '/sample_data/' + feed_ids + '_comment_words.json',
            'r') as sample_data:
                return jsonify(json.load(sample_data))

    # Get number of requested words from query parameter, using default if
    # null
    count = int(request.args.get('count', 1))

    # Connect to database
    session = models.Session()

    # Get highest frequency words used in comments, excluding stop words,
    # filtering by feed_ids if specified
    if feed_ids:
        query = session.execute(
            """
              SELECT *
                FROM ts_stat(
                     $$SELECT word_counts
                         FROM (
                                 SELECT DISTINCT ON (id)
                                        id, feed_id, word_counts
                                   FROM comment
                                        JOIN feed_comment
                                          ON feed_comment.comment_id =
                                             comment.id
                                  WHERE feed_id = ANY(:feed_id)
                               ORDER BY id, word_counts DESC
                         ) comment_table$$
                )
               WHERE LENGTH (word) > 1
            ORDER BY nentry DESC
               LIMIT :count;
            """,
            {'feed_id': feed_ids,
            'count': count}
            ).fetchall()

    else:
        query = session.execute(
            """
              SELECT *
                FROM ts_stat(
                     $$SELECT word_counts
                         FROM comment$$
                )
               WHERE LENGTH (word) > 1
            ORDER BY nentry DESC
               LIMIT :count;
            """,
            {'feed_id': feed_ids,
            'count': count}
            ).fetchall()

    session.close()

    words = []

    for row in query:
        words.append(dict(row))

    return jsonify(words)


def get_deepest_comment_tree(feed_ids):
    # Connect to database
    session = models.Session()

    # Get highest level comment (deepest in comment tree), filtering by
    # feed_ids if specified
    if feed_ids:
        subquery = session.query(models.Comment).with_entities(
            models.Comment.content, models.Comment.created, models.Comment.uid, models.Comment.id,
            models.Comment.level, models.Comment.parent_comment,
            models.Comment.post_id, models.Comment.username).join(
            models.FeedComment).filter(
            models.FeedComment.feed_id.in_(feed_ids)).order_by(
            models.Comment.id, models.Comment.level.desc()).distinct(
            models.Comment.id).subquery()

        comment = session.query(subquery).order_by(
            subquery.columns.get('level').desc()).limit(1).one()._asdict()

        # Get post information
        post = session.query(models.Post).with_entities(models.Post.created, models.Post.uid, models.Post.source,
            models.Post.id, models.Post.link, models.Post.title,
            models.Post.type, models.Post.username,
            models.FeedPost.comment_count, models.FeedPost.feed_rank,
            models.FeedPost.point_count).join(models.FeedPost).filter(
            models.Post.id == comment['post_id']).filter(
            models.FeedPost.feed_id.in_(feed_ids)).order_by(
            models.FeedPost.post_id.desc()).limit(1).one()._asdict()

    else:
        comment = session.query(models.Comment).with_entities(
            models.Comment.content, models.Comment.created, models.Comment.uid, models.Comment.id,
            models.Comment.level, models.Comment.parent_comment,
            models.Comment.post_id, models.Comment.username).order_by(
            models.Comment.level.desc()).limit(1).one()._asdict()

        # Get post information
        post = session.query(models.Post).with_entities(models.Post.created, models.Post.uid, models.Post.source,
            models.Post.id, models.Post.link, models.Post.title,
            models.Post.type, models.Post.username,
            models.FeedPost.comment_count, models.FeedPost.feed_rank,
            models.FeedPost.point_count).join(models.FeedPost).filter(
            models.Post.id == comment['post_id']).order_by(
            models.FeedPost.feed_id.desc()).limit(1).one()._asdict()

    comment.pop('post_id')
    comment.pop('level')

    # Get parent comments of comment to get full comment tree
    while comment['parent_comment']:
        parent_comment = session.query(models.Comment).with_entities(
            models.Comment.content, models.Comment.created, models.Comment.uid,
            models.Comment.id, models.Comment.parent_comment,
            models.Comment.username).filter(
            models.Comment.id == comment['parent_comment']).one()._asdict()

        comment.pop('parent_comment')

        # Set comment as child of parent
        parent_comment['child_comment'] = comment

        # Set next comment as current parent comment
        comment = parent_comment

    comment.pop('parent_comment')

    post['comment_tree'] = comment

    session.close()

    return jsonify(post)


def get_posts_with_highest_comment_counts(feed_ids):
    # Return sample data if there is no database connection
    if not os.environ['DB_CONNECTION']:
        with open(os.path.dirname(os.path.dirname(os.path.abspath(__file__))) +
            '/sample_data/' + feed_ids + '_posts_highest_comment_count.json',
            'r') as sample_data:
                return jsonify(json.load(sample_data))

    # Get number of requested posts from query parameter, using default if
    # null
    count = int(request.args.get('count', 1))

    # Connect to database
    session = models.Session()

    # Get posts with highest comment counts, filtering by feed_ids if specified
    if feed_ids:
        subquery = session.query(models.Post).with_entities(
            models.Post.created, models.Post.uid, models.Post.source, models.Post.id, models.Post.link,
            models.Post.title, models.Post.type, models.Post.username,
            models.Post.website, models.FeedPost.comment_count,
            models.FeedPost.feed_rank, models.FeedPost.point_count).join(
            models.FeedPost).filter(
            models.FeedPost.feed_id.in_(feed_ids)).order_by(
            models.Post.id, models.FeedPost.comment_count.desc()).distinct(
            models.Post.id).subquery()

        query = session.query(subquery).order_by(
            subquery.columns.get('comment_count').desc()).limit(count)

    else:
        subquery = session.query(models.Post).with_entities(
            models.Post.created, models.Post.uid, models.Post.source, models.Post.id, models.Post.link,
            models.Post.title, models.Post.type, models.Post.username,
            models.Post.website, models.FeedPost.comment_count,
            models.FeedPost.feed_rank, models.FeedPost.point_count).join(
            models.FeedPost).order_by(
            models.Post.id, models.FeedPost.comment_count.desc()).distinct(
            models.Post.id).subquery()

        query = session.query(subquery).order_by(
            subquery.columns.get('comment_count').desc()).limit(count)

    session.close()

    posts = []

    for row in query:
        posts.append(row._asdict())

    return jsonify(posts)


def get_posts_with_highest_point_counts(feed_ids):
    # Get number of requested posts from query parameter, using default if
    # null
    count = int(request.args.get('count', 1))

    # Connect to database
    session = models.Session()

    # Get posts with highest point counts, filtering by feed_ids if specified
    if feed_ids:
        subquery = session.query(models.Post).with_entities(
            models.Post.created, models.Post.uid, models.Post.source, models.Post.id, models.Post.link,
            models.Post.title, models.Post.type, models.Post.username,
            models.Post.website, models.FeedPost.comment_count,
            models.FeedPost.feed_rank, models.FeedPost.point_count).join(
            models.FeedPost).filter(
            models.FeedPost.feed_id.in_(feed_ids)).order_by(
            models.Post.id, models.FeedPost.point_count.desc()).distinct(
            models.Post.id).subquery()

        query = session.query(subquery).order_by(
            subquery.columns.get('point_count').desc()).limit(count)

    else:
        subquery = session.query(models.Post).with_entities(
            models.Post.created, models.Post.uid, models.Post.source, models.Post.id, models.Post.link,
            models.Post.title, models.Post.type, models.Post.username,
            models.Post.website, models.FeedPost.comment_count,
            models.FeedPost.feed_rank, models.FeedPost.point_count).join(
            models.FeedPost).order_by(
            models.Post.id, models.FeedPost.point_count.desc()).distinct(
            models.Post.id).subquery()

        query = session.query(subquery).order_by(
            subquery.columns.get('point_count').desc()).limit(count)

    session.close()

    posts = []

    for row in query:
        posts.append(row._asdict())

    return jsonify(posts)


def get_post_types(feed_ids):
    # Return sample data if there is no database connection
    if not os.environ['DB_CONNECTION']:
        with open(os.path.dirname(os.path.dirname(os.path.abspath(__file__))) +
            '/sample_data/' + feed_ids + '_post_types.json',
            'r') as sample_data:
                return jsonify(json.load(sample_data))

    # Otherwise, connect to database
    session = models.Session()

    # Get count of types of posts ('article' vs. 'ask' vs. 'job' vs. 'show'),
    # filtering by feed_ids if specified
    if feed_ids:
        subquery = session.query(models.Post).with_entities(
            models.Post.id, models.Post.type).join(
            models.FeedPost).filter(
            models.FeedPost.feed_id.in_(feed_ids)).order_by(
            models.Post.id, models.FeedPost.feed_id.desc()).distinct(
            models.Post.id).subquery()

        query = session.query(subquery).with_entities(
            subquery.columns.get('type'),
            func.count('*').label("type_count")).group_by(
            subquery.columns.get('type')).order_by(desc('type_count'))

    else:
        subquery = session.query(models.Post).with_entities(
            models.Post.id, models.Post.type).subquery()

        query = session.query(subquery).with_entities(
            subquery.columns.get('type'),
            func.count('*').label("type_count")).group_by(
            subquery.columns.get('type')).order_by(desc('type_count'))

    session.close()

    types = []

    for row in query:
        types.append(row._asdict())

    return jsonify(types)


def get_most_frequent_title_words(feed_ids):
    # Get number of requested words from query parameter, using default if
    # null
    count = int(request.args.get('count', 1))

    # Connect to database
    session = models.Session()

    # Get highest-frequency words used in post titles, excluding stop words,
    # filtering by feed_ids if specified
    if feed_ids:
        query = session.execute(
            """
              SELECT *
                FROM ts_stat(
                     $$SELECT to_tsvector('simple_english', LOWER(title))
                         FROM (
                                 SELECT DISTINCT ON (post.id)
                                        post.id, post.title, feed_post.feed_id
                                   FROM post
                                        JOIN feed_post
                                          ON feed_post.post_id = post.id
                                  WHERE feed_id = ANY(:feed_id)
                               ORDER BY post.id, feed_post.feed_id DESC
                         ) post_table$$
                )
               WHERE word NOT IN ('ask', 'hn', 'show')
                 AND LENGTH (word) > 1
            ORDER BY nentry DESC
               LIMIT :count;
            """,
            {'feed_id': feed_ids,
            'count': count}
            ).fetchall()

    else:
        query = session.execute(
            """
              SELECT *
                FROM ts_stat(
                     $$SELECT to_tsvector('simple_english', LOWER(title))
                         FROM post$$
                )
               WHERE word NOT IN ('ask', 'hn', 'show')
                 AND LENGTH (word) > 1
            ORDER BY nentry DESC
               LIMIT :count;
            """,
            {'feed_id': feed_ids,
            'count': count}
            ).fetchall()

    session.close()

    words = []

    for row in query:
        words.append(dict(row))

    return jsonify(words)


def get_top_posts(feed_ids):
    # Get number of requested posts from query parameter, using default if
    # null
    count = int(request.args.get('count', 3))

    # Connect to database
    session = models.Session()

    # Get posts in order of rank, filtering by feed_ids if specified
    if feed_ids:
        subquery = session.query(models.Post).with_entities(
            models.Post.created, models.Post.uid, models.Post.source, models.Post.id, models.Post.link,
            models.Post.title, models.Post.type, models.Post.username,
            models.Post.website, models.FeedPost.comment_count,
            models.FeedPost.feed_rank, models.FeedPost.point_count).join(
            models.FeedPost).filter(
            models.FeedPost.feed_id.in_(feed_ids)).order_by(
            models.Post.id, models.FeedPost.feed_rank,
            models.FeedPost.point_count.desc()).distinct(
            models.Post.id).subquery()

        query = session.query(subquery).order_by(
            subquery.columns.get('feed_rank'),
            subquery.columns.get('point_count').desc()).limit(count)

    else:
        subquery = session.query(models.Post).with_entities(
            models.Post.created, models.Post.uid, models.Post.source, models.Post.id, models.Post.link,
            models.Post.title, models.Post.type, models.Post.username,
            models.Post.website, models.FeedPost.comment_count,
            models.FeedPost.feed_rank, models.FeedPost.point_count).join(
            models.FeedPost).order_by(
            models.Post.id, models.FeedPost.feed_rank,
            models.FeedPost.point_count.desc()).distinct(
            models.Post.id).subquery()

        query = session.query(subquery).order_by(
            subquery.columns.get('feed_rank'),
            subquery.columns.get('point_count').desc()).limit(count)

    session.close()

    posts = []

    for row in query:
        posts.append(row._asdict())

    return jsonify(posts)


def get_top_websites(feed_ids):
    # Get number of requested websites from query parameter, using default if
    # null
    count = int(request.args.get('count', 1))

    # Connect to database
    session = models.Session()

    # Get websites that highest number of posts are from, filtering by feed_ids
    # if specified
    if feed_ids:
        subquery = session.query(models.Post).with_entities(
            models.Post.id, models.Post.website).join(
            models.FeedPost).filter(
            models.FeedPost.feed_id.in_(feed_ids)).filter(
            models.Post.website != '').order_by(
            models.Post.id, models.FeedPost.feed_id.desc()).distinct(
            models.Post.id).subquery()

        query = session.query(subquery).with_entities(
            subquery.columns.get('website'),
            func.count('*').label("link_count")).group_by(
            subquery.columns.get('website')).order_by(
            desc('link_count')).limit(count)

    else:
        subquery = session.query(models.Post).with_entities(
            models.Post.id, models.Post.website).filter(
            models.Post.website != '').subquery()

        query = session.query(subquery).with_entities(
            subquery.columns.get('website'),
            func.count('*').label("link_count")).group_by(
            subquery.columns.get('website')).order_by(
            desc('link_count')).limit(count)

    session.close()

    websites = []

    for row in query:
        websites.append(row._asdict())

    return jsonify(websites)


def get_users_with_most_comments(feed_ids):
    # Return sample data if there is no database connection
    if not os.environ['DB_CONNECTION']:
        with open(os.path.dirname(os.path.dirname(os.path.abspath(__file__))) +
            '/sample_data/' + feed_ids + '_users_most_comments.json',
            'r') as sample_data:
                return jsonify(json.load(sample_data))

    # Get number of requested users from query parameter, using default if
    # null
    count = int(request.args.get('count', 1))

    # Connect to database
    session = models.Session()

    # Get users who posted the most comments, filtering by feed_ids if
    # specified
    if feed_ids:
        subquery = session.query(models.Comment).with_entities(
            models.Comment.id, models.Comment.total_word_count,
            models.Comment.username).join(models.FeedComment).filter(
            models.FeedComment.feed_id.in_(feed_ids)).filter(
            models.Comment.username != '').order_by(models.Comment.id,
            models.FeedComment.feed_id.desc()).distinct(
            models.Comment.id).subquery()

        query = session.query(subquery).with_entities(
            subquery.columns.get('username'),
            func.count('*').label("comment_count"), func.sum(
                subquery.columns.get('total_word_count')
            ).label("word_count")).group_by(
            subquery.columns.get('username')).order_by(
            desc('comment_count')).limit(count)

    else:
        subquery = session.query(models.Comment).with_entities(
            models.Comment.id, models.Comment.total_word_count,
            models.Comment.username).filter(
            models.Comment.username != '').subquery()

        query = session.query(subquery).with_entities(
            subquery.columns.get('username'),
            func.count('*').label("comment_count"), func.sum(
                subquery.columns.get('total_word_count')
            ).label("word_count")).group_by(
            subquery.columns.get('username')).order_by(
            desc('comment_count')).limit(count)

    session.close()

    users = []

    for row in query:
        users.append(row._asdict())

    return jsonify(users)


def get_users_with_most_posts(feed_ids):
    # Get number of requested users from query parameter, using default if
    # null
    count = int(request.args.get('count', 1))

    # Connect to database
    session = models.Session()

    # Get users who posted the most posts, filtering by feed_ids if specified
    if feed_ids:
        subquery = session.query(models.Post).with_entities(
            models.Post.id, models.Post.username).join(models.FeedPost).filter(
            models.FeedPost.feed_id.in_(feed_ids)).filter(
            models.Post.username != '').order_by(models.Post.id,
            models.FeedPost.feed_id.desc()).distinct(models.Post.id).subquery()

        query = session.query(subquery).with_entities(
            subquery.columns.get('username'),
            func.count('*').label("post_count")).group_by(
            subquery.columns.get('username')).order_by(
            desc('post_count')).limit(count)

    else:
        subquery = session.query(models.Post).with_entities(
            models.Post.id, models.Post.username).filter(
            models.Post.username != '').subquery()

        query = session.query(subquery).with_entities(
            subquery.columns.get('username'),
            func.count('*').label("post_count")).group_by(
            subquery.columns.get('username')).order_by(
            desc('post_count')).limit(count)

    session.close()

    users = []

    for row in query:
        users.append(row._asdict())

    return jsonify(users)


def get_users_with_most_words_in_comments(feed_ids):
    # Get number of requested users from query parameter, using default if
    # null
    count = int(request.args.get('count', 1))

    # Connect to database
    session = models.Session()

    # Get users who posted the most words in comments, filtering by feed_ids if
    # specified
    if feed_ids:
        subquery = session.query(models.Comment).with_entities(
            models.Comment.id, models.Comment.total_word_count,
            models.Comment.username).join(models.FeedComment).filter(
            models.FeedComment.feed_id.in_(feed_ids)).filter(
            models.Comment.username != '').order_by(models.Comment.id,
            models.FeedComment.feed_id.desc()).distinct(
            models.Comment.id).subquery()

        query = session.query(subquery).with_entities(
            subquery.columns.get('username'),
            func.count('*').label("comment_count"), func.sum(
                subquery.columns.get('total_word_count')
            ).label("word_count")).group_by(
            subquery.columns.get('username')).order_by(
            desc('word_count')).limit(count)

    else:
        subquery = session.query(models.Comment).with_entities(
            models.Comment.id, models.Comment.total_word_count,
            models.Comment.username).filter(
            models.Comment.username != '').subquery()

        query = session.query(subquery).with_entities(
            subquery.columns.get('username'),
            func.count('*').label("comment_count"), func.sum(
                subquery.columns.get('total_word_count')
            ).label("word_count")).group_by(
            subquery.columns.get('username')).order_by(
            desc('word_count')).limit(count)

    session.close()

    users = []

    for row in query:
        users.append(row._asdict())

    return jsonify(users)
