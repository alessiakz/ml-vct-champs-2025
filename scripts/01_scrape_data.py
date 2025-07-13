import requests
from bs4 import BeautifulSoup
import json
import time
import logging
from datetime import datetime
from typing import Dict, List, Optional
import re
import os
from pathlib import Path
from urllib.parse import urljoin, urlparse

class VLRScraper:
    """
    Web scraper for vlr.gg to collect VCT match data, team stats, and player information.
    """
    
    def __init__(self, base_url: str = "https://www.vlr.gg", delay: float = 1.5):
        self.base_url = base_url
        self.delay = delay
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })
        
        # Setup project directories
        self.setup_directories()
        
        # Setup logging
        self.setup_logging()
        
    def setup_directories(self):
        """Create necessary directories for the project structure."""
        self.project_root = Path.cwd()
        self.data_dir = self.project_root / "data"
        self.raw_data_dir = self.data_dir / "raw"
        self.processed_data_dir = self.data_dir / "processed"
        self.logs_dir = self.project_root / "logs"
        
        # Create directory structure
        directories = [
            self.raw_data_dir / "matches",
            self.raw_data_dir / "teams",
            self.raw_data_dir / "players",
            self.raw_data_dir / "tournaments",
            self.processed_data_dir / "features",
            self.processed_data_dir / "training",
            self.logs_dir
        ]
        
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
            
    def setup_logging(self):
        """Setup logging configuration."""
        log_file = self.logs_dir / "scraping.log"
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
        
    def _make_request(self, url: str) -> Optional[BeautifulSoup]:
        """Make a request with error handling and rate limiting."""
        try:
            self.logger.info(f"Making request to: {url}")
            time.sleep(self.delay)  # Rate limiting
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            return BeautifulSoup(response.content, 'html.parser')
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Error fetching {url}: {e}")
            return None
    
    def scrape_team_info(self, team_url: str) -> Optional[Dict]:
        """
        Scrape team information and recent results.
        
        Args:
            team_url: URL to the team's page
            
        Returns:
            Dictionary with team information
        """
        soup = self._make_request(team_url)
        if not soup:
            return None
            
        try:
            # Team name and region - try multiple selectors
            team_name = self._extract_team_name(soup)
            if not team_name:
                self.logger.warning(f"Could not extract team name from {team_url}")
                # Try to extract from URL as fallback
                team_name = team_url.split('/')[-1].replace('-', ' ').title()
            
            self.logger.info(f"Processing team: {team_name}")
            
            # Team logo
            logo_url = self._extract_team_logo(soup)
            
            # Team region/country
            region = self._extract_team_region(soup)
            
            # Recent matches
            recent_matches = self._parse_team_recent_matches(soup)
            
            # Current roster - this is the main focus
            self.logger.info(f"Extracting roster for {team_name}...")
            roster = self._parse_team_roster(soup)
            self.logger.info(f"Found {len(roster)} players for {team_name}")
            
            # Team stats if available
            team_stats = self._extract_team_stats(soup)
            
            return {
                'team_name': team_name,
                'logo_url': logo_url,
                'region': region,
                'recent_matches': recent_matches,
                'roster': roster,
                'team_stats': team_stats,
                'team_url': team_url,
                'scraped_at': datetime.now().isoformat()
            }
            
        except Exception as e:
            self.logger.error(f"Error parsing team info from {team_url}: {e}")
            return None
    
    def _extract_team_name(self, soup) -> Optional[str]:
        """Extract team name using multiple selector strategies."""
        selectors = [
            'h1.wf-title',
            'h1.team-header-name',
            '.team-header-name',
            'h1',
            '.wf-title'
        ]
        
        for selector in selectors:
            element = soup.select_one(selector)
            if element:
                name = element.get_text(strip=True)
                if name and name != "Unknown":
                    return name
        
        return None
    
    def _extract_team_logo(self, soup) -> Optional[str]:
        """Extract team logo URL."""
        selectors = [
            'img.team-header-logo',
            '.team-header-logo img',
            '.wf-avatar img'
        ]
        
        for selector in selectors:
            element = soup.select_one(selector)
            if element and element.get('src'):
                return urljoin(self.base_url, element['src'])
        
        return None
    
    def _extract_team_region(self, soup) -> Optional[str]:
        """Extract team region/country."""
        selectors = [
            '.team-header-country',
            '.flag',
            '[class*="country"]',
            '[class*="flag"]'
        ]
        
        for selector in selectors:
            element = soup.select_one(selector)
            if element:
                region = element.get_text(strip=True)
                if region:
                    return region
        
        return None
    
    def _extract_team_stats(self, soup) -> Dict:
        """Extract team statistics if available."""
        stats = {}
        
        # Look for win rate, recent form, etc.
        stat_elements = soup.select('[class*="stat"]')
        for element in stat_elements:
            text = element.get_text(strip=True)
            if 'win' in text.lower() or '%' in text:
                stats['win_rate'] = text
                break
        
        return stats
    
    def _parse_team_recent_matches(self, soup) -> List[Dict]:
        """Parse recent matches from team page with improved selectors."""
        matches = []
        
        # Try multiple selectors for match elements
        match_selectors = [
            '.wf-card',
            '.match-item',
            '[class*="match"]',
            '.mod-color'
        ]
        
        for selector in match_selectors:
            match_elements = soup.select(selector)
            if match_elements:
                self.logger.info(f"Found {len(match_elements)} match elements with selector: {selector}")
                break
        
        for match_elem in match_elements[:10]:  # Get last 10 matches
            try:
                match_data = self._parse_single_team_match(match_elem)
                if match_data:
                    matches.append(match_data)
                    
            except Exception as e:
                self.logger.error(f"Error parsing recent match: {e}")
                continue
                
        return matches
    
    def _parse_single_team_match(self, match_elem) -> Optional[Dict]:
        """Parse a single match element from team page."""
        try:
            # Extract opponent
            opponent_selectors = [
                '.text-of',
                '.team-name',
                '[class*="team"]',
                'a[href*="/team"]'
            ]
            
            opponent = None
            for selector in opponent_selectors:
                elem = match_elem.select_one(selector)
                if elem:
                    opponent = elem.get_text(strip=True)
                    break
            
            # Extract result/score
            score_selectors = [
                '.match-item-vs-score',
                '[class*="score"]',
                '.mod-win',
                '.mod-loss'
            ]
            
            result = None
            for selector in score_selectors:
                elem = match_elem.select_one(selector)
                if elem:
                    result = elem.get_text(strip=True)
                    break
            
            # Extract date if available
            date_selectors = [
                '.match-item-date',
                '[class*="date"]',
                '.moment-tz-convert'
            ]
            
            match_date = None
            for selector in date_selectors:
                elem = match_elem.select_one(selector)
                if elem:
                    match_date = elem.get_text(strip=True)
                    break
            
            if opponent or result:
                return {
                    'opponent': opponent or 'Unknown',
                    'result': result or 'Unknown',
                    'date': match_date
                }
            
            return None
            
        except Exception as e:
            self.logger.error(f"Error parsing single match: {e}")
            return None
        
    def _parse_team_roster(self, soup) -> List[Dict]:
        """Robust VLR.gg player roster parser with role classification, prioritizing 'inactive'."""
        roster = []

        # Save page HTML for debugging
        try:
            debug_file = self.logs_dir / "team_page_debug.html"
            with open(debug_file, 'w', encoding='utf-8') as f:
                f.write(soup.prettify())
        except Exception as e:
            self.logger.error(f"Could not save HTML: {e}")

        player_links = soup.select('.team-roster-item a[href*="/player/"]')
        self.logger.info(f"Found {len(player_links)} player links in roster.")

        role_keywords = {
            'inactive': 'inactive',
            'performance coach': 'performance coach',
            'sub': 'sub',
            'coach': 'coach',
            'assistant coach': 'assistant coach',
            'manager': 'manager',
            'analyst': 'analyst',
        }

        for link in player_links:
            try:
                player_name = link.get_text(strip=True)
                player_url = urljoin(self.base_url, link['href'])
                player_id = link['href'].split('/')[-1]

                # Normalize surrounding text and name
                parent = link.find_parent(class_='team-roster-item')
                role_text = " ".join(parent.stripped_strings).lower() if parent else ""
                normalized_name = player_name.lower()

                # Role detection, prioritizing 'inactive'
                role = "active player"

                if 'inactive' in normalized_name or 'inactive' in role_text:
                    role = 'inactive'
                else:
                    for keyword, mapped_role in role_keywords.items():
                        if keyword in role_text:
                            role = mapped_role
                            break

                if player_name and len(player_name) > 1:
                    roster.append({
                        'player_name': player_name,
                        'player_url': player_url,
                        'player_id': player_id,
                        'role': role
                    })

            except Exception as e:
                self.logger.error(f"Error parsing player link: {e}")

        if not roster:
            self.logger.warning("⚠️ No players found in roster section!")

        return roster

    def save_team_data(self, team_data: Dict, team_name: str = None) -> str:
        """Save team data to individual JSON file."""
        if team_name is None:
            team_name = team_data.get('team_name', 'unknown_team')
        
        if not team_name or team_name == 'unknown_team':
            self.logger.warning("No valid team name found, using timestamp")
            team_name = f"team_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        # Clean team name for filename
        clean_name = re.sub(r'[^\w\s-]', '', team_name).strip()
        clean_name = re.sub(r'[-\s]+', '_', clean_name).lower()
        
        # Ensure filename is not empty
        if not clean_name:
            clean_name = f"team_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        filename = f"{clean_name}.json"
        filepath = self.raw_data_dir / "teams" / filename
        
        try:
            self._save_json(team_data, filepath)
            self.logger.info(f"✅ Team data saved: {filepath}")
            return str(filepath)
        except Exception as e:
            self.logger.error(f"❌ Failed to save team data: {e}")
            return ""
    
    def _save_json(self, data: Dict, filepath: Path) -> None:
        """Save data to JSON file with error handling."""
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.logger.error(f"Error saving data to {filepath}: {e}")
            raise
    
    def scrape_multiple_teams(self, team_urls: List[str]) -> Dict[str, str]:
        """
        Scrape multiple teams and save each to individual JSON files.
        
        Args:
            team_urls: List of team URLs to scrape
            
        Returns:
            Dictionary mapping team names to saved file paths
        """
        results = {}
        
        print(f"\n🏢 Scraping {len(team_urls)} teams...")
        
        for i, team_url in enumerate(team_urls, 1):
            print(f"\n📊 Scraping team {i}/{len(team_urls)}: {team_url}")
            
            try:
                team_data = self.scrape_team_info(team_url)
                
                if team_data:
                    team_name = team_data.get('team_name', f'team_{i}')
                    filepath = self.save_team_data(team_data)
                    
                    if filepath:
                        results[team_name] = filepath
                        print(f"  ✅ Saved: {team_name} -> {Path(filepath).name}")
                    else:
                        print(f"  ❌ Failed to save data for {team_name}")
                else:
                    print(f"  ❌ Failed to scrape team data from {team_url}")
                    
            except Exception as e:
                print(f"  ❌ Error scraping {team_url}: {e}")
                self.logger.error(f"Error scraping team {team_url}: {e}")
                continue
        
        return results

# Usage example for team scraping
if __name__ == "__main__":
    scraper = VLRScraper()
    
    print("=== VCT Teams Data Collection ===")
    print(f"Project directory: {scraper.project_root}")
    print(f"Team data will be saved to: {scraper.raw_data_dir / 'teams'}")
    
    # EMEA Teams
    team_urls = [
        # EMEA
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
        "https://www.vlr.gg/team/11479/apeks"
        # Add APAC, AMER, and China team URLs here
    ]
    
    # Scrape all teams
    results = scraper.scrape_multiple_teams(team_urls)
    
    # Summary
    print(f"\n📋 Scraping Summary:")
    print(f"  📊 Total teams attempted: {len(team_urls)}")
    print(f"  ✅ Successfully scraped: {len(results)}")
    print(f"  ❌ Failed: {len(team_urls) - len(results)}")
    
    if results:
        print(f"\n📁 Saved team files:")
        for team_name, filepath in results.items():
            print(f"  📄 {team_name}: {Path(filepath).name}")
    
    print(f"\n📂 All team files saved in: {scraper.raw_data_dir / 'teams'}")
    print(f"📄 Check logs at: {scraper.logs_dir / 'scraping.log'}")
    
    # List all saved files
    team_files = list((scraper.raw_data_dir / "teams").glob("*.json"))
    print(f"\n📋 Total JSON files created: {len(team_files)}")
    for file in team_files:
        print(f"  📄 {file.name}")