"""Database layer using DuckDB for P³ storage."""

import os
from datetime import datetime
from typing import Dict, List, Optional, Any
import duckdb
from pathlib import Path


class P3Database:
    def __init__(self, db_path: str = "data/p3.duckdb"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(str(self.db_path))
        self._initialize_schema()

    def _initialize_schema(self):
        """Create database schema if not exists."""
        self.conn.execute("""
            CREATE SEQUENCE IF NOT EXISTS podcast_id_seq START 1
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS podcasts (
                id INTEGER PRIMARY KEY DEFAULT nextval('podcast_id_seq'),
                title VARCHAR NOT NULL,
                rss_url VARCHAR UNIQUE NOT NULL,
                category VARCHAR,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self.conn.execute("""
            CREATE SEQUENCE IF NOT EXISTS episode_id_seq START 1
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS episodes (
                id INTEGER PRIMARY KEY DEFAULT nextval('episode_id_seq'),
                podcast_id INTEGER REFERENCES podcasts(id),
                title VARCHAR NOT NULL,
                date TIMESTAMP,
                url VARCHAR UNIQUE NOT NULL,
                file_path VARCHAR,
                duration_seconds INTEGER,
                status VARCHAR DEFAULT 'downloaded', -- downloaded, transcribed, processed, failed
                error_count INTEGER DEFAULT 0,
                last_error TEXT,
                error_timestamp TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self.conn.execute("""
            CREATE SEQUENCE IF NOT EXISTS transcript_id_seq START 1
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS transcripts (
                id INTEGER PRIMARY KEY DEFAULT nextval('transcript_id_seq'),
                episode_id INTEGER REFERENCES episodes(id),
                speaker VARCHAR,
                timestamp_start REAL,
                timestamp_end REAL,
                text TEXT NOT NULL,
                confidence REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self.conn.execute("""
            CREATE SEQUENCE IF NOT EXISTS summary_id_seq START 1
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS summaries (
                id INTEGER PRIMARY KEY DEFAULT nextval('summary_id_seq'),
                episode_id INTEGER REFERENCES episodes(id),
                key_topics JSON,
                themes JSON,
                quotes JSON,
                startups JSON,
                digest_date DATE,
                full_summary TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

    def add_podcast(self, title: str, rss_url: str, category: str = None) -> int:
        """Add new podcast feed."""
        # Get the next ID first
        next_id = self.conn.execute("SELECT nextval('podcast_id_seq')").fetchone()[0]
        self.conn.execute(
            "INSERT INTO podcasts (id, title, rss_url, category) VALUES (?, ?, ?, ?)",
            (next_id, title, rss_url, category)
        )
        return next_id

    def get_podcast_by_url(self, rss_url: str) -> Optional[Dict[str, Any]]:
        """Get podcast by RSS URL."""
        result = self.conn.execute(
            "SELECT * FROM podcasts WHERE rss_url = ?", (rss_url,)
        ).fetchone()
        if result:
            return {
                "id": result[0],
                "title": result[1],
                "rss_url": result[2],
                "category": result[3],
                "created_at": result[4]
            }
        return None

    def add_episode(self, podcast_id: int, title: str, date: datetime, url: str, 
                   file_path: str = None) -> int:
        """Add new episode."""
        next_id = self.conn.execute("SELECT nextval('episode_id_seq')").fetchone()[0]
        self.conn.execute("""
            INSERT INTO episodes (id, podcast_id, title, date, url, file_path) 
            VALUES (?, ?, ?, ?, ?, ?)
        """, (next_id, podcast_id, title, date, url, file_path))
        return next_id

    def episode_exists(self, url: str) -> bool:
        """Check if episode already exists."""
        result = self.conn.execute(
            "SELECT 1 FROM episodes WHERE url = ?", (url,)
        ).fetchone()
        return result is not None

    def get_episodes_by_status(self, status: str) -> List[Dict[str, Any]]:
        """Get episodes by processing status."""
        results = self.conn.execute("""
            SELECT e.*, p.title as podcast_title 
            FROM episodes e 
            JOIN podcasts p ON e.podcast_id = p.id 
            WHERE e.status = ?
            ORDER BY e.date DESC
        """, (status,)).fetchall()
        
        episodes = []
        for row in results:
            episodes.append({
                "id": row[0],
                "podcast_id": row[1],
                "title": row[2],
                "date": row[3],
                "url": row[4],
                "file_path": row[5],
                "duration_seconds": row[6],
                "status": row[7],
                "created_at": row[8],
                "podcast_title": row[9]
            })
        return episodes

    def update_episode_status(self, episode_id: int, status: str):
        """Update episode processing status."""
        self.conn.execute(
            "UPDATE episodes SET status = ? WHERE id = ?",
            (status, episode_id)
        )

    def add_transcript_segments(self, episode_id: int, segments: List[Dict[str, Any]]):
        """Add transcript segments for an episode."""
        for segment in segments:
            self.conn.execute("""
                INSERT INTO transcripts (episode_id, speaker, timestamp_start, timestamp_end, text, confidence) 
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                episode_id,
                segment.get("speaker"),
                segment.get("start"),
                segment.get("end"),
                segment.get("text"),
                segment.get("confidence")
            ))

    def get_transcripts_for_episode(self, episode_id: int) -> List[Dict[str, Any]]:
        """Get all transcript segments for an episode."""
        results = self.conn.execute("""
            SELECT * FROM transcripts WHERE episode_id = ? 
            ORDER BY timestamp_start
        """, (episode_id,)).fetchall()
        
        transcripts = []
        for row in results:
            transcripts.append({
                "id": row[0],
                "episode_id": row[1],
                "speaker": row[2],
                "timestamp_start": row[3],
                "timestamp_end": row[4],
                "text": row[5],
                "confidence": row[6],
                "created_at": row[7]
            })
        return transcripts

    def add_summary(self, episode_id: int, key_topics: List[str], themes: List[str],
                   quotes: List[str], startups: List[str], full_summary: str,
                   digest_date: datetime = None):
        """Add episode summary."""
        if digest_date is None:
            digest_date = datetime.now().date()
            
        import json
        self.conn.execute("""
            INSERT INTO summaries (episode_id, key_topics, themes, quotes, startups, full_summary, digest_date) 
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            episode_id,
            json.dumps(key_topics),
            json.dumps(themes),
            json.dumps(quotes),
            json.dumps(startups),
            full_summary,
            digest_date
        ))

    def get_summaries_by_date(self, date: datetime) -> List[Dict[str, Any]]:
        """Get all summaries for a specific date."""
        results = self.conn.execute("""
            SELECT s.*, e.title as episode_title, p.title as podcast_title
            FROM summaries s
            JOIN episodes e ON s.episode_id = e.id
            JOIN podcasts p ON e.podcast_id = p.id
            WHERE s.digest_date = ?
            ORDER BY p.title, e.title
        """, (date.date(),)).fetchall()
        
        summaries = []
        for row in results:
            import json
            summaries.append({
                "id": row[0],
                "episode_id": row[1],
                "key_topics": json.loads(row[2]),
                "themes": json.loads(row[3]),
                "quotes": json.loads(row[4]),
                "startups": json.loads(row[5]),
                "digest_date": row[6],
                "full_summary": row[7],
                "created_at": row[8],
                "episode_title": row[9],
                "podcast_title": row[10]
            })
        return summaries

    def mark_episode_as_processed(self, episode_id: int, reason: str = None):
        """Mark an episode as processed, typically used for error recovery."""
        if reason:
            self.conn.execute("""
                UPDATE episodes 
                SET status = 'processed', 
                    last_error = ?
                WHERE id = ?
            """, (f"Manually marked as processed: {reason}", episode_id))
        else:
            self.conn.execute(
                "UPDATE episodes SET status = 'processed' WHERE id = ?",
                (episode_id,)
            )
    
    def record_episode_error(self, episode_id: int, error: str):
        """Record an error for an episode."""
        self.conn.execute("""
            UPDATE episodes 
            SET error_count = error_count + 1,
                last_error = ?,
                error_timestamp = CURRENT_TIMESTAMP,
                status = 'failed'
            WHERE id = ?
        """, (error, episode_id))
    
    def reset_episode_errors(self, episode_id: int):
        """Reset error count and status for retry."""
        self.conn.execute("""
            UPDATE episodes 
            SET error_count = 0,
                last_error = NULL,
                error_timestamp = NULL,
                status = 'downloaded'
            WHERE id = ?
        """, (episode_id,))
    
    def get_error_episodes(self) -> List[Dict[str, Any]]:
        """Get all episodes with errors."""
        results = self.conn.execute("""
            SELECT e.*, p.title as podcast_title 
            FROM episodes e 
            JOIN podcasts p ON e.podcast_id = p.id 
            WHERE e.error_count > 0 OR e.status = 'failed'
            ORDER BY e.error_timestamp DESC
        """).fetchall()
        
        episodes = []
        for row in results:
            episodes.append({
                "id": row[0],
                "podcast_id": row[1],
                "title": row[2],
                "date": row[3],
                "url": row[4],
                "file_path": row[5],
                "duration_seconds": row[6],
                "status": row[7],
                "error_count": row[8],
                "last_error": row[9],
                "error_timestamp": row[10],
                "created_at": row[11],
                "podcast_title": row[12]
            })
        return episodes
    
    def get_episode_by_id(self, episode_id: int) -> Optional[Dict[str, Any]]:
        """Get episode by ID."""
        result = self.conn.execute("""
            SELECT e.*, p.title as podcast_title 
            FROM episodes e 
            JOIN podcasts p ON e.podcast_id = p.id 
            WHERE e.id = ?
        """, (episode_id,)).fetchone()
        
        if result:
            return {
                "id": result[0],
                "podcast_id": result[1],
                "title": result[2],
                "date": result[3],
                "url": result[4],
                "file_path": result[5],
                "duration_seconds": result[6],
                "status": result[7],
                "error_count": result[8],
                "last_error": result[9],
                "error_timestamp": result[10],
                "created_at": result[11],
                "podcast_title": result[12]
            }
        return None

    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()
