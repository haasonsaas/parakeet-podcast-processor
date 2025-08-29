"""Audio transcription using Whisper and Parakeet."""

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Any
import whisper

from .database import P3Database

# Optional Parakeet MLX support
try:
    from parakeet_mlx import from_pretrained as parakeet_from_pretrained
    PARAKEET_AVAILABLE = True
except ImportError:
    PARAKEET_AVAILABLE = False


class AudioTranscriber:
    def __init__(self, db: P3Database, whisper_model: str = "base", 
                 use_parakeet: bool = False, parakeet_model: str = "mlx-community/parakeet-tdt-0.6b-v2"):
        self.db = db
        self.whisper_model = whisper_model
        self.use_parakeet = use_parakeet
        self.parakeet_model = parakeet_model
        self.whisper = None
        self.parakeet = None
        
    def _load_whisper(self):
        """Lazy load Whisper model."""
        if self.whisper is None:
            print(f"Loading Whisper model: {self.whisper_model}")
            self.whisper = whisper.load_model(self.whisper_model)

    def _load_parakeet(self):
        """Lazy load Parakeet MLX model."""
        if self.parakeet is None and PARAKEET_AVAILABLE:
            print(f"Loading Parakeet model: {self.parakeet_model}")
            self.parakeet = parakeet_from_pretrained(self.parakeet_model)

    def transcribe_with_whisper(self, audio_path: str) -> Dict[str, Any]:
        """Transcribe audio using OpenAI Whisper."""
        self._load_whisper()
        
        try:
            result = self.whisper.transcribe(
                audio_path,
                word_timestamps=True,
                verbose=False
            )
            
            # Convert Whisper output to our format
            segments = []
            for segment in result.get('segments', []):
                segments.append({
                    'start': segment.get('start', 0),
                    'end': segment.get('end', 0),
                    'text': segment.get('text', '').strip(),
                    'speaker': None,  # Whisper doesn't do speaker detection
                    'confidence': segment.get('no_speech_prob', 0.0)
                })
            
            return {
                'segments': segments,
                'language': result.get('language'),
                'text': result.get('text', ''),
                'provider': 'whisper'
            }
            
        except Exception as e:
            print(f"Whisper transcription failed: {e}")
            return None

    def transcribe_with_parakeet(self, audio_path: str) -> Optional[Dict[str, Any]]:
        """Transcribe audio using Nvidia Parakeet MLX."""
        if not PARAKEET_AVAILABLE:
            print("Parakeet MLX not available, falling back to Whisper")
            return self.transcribe_with_whisper(audio_path)
        
        self._load_parakeet()
        
        try:
            result = self.parakeet.transcribe(audio_path)
            
            # Convert Parakeet output to our format
            segments = []
            for sentence in result.sentences:
                segments.append({
                    'start': sentence.start,
                    'end': sentence.end,
                    'text': sentence.text.strip(),
                    'speaker': None,  # Parakeet doesn't do speaker identification
                    'confidence': 1.0  # Parakeet doesn't provide confidence scores
                })
            
            return {
                'segments': segments,
                'language': 'en',  # Parakeet is English-only
                'text': result.text,
                'provider': 'parakeet-mlx'
            }
            
        except Exception as e:
            print(f"Parakeet transcription failed: {e}")
            print("Falling back to Whisper")
            return self.transcribe_with_whisper(audio_path)

    def transcribe_episode(self, episode_id: int, skip_errors: bool = False) -> bool:
        """Transcribe a single episode and store results."""
        # Get episode from database directly
        episode = self.db.get_episode_by_id(episode_id)
        
        if not episode:
            print(f"Episode {episode_id} not found")
            return False
        
        # Skip if already transcribed or processed
        if episode['status'] in ['transcribed', 'processed']:
            print(f"Episode {episode_id} already transcribed/processed")
            return True
        
        # Skip if has errors and skip_errors is True
        if skip_errors and episode.get('error_count', 0) > 0:
            print(f"Skipping episode {episode_id} due to previous errors")
            return False

        if not episode['file_path'] or not Path(episode['file_path']).exists():
            error_msg = f"Audio file not found: {episode.get('file_path')}"
            print(f"Error: {error_msg}")
            self.db.record_episode_error(episode_id, error_msg)
            return False

        print(f"Transcribing: {episode['title']}")
        
        try:
            # Choose transcription method
            if self.use_parakeet:
                result = self.transcribe_with_parakeet(episode['file_path'])
            else:
                result = self.transcribe_with_whisper(episode['file_path'])
            
            if not result:
                error_msg = "Transcription returned no results"
                self.db.record_episode_error(episode_id, error_msg)
                return False

            # Store transcript segments in database
            self.db.add_transcript_segments(episode_id, result['segments'])
            
            # Update episode status
            self.db.update_episode_status(episode_id, 'transcribed')
            
            print(f"✓ Transcribed: {episode['title']}")
            return True
            
        except Exception as e:
            error_msg = f"Transcription failed: {str(e)}"
            print(f"Error: {error_msg}")
            self.db.record_episode_error(episode_id, error_msg)
            return False

    def transcribe_all_pending(self, skip_errors: bool = False, max_retries: int = 3) -> int:
        """Transcribe all episodes with 'downloaded' status."""
        episodes = self.db.get_episodes_by_status('downloaded')
        transcribed_count = 0
        failed_count = 0
        
        for episode in episodes:
            # Skip episodes that have exceeded max retries
            if episode.get('error_count', 0) >= max_retries:
                print(f"Skipping {episode['title']} - exceeded max retries ({max_retries})")
                continue
                
            if self.transcribe_episode(episode['id'], skip_errors=skip_errors):
                transcribed_count += 1
            else:
                failed_count += 1
        
        if failed_count > 0:
            print(f"\n⚠ {failed_count} episodes failed transcription")
            print("Use 'p3 errors' to see details or 'p3 retry' to retry failed episodes")
            
        return transcribed_count

    def get_full_transcript(self, episode_id: int) -> str:
        """Get the full transcript text for an episode."""
        segments = self.db.get_transcripts_for_episode(episode_id)
        return "\n".join(segment['text'] for segment in segments)

    def export_transcript(self, episode_id: int, format: str = "txt") -> str:
        """Export transcript in various formats."""
        segments = self.db.get_transcripts_for_episode(episode_id)
        
        if format == "txt":
            return "\n".join(segment['text'] for segment in segments)
        
        elif format == "srt":
            srt_content = []
            for i, segment in enumerate(segments, 1):
                start_time = self._seconds_to_srt_time(segment['timestamp_start'] or 0)
                end_time = self._seconds_to_srt_time(segment['timestamp_end'] or 0)
                srt_content.append(f"{i}\n{start_time} --> {end_time}\n{segment['text']}\n")
            return "\n".join(srt_content)
        
        elif format == "json":
            return json.dumps(segments, indent=2, default=str)
        
        else:
            raise ValueError(f"Unsupported format: {format}")

    def _seconds_to_srt_time(self, seconds: float) -> str:
        """Convert seconds to SRT timestamp format."""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millisecs = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millisecs:03d}"
