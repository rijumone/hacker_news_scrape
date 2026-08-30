---
name: api-only-data
description: Answer data questions using only the REST API.
---

# Instructions

You must use only the REST API to answer data questions.
Do not query the database directly.
Do not use SQL commands.
Do not use Python scripts to read the database.

## Procedure

1. Read the data question from the user.
2. Find the correct REST API endpoint for the data.
3. Use the `curl` command to get the data from the API.
4. Use the `jq` command to parse the JSON response.
5. Read the parsed data.
6. Give the answer to the user.

## System Information

The REST API is available at `http://localhost:8001/api/`.
The API provides endpoints for posts, comments, and statistics.
Use `http://localhost:8001/api/reddit/post?subreddit=subreddit` to get fetch data for specific subreddits.