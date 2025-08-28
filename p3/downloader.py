"""Podcast episode downloader and RSS feed processor."""

import os
import requests
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse
import feedparser
# from pydub import AudioSegment  # Disabled due to Python 3.13 compatibility
import subprocess
import tempfile

from .database import P3Database


class PodcastDownloader:
    def __init__(self, db: P3Database, data_dir: str = "data", 
                 max_episodes: int = 10, audio_format: str = "wav"):
        self.db = db
        self.data_dir = Path(data_dir)
        self.audio_dir = self.data_dir / "audio"
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self.max_episodes = max_episodes
        self.audio_format = audio_format

    def add_feed(self, name: str, url: str, category: str = None) -> int:
        """Add a new podcast feed to the database."""
        existing = self.db.get_podcast_by_url(url)
        if existing:
            return existing["id"]
        return self.db.add_podcast(name, url, category)

    def fetch_episodes(self, rss_url: str, limit: int = None) -> List[Dict]:
        """Fetch episode metadata from RSS feed."""
        if limit is None:
            limit = self.max_episodes

        try:
            feed = feedparser.parse(rss_url)
            episodes = []
            
            for entry in feed.entries[:limit]:
                # Find audio enclosure
                audio_url = None
                for enclosure in entry.get('enclosures', []):
                    if enclosure.type and 'audio' in enclosure.type:
                        audio_url = enclosure.href
                        break
                
                if not audio_url:
                    continue

                # Parse publication date
                pub_date = None
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    pub_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
                    pub_date = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)

                episodes.append({
                    'title': entry.get('title', 'Unknown Title'),
                    'url': audio_url,
                    'date': pub_date,
                    'description': entry.get('description', ''),
                    'guid': entry.get('id', audio_url)
                })
            
            return episodes
            
        except Exception as e:
            print(f"Error fetching RSS feed {rss_url}: {e}")
            return []

    def download_episode(self, episode_url: str, filename: str) -> Optional[str]:
        """Download and normalize audio episode."""
        try:
            # Download audio file
            response = requests.get(episode_url, stream=True, timeout=300)
            response.raise_for_status()
            
            # Save to temporary file first
            with tempfile.NamedTemporaryFile(delete=False, suffix='.tmp') as tmp_file:
                for chunk in response.iter_content(chunk_size=8192):
                    tmp_file.write(chunk)
                tmp_path = tmp_file.name

            # Convert and normalize with ffmpeg
            output_path = self.audio_dir / f"{filename}.{self.audio_format}"
            
            # Use ffmpeg for reliable audio processing and normalization
            cmd = [
                'ffmpeg', '-y',  # overwrite existing files
                '-i', tmp_path,
                '-ar', '16000',  # 16kHz sample rate for Whisper
                '-ac', '1',      # mono
                '-c:a', 'pcm_s16le' if self.audio_format == 'wav' else 'libmp3lame',
                '-af', 'loudnorm',  # normalize audio levels
                str(output_path)
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"FFmpeg error: {result.stderr}")
                # Fallback to pydub
                return self._fallback_conversion(tmp_path, output_path)
            
            # Clean up temp file
            os.unlink(tmp_path)
            
            return str(output_path)
            
        except Exception as e:
            print(f"Error downloading {episode_url}: {e}")
            return None

    def _fallback_conversion(self, input_path: str, output_path: Path) -> str:
        """Fallback audio conversion using ffmpeg directly."""
        try:
            # Use ffmpeg without pydub as fallback
            cmd = [
                'ffmpeg', '-y', '-i', input_path,
                '-ar', '12000', '-ac', '1',
                str(output_path)
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode == 0:
                os.unlink(input_path)
                return str(output_path)
            else:
                print(f"Fallback conversion failed: {result.stderr}")
                os.unlink(input_path)
                return None
            
        except Exception as e:
            print(f"Fallback conversion failed: {e}")
            os.unlink(input_path)
            return None

    def process_feed(self, rss_url: str) -> int:
        """Process a single RSS feed and download new episodes."""
        podcast = self.db.get_podcast_by_url(rss_url)
        if not podcast:
            print(f"Podcast not found for URL: {rss_url}")
            return 0

        episodes = self.fetch_episodes(rss_url)
        downloaded_count = 0
        
        for ep_data in episodes:
            # Skip if episode already exists
            if self.db.episode_exists(ep_data['url']):
                continue
                
            print(f"Downloading: {ep_data['title']}")
            
            # Generate safe filename
            safe_title = "".join(c for c in ep_data['title'] if c.isalnum() or c in (' ', '-', '_')).rstrip()
            filename = f"{podcast['id']}_{safe_title[:50]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            
            # Download episode
            file_path = self.download_episode(ep_data['url'], filename)
            if file_path:
                # Add to database
                self.db.add_episode(
                    podcast_id=podcast['id'],
                    title=ep_data['title'],
                    date=ep_data['date'],
                    url=ep_data['url'],
                    file_path=file_path
                )
                downloaded_count += 1
                print(f"✓ Downloaded: {ep_data['title']}")
            else:
                print(f"✗ Failed to download: {ep_data['title']}")
        
        return downloaded_count

    def fetch_all_feeds(self, feeds_config: List[Dict]) -> Dict[str, int]:
        """Process all configured RSS feeds."""
        results = {}
        
        for feed_config in feeds_config:
            name = feed_config['name']
            url = feed_config['url']
            category = feed_config.get('category')
            
            print(f"Processing feed: {name}")
            
            # Ensure podcast exists in database
            self.add_feed(name, url, category)
            
            # Process episodes
            count = self.process_feed(url)
            results[name] = count
            
            print(f"Downloaded {count} new episodes from {name}")
        
        return results
