from abc import ABC, abstractmethod
import asyncio

class BaseScraper(ABC):
    @abstractmethod
    def scrape_loop(self):
        """Main loop to initiate scraping and create a feed."""
        pass

    @abstractmethod
    async def scrape_page(self, page, feed_id, loop):
        """Scrape a specific page of the source."""
        pass

    @abstractmethod
    async def scrape_post(self, post_uid, feed_id, loop, page_number):
        """Scrape a specific post and its comments."""
        pass
