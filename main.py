import time
import json
import random

import aiohttp
import asyncio
import logging

from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, urldefrag
from urllib import robotparser


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)

logger = logging.getLogger(__name__)


def log_section(title: str) -> None:
    logger.info("")
    logger.info("=" * 78)
    logger.info(title)
    logger.info("=" * 78)

class RateLimiter:
    def __init__(self, requests_per_second: float = 1.0, per_domain: bool = True) -> None:
        self.requests_per_second = requests_per_second
        if self.requests_per_second <= 0:
            raise ValueError("Requests per second must be greater than 0")

        self.interval = 1 / requests_per_second
        self.per_domain = per_domain
        self.last_request = {}
        self.locks = {}

    async def acquire(self, domain: str = None):
        if self.per_domain and domain:
            key = domain
        elif domain is None:
            key = "global"
        else:
            key = "global"

        if key not in self.locks:
            lock = asyncio.Lock()
            self.locks[key] = lock

        lock = self.locks[key]

        async with lock:
            now = time.monotonic()
            last_time = self.last_request.get(key)

            if last_time is not None:
                elapsed = now - last_time
                wait_time = self.interval - elapsed

                if wait_time > 0:
                    logger.debug("[RATE LIMIT] Waiting %.2f sec for %s", wait_time, key)

                    await asyncio.sleep(wait_time)
            self.last_request[key] = time.monotonic()

            logger.debug("[RATE LIMIT] Request allowed for %s", key)

class RobotsParser:
    def __init__(self, user_agent: str = "*") -> None:
        self.user_agent = user_agent
        self.rules = {}

    async def fetch_robots(self, base_url: str):
        domain = urlparse(base_url).hostname
        robots_url = urljoin(base_url, "/robots.txt")
        if domain in self.rules:
            return self.rules[domain]
        else:
            async with aiohttp.ClientSession(headers={"User-Agent": self.user_agent}) as session:
                async with session.get(robots_url) as response:
                    parser = robotparser.RobotFileParser()
                    parser.set_url(robots_url)
                    if response.status == 200:
                        robots_text = await response.text()
                        parser.parse(robots_text.splitlines())
                    else:
                        parser.parse([])

                    self.rules[domain] = parser
                    return parser
    def can_fetch(self, url: str, user_agent: str = "*") -> bool:
        domain = urlparse(url).hostname
        parser = self.rules.get(domain)

        if parser is None:
            return True

        return parser.can_fetch(user_agent, url)

    def get_crawl_delay(self, url: str, user_agent: str = "*") -> float:
        domain = urlparse(url).hostname
        parser = self.rules.get(domain)

        if parser is None:
            return 0.0

        delay = parser.crawl_delay(user_agent)
        if delay is None:
            return 0.0

        return float(delay)

class HTMLParser:
    def __init__(self):
        pass

    async def parse_html(self, html: str, url: str) -> dict:
        soup = BeautifulSoup(html, "lxml")
        metadata = self.extract_metadata(soup)
        text = self.extract_text(soup)
        links = self.extract_links(soup, url)
        images = self.extract_images(soup)
        headings = self.extract_headings(soup)
        tables = self.extract_tables(soup)
        lists = self.extract_lists(soup)

        return {
            "url": url,
            "title": metadata["title"],
            "metadata": metadata,
            "text": text,
            "links": links,
            "images": images,
            "headings": headings,
            "tables": tables,
            "lists": lists
        }

    def extract_links(self, soup: BeautifulSoup, base_url: str) -> list[str]:
        links_tags = soup.find_all("a", href=True)
        links = []
        seen = set()

        for i in links_tags:
            link = i.get("href", "").strip()

            if not link:
                continue

            if link.startswith(("#", "mailto:", "javascript:", "tel:")):
                continue

            absolute_url = urljoin(base_url, link)
            absolute_url, _ = urldefrag(absolute_url)

            parsed_url = urlparse(absolute_url)

            if parsed_url.scheme not in ("http", "https"):
                continue

            if not parsed_url.netloc:
                continue

            if absolute_url not in seen:
                seen.add(absolute_url)
                links.append(absolute_url)

        return links

    def extract_text(self, soup: BeautifulSoup, selector: str = None) -> str:
        if selector:
            target = soup.select_one(selector)

            if target is None:
                logger.warning("Selector not found: %s", selector)
                return ""
        else:
            target = soup

        return target.get_text(" ", strip=True)

    def extract_metadata(self, soup: BeautifulSoup) -> dict:
        title_tag = soup.find("title")
        if title_tag is not None:
            title = title_tag.get_text(strip=True)
        else:
            logger.warning("Title not found")
            title = ""

        description_tag = soup.find("meta", attrs={"name":"description"})
        if description_tag is not None:
            description = description_tag.get("content", "")
        else:
            logger.warning("Description not found")
            description = ""

        keywords_tag = soup.find("meta", attrs={"name": "keywords"})
        if keywords_tag is not None:
            keywords = keywords_tag.get("content", "")
        else:
            logger.warning("Keyword not found")
            keywords = ""

        return {
            "title": title,
            "description": description,
            "keywords": keywords,
        }

    def extract_images(self, soup: BeautifulSoup) -> list[dict]:
        images = []
        images_tag = soup.find_all("img", src=True)

        if not images_tag:
            logger.warning("Image not found")

        for img in images_tag:
            src = img.get("src")
            alt = img.get("alt")

            if alt is None:
                alt = ""

            img_dict = {"src": src, "alt": alt}
            images.append(img_dict)

        return images

    def extract_headings(self, soup: BeautifulSoup) -> list[dict]:
        heading = []

        heading_tags = soup.find_all(["h1", "h2", "h3"])

        for head in heading_tags:
            text = head.get_text(" ", strip=True)

            head_dict = {
                "level": head.name,
                "text": text
            }

            heading.append(head_dict)
        return heading


    def extract_tables(self, soup: BeautifulSoup) -> list[dict]:
        tables = []

        for table_tag in soup.find_all("table"):
            headers = []
            rows = []

            for row_tag in table_tag.find_all("tr"):
                cell_tags = row_tag.find_all(["th", "td"])

                row_values = [
                    cell.get_text(" ", strip=True)
                    for cell in cell_tags
                ]

                if not row_values:
                    continue

                header_tags = row_tag.find_all("th")

                if header_tags and not headers:
                    headers = [
                        header.get_text(" ", strip=True)
                        for header in header_tags
                    ]
                else:
                    rows.append(row_values)

            tables.append({
                "headers": headers,
                "rows": rows,
            })

        return tables


    def extract_lists(self, soup: BeautifulSoup) -> list[dict]:
        lists = []
        list_tags = soup.find_all(["ul", "ol"])

        for list_tag in list_tags:
            items = []
            li = list_tag.find_all("li")

            for li_tag in li:
                text = li_tag.get_text(" ", strip=True)
                items.append(text)

            lists.append({
                "type": list_tag.name,
                "items": items
            })

        return lists

class CrawlerQueue:
    def __init__(self):
        self.queued_urls = set()
        self.queue = asyncio.PriorityQueue()
        self.counter = 0
        self.processed_urls = {}
        self.failed_urls = {}
        self.deep_urls = {}


    @staticmethod
    def normalize_url(url: str) -> str:
        parsed = urlparse(url)
        path = parsed.path or "/"
        return parsed._replace(path=path, fragment="").geturl()

    def add_url(self, url: str, priority: int = 0, depth: int = 0) -> bool:
        url = self.normalize_url(url)

        if url in self.queued_urls:
            return False

        self.queued_urls.add(url)
        self.counter += 1
        self.queue.put_nowait((priority, self.counter, url))
        self.deep_urls[url] = depth
        return True

    def deepness(self, url: str) -> int | None:
        return self.deep_urls.get(url)

    async def get_next(self) -> str | None:
        if self.queue.empty():
            return None

        priority, counter, url = await self.queue.get()
        return url


    def mark_processed(self, url: str) -> None:
        self.processed_urls[url] = True


    def mark_failed(self, url: str, error: str ) -> None:
        self.failed_urls[url] = error


    def get_stats(self) -> dict:
        return {
            "queued": self.queue.qsize(),
            "processed": len(self.processed_urls),
            "failed": len(self.failed_urls),
        }

class SemaphoreManager:
    def __init__(self, global_limit, domain_limit):
        self.global_limit = global_limit
        self.domain_limit = domain_limit
        self.domain_semaphores = {}
        self.active_tasks = 0

        self.semaphore = asyncio.Semaphore(global_limit)

    def get_domain_semaphore(self, url: str):
        domain = urlparse(url).hostname
        if domain in self.domain_semaphores:
            logger.debug("Existing semaphore: %s", domain)
            return self.domain_semaphores[domain]
        else:
            domain_semaphore = asyncio.Semaphore(self.domain_limit)
            self.domain_semaphores[domain] = domain_semaphore

        return self.domain_semaphores[domain]

    async def acquire(self, url):
        domain_semaphore = self.get_domain_semaphore(url)

        await self.semaphore.acquire()
        await domain_semaphore.acquire()

        self.active_tasks += 1

    def release(self, url):
        domain_semaphore = self.get_domain_semaphore(url)

        self.semaphore.release()
        domain_semaphore.release()

        self.active_tasks -= 1

class AsyncCrawler:
    def __init__(
            self,
            max_concurrent: int = 4,
            domain_limit: int = 2,
            requests_per_second: float = 1.0,
            max_depth: int = 2,
            respect_robots: bool = True,
            user_agent: str = "MyBot/1.0",
            min_delay: float = 0.0,
            jitter: float = 0.0,
            max_retries: int = 3,
            backoff_factor: float = 1.0,
    ):
        self.max_concurrency = max_concurrent
        self.semaphore_manager = SemaphoreManager(max_concurrent, domain_limit)
        self.rate_limiter = RateLimiter(requests_per_second)
        self.max_depth = max_depth

        self.session = None
        self.parser = HTMLParser()
        self.visited_urls = set()
        self.failed_urls = {}
        self.processed_urls = {}

        self.respect_robots = respect_robots
        self.user_agent = user_agent
        self.robots_parser = RobotsParser(user_agent=user_agent)
        self.blocked_robots = 0
        self.request_count = 0
        self.request_intervals = []
        self.last_request_started = None
        self.min_delay = min_delay
        self.jitter = jitter
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor

        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")

        if self.backoff_factor < 0:
            raise ValueError("backoff_factor must be >= 0")

        if self.jitter < 0:
            raise ValueError("jitter must be >= 0")

        if self.min_delay < 0:
            raise ValueError("min_delay must be >= 0")


    async def fetch_url(self, url: str) -> str | None:
        if self.session is None:

            timeout = aiohttp.ClientTimeout(connect=10, sock_read=2)
            self.session = aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": self.user_agent})

        robots_delay = 0.0
        if self.respect_robots:
            await self.robots_parser.fetch_robots(url)

            if not self.robots_parser.can_fetch(url, self.user_agent):
                self.blocked_robots += 1
                logger.warning("[ROBOTS] BLOCKED | %s", url)
                return None

            robots_delay = self.robots_parser.get_crawl_delay(url, self.user_agent)

            if robots_delay > 0:
                logger.debug(
                    "[ROBOTS] Crawl-delay %.2f sec | %s",
                    robots_delay,
                    url
                )

        delay = max(robots_delay, self.min_delay)

        if self.jitter > 0:
            delay += random.uniform(0, self.jitter)

        if delay > 0:
            logger.debug(
                "[DELAY] Waiting %.2f sec | %sec",
                delay,
                url
            )

            await asyncio.sleep(delay)

        logger.debug("[DOWNLOAD] WAITING  | %s", url)

        for attempt in range(self.max_retries + 1):
            hostname = urlparse(url).hostname

            await self.rate_limiter.acquire(hostname)
            await self.semaphore_manager.acquire(url)

            logger.debug(
                "[DOWNLOAD] STARTED | %s | attempt %d",
                url,
                attempt + 1
            )

            request_started = time.monotonic()

            if self.last_request_started is not None:
                self.request_intervals.append(
                    request_started - self.last_request_started
                )

            self.last_request_started = request_started
            self.request_count += 1

            try:
                async with self.session.get(url) as response:
                    response.raise_for_status()
                    text = await response.text()

                    logger.debug("[DOWNLOAD] FINISHED | %s", url)
                    return text

            except asyncio.TimeoutError:
                logger.warning("[DOWNLOAD] TIMEOUT | %s", url)

            except aiohttp.ClientResponseError:
                logger.error("[DOWNLOAD] HTTP ERROR | %s", url)

            except aiohttp.ClientError:
                logger.error("[DOWNLOAD] NETWORK ERROR | %s", url)

            finally:
                self.semaphore_manager.release(url)

            if attempt < self.max_retries:
                backoff_delay = self.backoff_factor * (2 ** attempt)

                logger.debug(
                    "[BACKOFF] Waiting %.2f sec before retry | %s",
                    backoff_delay,
                    url
                )

                await asyncio.sleep(backoff_delay)
        return None


    async def fetch_urls(self, urls: list[str]) -> dict[str, str | None]:
        result = await asyncio.gather(*(self.fetch_url(url) for url in urls))
        return dict(zip(urls, result))


    async def fetch_and_parse(self, url: str) -> dict | None:
        html = await self.fetch_url(url)

        if html is None:
            self.failed_urls[url] = "Failed to fetch"
            logger.warning("Failed to fetch %s", url)
            return None
        else:
            result = await self.parser.parse_html(html, url)
            return result

    async def crawl(
            self,
            start_urls: list[str],
            max_pages: int = 15,
            same_domain_only: bool = False,
            max_depth: int | None = None,
            include_patterns: list[str] | None = None,
            exclude_patterns: list[str] | None = None,

    ):
        start = time.time()
        queue = CrawlerQueue()

        if max_depth is None:
            max_depth = self.max_depth

        if include_patterns is None:
            include_patterns = []

        if exclude_patterns is None:
            exclude_patterns = []

        allowed_domains = {
            urlparse(url).hostname
            for url in start_urls
            if urlparse(url).hostname
        }

        for url in start_urls:
            queue.add_url(url, depth=0)

        results = {}
        attempted_pages = 0


        async def worker(url: str):
            page = await self.fetch_and_parse(url)
            return page

        while len(results) < max_pages:
            batch_urls = []

            while len(batch_urls) < self.max_concurrency and len(results) + len(batch_urls) < max_pages:
                next_url = await queue.get_next()

                if next_url is None:
                    break

                if next_url in self.visited_urls:
                    continue

                self.visited_urls.add(next_url)
                batch_urls.append(next_url)

            if not batch_urls:
                break

            tasks = [
                asyncio.create_task(worker(url))
                for url in batch_urls
            ]

            pages = await asyncio.gather(*tasks)

            for url, page in zip(batch_urls, pages):
                attempted_pages += 1
                current_depth = queue.deepness(url)

                if page is None:
                    logger.error("[PAGE %02d] FAILED | %s", attempted_pages, url)
                    continue

                results[url] = page
                queue.mark_processed(url)
                self.processed_urls[url] = page

                logger.info("[PAGE %02d] OK     | %s", attempted_pages, url)

                end = time.time()
                time_progress = end - start
                speed = len(results) / time_progress if time_progress > 0 else 0

                logger.debug(f"[CRAWLER] FINISHED FOR {time_progress:.2f} seconds")
                logger.debug(
                    f"[CRAWLER] CRAWLED {len(results)} PAGES. "
                    f"AVG SPEED: {speed:.2f} PAGES PER SECOND"
                )
                logger.debug(f"[CRAWLER] {queue.get_stats()['queued']} URLS ARE IN QUEUE")
                logger.debug(f"[CRAWLER] QUANTITY OF ERRORS: {len(self.failed_urls)} \n")

                for link in results[url]["links"]:
                    if same_domain_only:
                        link_domain = urlparse(link).hostname
                        if link_domain not in allowed_domains:
                            continue

                    if any(pattern in link for pattern in exclude_patterns):
                        continue

                    if include_patterns and not any(pattern in link for pattern in include_patterns):
                        continue

                    if current_depth < max_depth:
                        queue.add_url(link, depth=current_depth + 1)
        return results



    async def close(self) -> None:
        if self.session is not None and not self.session.closed:
            await self.session.close()
            self.session = None


async def test_queue() -> None:
    queue = CrawlerQueue()

    assert queue.add_url("https://example.com/a", priority=1)
    assert queue.add_url("https://example.com/b", priority=0)
    assert not queue.add_url("https://example.com/a", priority=0)

    first = await queue.get_next()
    assert first == "https://example.com/b"
    assert queue.deepness(first) == 0
    queue.mark_processed(first)

    second = await queue.get_next()
    assert second == "https://example.com/a"
    queue.mark_failed(second, "test error")

    assert queue.get_stats() == {
        "queued": 0,
        "processed": 1,
        "failed": 1,
    }

    logger.info("[CHECK] CrawlerQueue: PASSED")


async def main():
    await test_queue()

    crawler = AsyncCrawler(
        max_concurrent=4,
        domain_limit=2,
        requests_per_second=2.0,
        respect_robots=True,
        user_agent="MyBot/1.0",
        min_delay=0.5,
        jitter=0.2,
        max_retries=3,
        backoff_factor=1.0,
    )

    demo_urls = [
        (
            "HTTP error + backoff",
            "https://www.python.org/this-page-does-not-exist-async-hw-4",
        ),
        (
            "robots.txt block",
            "https://www.google.com/search?q=python",
        ),
    ]

    for label, demo_url in demo_urls:
        logger.info("[DEMO] %s | %s", label, demo_url)
        demo_result = await crawler.fetch_url(demo_url)

        if demo_result is None:
            logger.info("[DEMO] HANDLED | %s", demo_url)
        else:
            logger.warning("[DEMO] UNEXPECTED SUCCESS | %s", demo_url)
    
    crawl_config = {
        "start_urls": ["https://www.python.org"],
        "max_pages": 15,
        "max_depth": 2,
        "same_domain_only": True,
        "include_patterns": None,
        "exclude_patterns": [],
    }

    try:
        crawl_results = await crawler.crawl(**crawl_config)

        with open("crawl_results.json", "w", encoding="utf-8") as file:
            json.dump(
                crawl_results,
                file,
                ensure_ascii=False,
                indent=2,
            )

        logger.info("[RESULT] Saved pages: %d", len(crawl_results))
        logger.info("[RESULT] Visited URLs: %d", len(crawler.visited_urls))
        logger.info("[RESULT] Processed URLs: %d", len(crawler.processed_urls))
        logger.info("[RESULT] Failed URLs: %d", len(crawler.failed_urls))
        logger.info("[RESULT] File: crawl_results.json")

        average_delay = (
            sum(crawler.request_intervals) / len(crawler.request_intervals)
            if crawler.request_intervals
            else 0.0
        )
        logger.info("[STATS] Page requests: %d", crawler.request_count)
        logger.info("[STATS] Robots blocked: %d", crawler.blocked_robots)
        logger.info("[STATS] Average request interval: %.2f sec", average_delay)
    finally:
        await crawler.close()


if __name__ == "__main__":
    asyncio.run(main())
