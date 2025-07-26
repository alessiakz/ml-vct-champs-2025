import requests
from bs4 import BeautifulSoup
import json
import time
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import re
import os
from pathlib import Path
from urllib.parse import urljoin, urlparse
import hashlib
from dataclasses import dataclass, asdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import time

@dataclass
class Player:
    """Data class for player information."""
    player_name: str
    player_url: str
    player_id: str
    role: str
    status: str = "active"  # active, inactive, sub, etc.

@dataclass
class Match:
    """Data class for match information."""
    opponent: str
    result: str
    score: Optional[str] = None
    date: Optional[str] = None
    tournament: Optional[str] = None
    match_url: Optional[str] = None

@dataclass
class TeamData:
    """Data class for team information."""
    team_name: str
    team_id: str
    logo_url: Optional[str]
    region: Optional[str]
    roster: List[Player]
    recent_matches: List[Match]
    team_stats: Dict
    team_url: str
    scraped_at: str
    last_updated: Optional[str] = None

@dataclass
class Tournament:
    """Data class for tournament information."""
    tournament_name: str
    tournament_id: str
    start_date: Optional[str]
    end_date: Optional[str]
    region: Optional[str]
    prize_pool: Optional[str]
    participating_teams: List[str]
    tournament_url: str
    scraped_at: str

class VLRScraperError(Exception):
    """Custom exception for VLR scraper errors."""
    pass

class RateLimiter:
    """Thread-safe rate limiter."""
    def __init__(self, max_requests: int = 30, time_window: int = 60):
        self.max_requests = max_requests
        self.time_window = time_window
        self.requests = []
        self.lock = threading.Lock()
    
    def wait_if_needed(self):
        """Wait if rate limit would be exceeded."""
        with self.lock:
            now = time.time()
            # Remove old requests outside the time window
            self.requests = [req_time for req_time in self.requests if now - req_time < self.time_window]
            
            if len(self.requests) >= self.max_requests:
                # Wait until the oldest request is outside the window
                sleep_time = self.time_window - (now - self.requests[0]) + 0.1
                if sleep_time > 0:
                    time.sleep(sleep_time)
                    # Clean up old requests again
                    now = time.time()
                    self.requests = [req_time for req_time in self.requests if now - req_time < self.time_window]
            
            self.requests.append(now)

class CacheManager:
    """Simple file-based cache manager."""
    def __init__(self, cache_dir: Path, cache_duration_hours: int = 24):
        self.cache_dir = cache_dir
        self.cache_duration = timedelta(hours=cache_duration_hours)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
    
    def _get_cache_path(self, url: str) -> Path:
        """Generate cache file path from URL."""
        url_hash = hashlib.md5(url.encode()).hexdigest()
        return self.cache_dir / f"{url_hash}.json"
    
    def get(self, url: str) -> Optional[Dict]:
        """Get cached data if valid."""
        cache_path = self._get_cache_path(url)
        if not cache_path.exists():
            return None
        
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                cached_data = json.load(f)
            
            cached_time = datetime.fromisoformat(cached_data['cached_at'])
            if datetime.now() - cached_time < self.cache_duration:
                return cached_data['data']
        except (json.JSONDecodeError, KeyError, ValueError):
            # Invalid cache file, remove it
            cache_path.unlink(missing_ok=True)
        
        return None
    
    def set(self, url: str, data: Dict) -> None:
        """Cache data."""
        cache_path = self._get_cache_path(url)
        cache_data = {
            'cached_at': datetime.now().isoformat(),
            'url': url,
            'data': data
        }
        
        try:
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logging.error(f"Failed to cache data for {url}: {e}")

class VLRScraper:
    """
    Enhanced web scraper for vlr.gg with caching, rate limiting, and improved error handling.
    """
    
    def __init__(self, 
                 base_url: str = "https://www.vlr.gg", 
                 delay: float = 1.0,
                 max_workers: int = 3,
                 cache_duration_hours: int = 6,
                 retry_attempts: int = 3):
        self.base_url = base_url
        self.delay = delay
        self.max_workers = max_workers
        self.retry_attempts = retry_attempts
        
        # Setup components
        self.setup_directories()
        self.setup_logging()
        self.setup_session()
        
        # Initialize rate limiter and cache
        self.rate_limiter = RateLimiter(max_requests=20, time_window=60)
        self.cache = CacheManager(self.project_root / "cache", cache_duration_hours)
        
        # Selector configurations
        self.selectors = self._load_selector_config()
        
    def setup_directories(self):
        """Create necessary directories for the project structure."""
        self.project_root = Path.cwd()
        self.data_dir = self.project_root / "data"
        self.raw_data_dir = self.data_dir / "raw"
        self.processed_data_dir = self.data_dir / "processed"
        self.logs_dir = self.project_root / "logs"
        
        directories = [
            self.raw_data_dir / "matches",
            self.raw_data_dir / "teams", 
            self.raw_data_dir / "players",
            self.raw_data_dir / "tournaments",
            self.processed_data_dir / "features",
            self.processed_data_dir / "training",
            self.logs_dir,
            self.project_root / "cache"
        ]
        
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
            
    def setup_logging(self):
        """Setup enhanced logging configuration."""
        log_file = self.logs_dir / f"scraping_{datetime.now().strftime('%Y%m%d')}.log"
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
        
    def setup_session(self):
        """Setup requests session with retry strategy."""
        self.session = requests.Session()
        
        # Retry strategy
        retry_strategy = Retry(
            total=self.retry_attempts,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        
        # Headers
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        })
    
    def _load_selector_config(self) -> Dict:
        """Load CSS selector configurations."""
        return {
            'team_name': [
                'h1.wf-title',
                'h1.team-header-name',
                '.team-header-name',
                'h1[class*="title"]',
                'h1'
            ],
            'team_logo': [
                'img.team-header-logo',
                '.team-header-logo img',
                '.wf-avatar img',
                'img[class*="logo"]'
            ],
            'team_region': [
                '.team-header-country',
                '.flag',
                '[class*="country"]',
                '[class*="flag"]',
                '.team-header-info .flag'
            ],
            'roster_players': [
                '.team-roster-item a[href*="/player/"]',
                '.roster-item a[href*="/player/"]',
                'a[href*="/player/"]'
            ],
            'match_elements': [
                '.wf-card',
                '.match-item',
                '[class*="match"]',
                '.mod-color',
                '.match-list-item'
            ]
        }
    
    def _make_request(self, url: str, use_cache: bool = True) -> Optional[BeautifulSoup]:
        """Enhanced request method with caching and rate limiting."""
        # Check cache first
        if use_cache:
            cached_soup = self.cache.get(url)
            if cached_soup:
                self.logger.info(f"Using cached data for: {url}")
                return BeautifulSoup(cached_soup['html'], 'html.parser')
        
        # Rate limiting
        self.rate_limiter.wait_if_needed()
        
        try:
            self.logger.info(f"Making request to: {url}")
            response = self.session.get(url, timeout=15)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Cache the response
            if use_cache:
                self.cache.set(url, {'html': str(soup)})
            
            return soup
            
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Request failed for {url}: {e}")
            return None
    
    def _extract_with_selectors(self, soup: BeautifulSoup, selector_key: str, 
                               attribute: Optional[str] = None) -> Optional[str]:
        """Extract data using multiple selectors with fallback."""
        selectors = self.selectors.get(selector_key, [])
        
        for selector in selectors:
            try:
                element = soup.select_one(selector)
                if element:
                    if attribute:
                        value = element.get(attribute)
                        if value:
                            return urljoin(self.base_url, value) if attribute in ['src', 'href'] else value
                    else:
                        text = element.get_text(strip=True)
                        if text and text.lower() not in ['unknown', 'n/a', '']:
                            return text
            except Exception as e:
                self.logger.debug(f"Error with selector {selector}: {e}")
                continue
        
        return None
    
    def _parse_team_roster_enhanced(self, soup: BeautifulSoup) -> List[Player]:
        """Enhanced roster parsing with better role detection."""
        roster = []
        
        player_links = soup.select('.team-roster-item a[href*="/player/"]')
        if not player_links:
            # Try alternative selectors
            alternative_selectors = [
                '.roster-item a[href*="/player/"]',
                'a[href*="/player/"]'
            ]
            for selector in alternative_selectors:
                player_links = soup.select(selector)
                if player_links:
                    break
        
        self.logger.info(f"Found {len(player_links)} player links")
        
        # Enhanced role detection patterns
        role_patterns = {
            r'\binactive\b': 'inactive',
            r'\bsub\b|\bsubstitute\b': 'substitute',
            r'\bcoach\b': 'coach',
            r'\bassistant coach\b': 'assistant coach',
            r'\bperformance coach\b': 'performance coach',
            r'\bmanager\b': 'manager',
            r'\banalyst\b': 'analyst',
            r'\bigl\b': 'igl',  # in-game leader
        }
        
        for link in player_links:
            try:
                player_name = link.get_text(strip=True)
                if not player_name or len(player_name) < 2:
                    continue
                
                player_url = urljoin(self.base_url, link['href'])
                player_id = link['href'].split('/')[-1]
                
                # Get surrounding context for role detection
                parent = link.find_parent(class_='team-roster-item')
                if not parent:
                    parent = link.find_parent()
                
                context_text = ""
                if parent:
                    context_text = " ".join(parent.stripped_strings).lower()
                
                # Role detection
                role = "active player"
                status = "active"
                
                # Check if player name itself contains role info
                name_lower = player_name.lower()
                for pattern, detected_role in role_patterns.items():
                    if re.search(pattern, name_lower) or re.search(pattern, context_text):
                        role = detected_role
                        if detected_role == 'inactive':
                            status = 'inactive'
                        elif detected_role in ['substitute', 'sub']:
                            status = 'substitute'
                        break
                
                player = Player(
                    player_name=player_name,
                    player_url=player_url,
                    player_id=player_id,
                    role=role,
                    status=status
                )
                
                roster.append(player)
                
            except Exception as e:
                self.logger.error(f"Error parsing player: {e}")
                continue
        
        return roster
    
    def _parse_recent_matches_enhanced(self, soup: BeautifulSoup) -> List[Match]:
        """Enhanced match parsing with better data extraction."""
        matches = []
        
        # Find match elements using multiple selectors
        match_elements = []
        for selector in self.selectors['match_elements']:
            elements = soup.select(selector)
            if elements:
                match_elements = elements
                break
        
        self.logger.info(f"Found {len(match_elements)} potential match elements")
        
        for match_elem in match_elements[:15]:  # Get recent matches
            try:
                match_data = self._parse_single_match_enhanced(match_elem)
                if match_data:
                    matches.append(match_data)
            except Exception as e:
                self.logger.error(f"Error parsing match: {e}")
                continue
        
        return matches
    
    def _parse_single_match_enhanced(self, match_elem) -> Optional[Match]:
        """Enhanced single match parsing."""
        try:
            # Extract opponent with multiple strategies
            opponent_selectors = [
                '.text-of',
                '.team-name',
                '[class*="team"]:not([class*="own"])',
                'a[href*="/team/"]',
                '.match-item-vs .text-of'
            ]
            
            opponent = None
            for selector in opponent_selectors:
                elem = match_elem.select_one(selector)
                if elem:
                    text = elem.get_text(strip=True)
                    if text and text.lower() not in ['vs', 'versus']:
                        opponent = text
                        break
            
            # Extract result/score with multiple strategies
            score_selectors = [
                '.match-item-vs-score',
                '[class*="score"]',
                '.mod-win',
                '.mod-loss',
                '.match-item-eta'
            ]
            
            result = None
            score = None
            for selector in score_selectors:
                elem = match_elem.select_one(selector)
                if elem:
                    text = elem.get_text(strip=True)
                    if ':' in text or '-' in text:  # Likely a score
                        score = text
                    if text:
                        result = text
                    break
            
            # Extract date
            date_selectors = [
                '.match-item-date',
                '[class*="date"]',
                '.moment-tz-convert',
                '[data-time-to-show]'
            ]
            
            match_date = None
            for selector in date_selectors:
                elem = match_elem.select_one(selector)
                if elem:
                    # Try data attributes first
                    date_attr = elem.get('data-time-to-show') or elem.get('title')
                    if date_attr:
                        match_date = date_attr
                    else:
                        match_date = elem.get_text(strip=True)
                    break
            
            # Extract tournament if available
            tournament_selectors = [
                '.match-item-event',
                '[class*="tournament"]',
                '[class*="event"]'
            ]
            
            tournament = None
            for selector in tournament_selectors:
                elem = match_elem.select_one(selector)
                if elem:
                    tournament = elem.get_text(strip=True)
                    break
            
            # Extract match URL if available
            match_url = None
            match_link = match_elem.select_one('a[href*="/match/"]')
            if match_link:
                match_url = urljoin(self.base_url, match_link['href'])
            
            if opponent or result:
                return Match(
                    opponent=opponent or 'Unknown',
                    result=result or 'Unknown',
                    score=score,
                    date=match_date,
                    tournament=tournament,
                    match_url=match_url
                )
            
            return None
            
        except Exception as e:
            self.logger.error(f"Error parsing single match: {e}")
            return None
    
    def scrape_team_info(self, team_url: str) -> Optional[TeamData]:
        """Enhanced team information scraping."""
        soup = self._make_request(team_url)
        if not soup:
            return None
        
        try:
            # Extract team name
            team_name = self._extract_with_selectors(soup, 'team_name')
            if not team_name:
                # Fallback to URL parsing
                team_name = team_url.split('/')[-1].replace('-', ' ').title()
            
            # Extract team ID from URL
            team_id = team_url.split('/')[-1] if '/' in team_url else 'unknown'
            
            self.logger.info(f"Processing team: {team_name}")
            
            # Extract other team info
            logo_url = self._extract_with_selectors(soup, 'team_logo', 'src')
            region = self._extract_with_selectors(soup, 'team_region')
            
            # Parse roster and matches
            roster = self._parse_team_roster_enhanced(soup)
            recent_matches = self._parse_recent_matches_enhanced(soup)
            
            # Extract team stats
            team_stats = self._extract_team_stats_enhanced(soup)
            
            return TeamData(
                team_name=team_name,
                team_id=team_id,
                logo_url=logo_url,
                region=region,
                roster=roster,
                recent_matches=recent_matches,
                team_stats=team_stats,
                team_url=team_url,
                scraped_at=datetime.now().isoformat()
            )
            
        except Exception as e:
            self.logger.error(f"Error parsing team info from {team_url}: {e}")
            return None
    
    def _extract_team_stats_enhanced(self, soup: BeautifulSoup) -> Dict:
        """Enhanced team statistics extraction."""
        stats = {}
        
        # Look for various stat elements
        stat_selectors = [
            '[class*="stat"]',
            '[class*="rating"]',
            '[class*="winrate"]',
            '.team-summary-stats'
        ]
        
        for selector in stat_selectors:
            elements = soup.select(selector)
            for element in elements:
                text = element.get_text(strip=True).lower()
                
                # Extract win rate
                if 'win' in text and '%' in text:
                    match = re.search(r'(\d+(?:\.\d+)?)%', text)
                    if match:
                        stats['win_rate'] = match.group(1) + '%'
                
                # Extract rating
                if 'rating' in text:
                    match = re.search(r'(\d+(?:\.\d+)?)', text)
                    if match:
                        stats['rating'] = float(match.group(1))
        
        return stats
    
    def save_team_data_enhanced(self, team_data: TeamData) -> Optional[str]:
        """Enhanced team data saving with validation."""
        if not team_data.team_name:
            self.logger.error("Cannot save team data without team name")
            return None
        
        # Clean team name for filename
        clean_name = re.sub(r'[^\w\s-]', '', team_data.team_name).strip()
        clean_name = re.sub(r'[-\s]+', '_', clean_name).lower()
        
        if not clean_name:
            clean_name = f"team_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        filename = f"{clean_name}.json"
        filepath = self.raw_data_dir / "teams" / filename
        
        try:
            # Convert dataclass to dict, handling nested dataclasses
            team_dict = asdict(team_data)
            
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(team_dict, f, indent=2, ensure_ascii=False)
            
            self.logger.info(f"✅ Team data saved: {filepath}")
            return str(filepath)
            
        except Exception as e:
            self.logger.error(f"❌ Failed to save team data: {e}")
            return None
        
    def scrape_tournament_info(self, tournament_url: str) -> Optional[Tournament]:
        """Scrape tournament page for high-level information."""
        soup = self._make_request(tournament_url)
        if not soup:
            return None

        try:
            tournament_name = soup.select_one('h1').get_text(strip=True) if soup.select_one('h1') else "Unknown"
            tournament_id = tournament_url.split('/')[-1] if '/' in tournament_url else 'unknown'

            # Extract start and end date
            date_elem = soup.select_one('.event-header-date')  # adjust if needed
            if date_elem:
                date_text = date_elem.get_text(strip=True)
                dates = date_text.split('–') if '–' in date_text else [date_text]
                start_date = dates[0].strip()
                end_date = dates[1].strip() if len(dates) > 1 else None
            else:
                start_date = end_date = None

            # Extract region or location
            region_elem = soup.select_one('.event-header-region')  # adjust if needed
            region = region_elem.get_text(strip=True) if region_elem else None

            # Extract prize pool
            prize_elem = soup.select_one('.event-prize')  # adjust if needed
            prize_pool = prize_elem.get_text(strip=True) if prize_elem else None

            # Extract team names (can be expanded to links later)
            teams = []
            team_elements = soup.select('a[href*="/team/"]')
            for team in team_elements:
                name = team.get_text(strip=True)
                if name and name not in teams:
                    teams.append(name)

            return Tournament(
                tournament_name=tournament_name,
                tournament_id=tournament_id,
                start_date=start_date,
                end_date=end_date,
                region=region,
                prize_pool=prize_pool,
                participating_teams=teams,
                tournament_url=tournament_url,
                scraped_at=datetime.now().isoformat()
            )
        except Exception as e:
            self.logger.error(f"Failed to parse tournament from {tournament_url}: {e}")
            return None

    def save_tournament_data(self, tournament: Tournament) -> Optional[str]:
        """Save tournament data to JSON."""
        clean_name = re.sub(r'[^\w\s-]', '', tournament.tournament_name).strip()
        clean_name = re.sub(r'[-\s]+', '_', clean_name).lower()

        if not clean_name:
            clean_name = f"tournament_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        filename = f"{clean_name}.json"
        filepath = self.raw_data_dir / "tournaments" / filename

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(asdict(tournament), f, indent=2, ensure_ascii=False)
            self.logger.info(f"✅ Tournament data saved: {filepath}")
            return str(filepath)
        except Exception as e:
            self.logger.error(f"❌ Failed to save tournament data: {e}")
            return None
        
    def get_vct_tournament_urls(self, max_pages: int = 5) -> List[str]:
        vct_urls = []
        base_event_url = "https://www.vlr.gg/event"
        
        for page in range(1, max_pages + 1):
            url = f"{base_event_url}?page={page}"
            
            soup = self._make_request(url)
            if not soup:
                time.sleep(10)  # wait and try next page
                continue
            
            event_blocks = soup.select('a.event-item')
            for block in event_blocks:
                href = block.get('href', '')
                name = block.get_text(strip=True).lower()

                if any(kw in name for kw in ['vct', 'valorant champions tour', 'masters', 'champions']):
                    full_url = urljoin(self.base_url, href)
                    vct_urls.append(full_url)

            time.sleep(5)  # sleep between pages to avoid rate limit

        return list(set(vct_urls))

    def scrape_multiple_teams_parallel(self, team_urls: List[str]) -> Dict[str, str]:
        """Scrape multiple teams in parallel with thread pool."""
        results = {}
        failed_urls = []
        
        print(f"\n🏢 Scraping {len(team_urls)} teams in parallel (max {self.max_workers} workers)...")
        
        def scrape_single_team(team_url: str) -> Tuple[str, Optional[TeamData]]:
            """Helper function for parallel execution."""
            team_data = self.scrape_team_info(team_url)
            return team_url, team_data
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all tasks
            future_to_url = {executor.submit(scrape_single_team, url): url for url in team_urls}
            
            # Process completed tasks
            for i, future in enumerate(as_completed(future_to_url), 1):
                team_url = future_to_url[future]
                
                try:
                    url, team_data = future.result()
                    
                    if team_data:
                        filepath = self.save_team_data_enhanced(team_data)
                        if filepath:
                            results[team_data.team_name] = filepath
                            print(f"  ✅ ({i}/{len(team_urls)}) {team_data.team_name}")
                        else:
                            failed_urls.append(team_url)
                            print(f"  ❌ ({i}/{len(team_urls)}) Failed to save: {team_url}")
                    else:
                        failed_urls.append(team_url)
                        print(f"  ❌ ({i}/{len(team_urls)}) Failed to scrape: {team_url}")
                        
                except Exception as e:
                    failed_urls.append(team_url)
                    print(f"  ❌ ({i}/{len(team_urls)}) Error: {e}")
                    self.logger.error(f"Error processing {team_url}: {e}")
        
        # Log results
        if failed_urls:
            self.logger.warning(f"Failed to scrape {len(failed_urls)} teams: {failed_urls}")
        
        return results
    
    def get_scraping_summary(self) -> Dict:
        """Get summary of all scraped data."""
        team_files = list((self.raw_data_dir / "teams").glob("*.json"))
        
        summary = {
            'total_teams_scraped': len(team_files),
            'scraping_date': datetime.now().isoformat(),
            'cache_stats': {
                'cache_dir': str(self.project_root / "cache"),
                'cached_files': len(list((self.project_root / "cache").glob("*.json")))
            },
            'files': [file.name for file in team_files]
        }
        
        return summary

# Enhanced usage example
if __name__ == "__main__":
    
    # Configuration
    scraper = VLRScraper(
        delay=0.8,  # Reduced delay for better performance
        max_workers=4,  # Parallel processing
        cache_duration_hours=6,  # Cache for 6 hours
        retry_attempts=3
    )

    # # Example: scrape a single tournament
    # tournament_url = "https://www.vlr.gg/event/1700/vct-2025-masters-shanghai"
    # tournament = scraper.scrape_tournament_info(tournament_url)
    # if tournament:
    #     scraper.save_tournament_data(tournament)

    vct_urls = scraper.get_vct_tournament_urls(max_pages=3)

    print(f"📋 Found {len(vct_urls)} VCT tournaments.")
    for url in vct_urls:
        tournament = scraper.scrape_tournament_info(url)
        if tournament:
            scraper.save_tournament_data(tournament)


    
    print("=== Enhanced VCT Teams Data Collection ===")
    print(f"Project directory: {scraper.project_root}")
    print(f"Team data will be saved to: {scraper.raw_data_dir / 'teams'}")
    print(f"Using cache: {scraper.project_root / 'cache'}")
    
    # Extended team URLs (you can add more)
    team_urls = [
        # EMEA Teams
        "https://www.vlr.gg/team/1001/team-heretics",
        "https://www.vlr.gg/team/2593/fnatic", 
        "https://www.vlr.gg/team/397/bbl-esports",
        "https://www.vlr.gg/team/1184/fut-esports",
        "https://www.vlr.gg/team/2059/team-vitality",
        "https://www.vlr.gg/team/14419/giantx",
        "https://www.vlr.gg/team/12694/gentle-mates",
        "https://www.vlr.gg/team/474/team-liquid",
        "https://www.vlr.gg/team/4915/natus-vincere",
        "https://www.vlr.gg/team/8877/karmine-corp",
        "https://www.vlr.gg/team/7035/koi",
        "https://www.vlr.gg/team/11479/apeks",
        
        # Americas Teams (examples)
        "https://www.vlr.gg/team/5248/sentinels",
        "https://www.vlr.gg/team/188/cloud9",
        "https://www.vlr.gg/team/5987/100-thieves",
        
        # APAC Teams (examples)  
        "https://www.vlr.gg/team/8127/paper-rex",
        "https://www.vlr.gg/team/6199/drx"
    ]
    
    # Scrape all teams with parallel processing
    start_time = time.time()
    results = scraper.scrape_multiple_teams_parallel(team_urls)
    end_time = time.time()
    
    # Enhanced summary
    print(f"\n📋 Scraping Summary:")
    print(f"  📊 Total teams attempted: {len(team_urls)}")
    print(f"  ✅ Successfully scraped: {len(results)}")
    print(f"  ❌ Failed: {len(team_urls) - len(results)}")
    print(f"  ⏱️  Total time: {end_time - start_time:.2f} seconds")
    
    if results:
        print(f"\n📁 Successfully scraped teams:")
        for team_name, filepath in results.items():
            print(f"  📄 {team_name}")
    
    # Get and save scraping summary
    summary = scraper.get_scraping_summary()
    summary_file = scraper.raw_data_dir / "scraping_summary.json"
    
    with open(summary_file, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    
    print(f"\n📂 Data locations:")
    print(f"  📁 Team files: {scraper.raw_data_dir / 'teams'}")
    print(f"  📄 Summary: {summary_file}")
    print(f"  📄 Logs: {scraper.logs_dir}")
    print(f"  🗄️  Cache: {scraper.project_root / 'cache'}")
    
    print(f"\n🎉 Scraping completed! Check the files above for your data.")