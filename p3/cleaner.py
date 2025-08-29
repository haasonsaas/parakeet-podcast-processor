"""LLM-based transcript cleaning and summarization."""

import json
import re
from datetime import datetime, date
from typing import Dict, List, Optional, Any
import httpx

from .database import P3Database

# Optional Ollama support
try:
    import ollama
    OLLAMA_AVAILABLE = True
except ImportError:
    OLLAMA_AVAILABLE = False


class TranscriptCleaner:
    def __init__(self, db: P3Database, llm_provider: str = "openai", 
                 llm_model: str = "gpt-3.5-turbo", api_key: str = None, ollama_base_url: str = "http://localhost:11434"):
        self.db = db
        self.llm_provider = llm_provider.lower()
        self.llm_model = llm_model
        self.api_key = api_key
        self.ollama_base_url = ollama_base_url
        
        # Load API key from environment if not provided
        if not self.api_key and self.llm_provider != "ollama":
            import os
            if self.llm_provider == "openai":
                self.api_key = os.getenv("OPENAI_API_KEY")
            elif self.llm_provider == "anthropic":
                self.api_key = os.getenv("ANTHROPIC_API_KEY")

    def clean_transcript(self, raw_text: str) -> str:
        """Clean transcript by removing filler words and improving readability."""
        # Basic cleaning without LLM first
        text = raw_text
        
        # Remove common filler words
        fillers = [
            r'\b(um|uh|ah|er|hmm|like|you know|sort of|kind of)\b',
            r'\b(actually|basically|literally|obviously|definitely)\b(?=\s+\w)',
        ]
        
        for filler in fillers:
            text = re.sub(filler, '', text, flags=re.IGNORECASE)
        
        # Clean up extra whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        
        # If we have API access or Ollama, use LLM for advanced cleaning
        if self.api_key or self.llm_provider == "ollama":
            try:
                text = self._llm_clean(text)
            except Exception as e:
                print(f"LLM cleaning failed, using basic cleaning: {e}")
        
        return text

    def _llm_clean(self, text: str) -> str:
        """Use LLM to clean and improve transcript."""
        if self.llm_provider == "openai":
            return self._openai_clean(text)
        elif self.llm_provider == "anthropic":
            return self._anthropic_clean(text)
        elif self.llm_provider == "ollama":
            return self._ollama_clean(text)
        else:
            print(f"Unsupported LLM provider: {self.llm_provider}")
            return text

    def _openai_clean(self, text: str) -> str:
        """Clean transcript using OpenAI API."""
        prompt = """Clean this podcast transcript by:
1. Removing filler words (um, uh, like, you know)
2. Fixing grammar and punctuation
3. Preserving technical terms and proper nouns exactly
4. Maintaining the speaker's voice and meaning
5. Breaking into clear paragraphs

Return only the cleaned text, no additional commentary.

Transcript:
"""
        
        with httpx.Client(timeout=60.0) as client:
            response = client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": self.llm_model,
                    "messages": [
                        {"role": "system", "content": "You are an expert transcript editor."},
                        {"role": "user", "content": prompt + text}
                    ],
                    "temperature": 0.1,
                    "max_tokens": min(len(text) * 2, 4000)
                }
            )
        
        if response.status_code == 200:
            return response.json()["choices"][0]["message"]["content"].strip()
        else:
            raise Exception(f"OpenAI API error: {response.status_code}")

    def _anthropic_clean(self, text: str) -> str:
        """Clean transcript using Anthropic Claude API."""
        # Similar implementation for Claude
        # This would use the Anthropic API format
        return text  # Placeholder

    def _ollama_clean(self, text: str) -> str:
        """Clean transcript using Ollama local LLM."""
        if not OLLAMA_AVAILABLE:
            print("Ollama not available, skipping LLM cleaning")
            return text
            
        prompt = """Clean this podcast transcript by:
1. Removing filler words (um, uh, like, you know)
2. Fixing grammar and punctuation
3. Preserving technical terms and proper nouns exactly
4. Maintaining the speaker's voice and meaning
5. Breaking into clear paragraphs

Return only the cleaned text, no additional commentary.

Transcript:
""" + text

        try:
            response = ollama.chat(
                model=self.llm_model,
                messages=[
                    {"role": "system", "content": "You are an expert transcript editor."},
                    {"role": "user", "content": prompt}
                ]
            )
            return response['message']['content'].strip()
        except Exception as e:
            print(f"Ollama cleaning failed: {e}")
            return text

    def generate_summary(self, episode_id: int, skip_errors: bool = False) -> Dict[str, Any]:
        """Generate structured summary of an episode."""
        # Get episode to check status
        episode = self.db.get_episode_by_id(episode_id)
        if not episode:
            print(f"Episode {episode_id} not found")
            return None
        
        # Skip if already processed
        if episode['status'] == 'processed':
            print(f"Episode {episode_id} already processed")
            return None
        
        # Skip if has errors and skip_errors is True
        if skip_errors and episode.get('error_count', 0) > 0:
            print(f"Skipping episode {episode_id} due to previous errors")
            return None
        
        try:
            # Get transcript segments
            segments = self.db.get_transcripts_for_episode(episode_id)
            full_text = "\n".join(segment['text'] for segment in segments)
            
            if not full_text.strip():
                error_msg = "No transcript text found for episode"
                self.db.record_episode_error(episode_id, error_msg)
                return None
            
            # Clean the transcript first
            cleaned_text = self.clean_transcript(full_text)
            
            # Generate structured summary using LLM
            summary_data = self._generate_structured_summary(cleaned_text)
            
            if summary_data:
                # Store in database
                self.db.add_summary(
                    episode_id=episode_id,
                    key_topics=summary_data.get('key_topics', []),
                    themes=summary_data.get('themes', []),
                    quotes=summary_data.get('quotes', []),
                    startups=summary_data.get('startups', []),
                    full_summary=summary_data.get('summary', ''),
                    digest_date=datetime.now()
                )
                
                # Update episode status
                self.db.update_episode_status(episode_id, 'processed')
                print(f"✓ Processed: {episode['title']}")
            else:
                error_msg = "Failed to generate summary data"
                self.db.record_episode_error(episode_id, error_msg)
                print(f"Failed to generate summary for episode {episode_id}")
            
            return summary_data
            
        except Exception as e:
            error_msg = f"Summary generation failed: {str(e)}"
            print(f"Error: {error_msg}")
            self.db.record_episode_error(episode_id, error_msg)
            return None

    def _generate_structured_summary(self, text: str) -> Optional[Dict[str, Any]]:
        """Generate structured summary using LLM."""
        if not self.api_key and self.llm_provider != "ollama":
            # Fallback to simple extraction
            return self._basic_extraction(text)

        prompt = """Analyze this podcast transcript and extract structured information in JSON format:

{
  "key_topics": ["topic1", "topic2", ...],
  "themes": ["theme1", "theme2", ...],  
  "quotes": ["notable quote 1", "notable quote 2", ...],
  "startups": ["company1", "company2", ...],
  "summary": "Brief 2-3 sentence summary"
}

Guidelines:
- key_topics: Main subjects discussed (3-5 topics)
- themes: Broader themes or patterns (2-4 themes)  
- quotes: Memorable, insightful quotes (2-3 max)
- startups: Any companies, startups, or brands mentioned
- summary: Concise overview of the episode

Transcript:
"""
        
        try:
            if self.llm_provider == "openai":
                return self._openai_extract(prompt + text)
            elif self.llm_provider == "anthropic":
                return self._anthropic_extract(prompt + text)
            elif self.llm_provider == "ollama":
                return self._ollama_extract(prompt + text)
        except Exception as e:
            print(f"LLM summarization failed: {e}")
            return self._basic_extraction(text)

    def _openai_extract(self, prompt: str) -> Optional[Dict[str, Any]]:
        """Extract using OpenAI API."""
        with httpx.Client(timeout=120.0) as client:
            response = client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": self.llm_model,
                    "messages": [
                        {"role": "system", "content": "You are an expert at analyzing podcast content. Return valid JSON only."},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.2,
                    "max_tokens": 1000
                }
            )
        
        if response.status_code == 200:
            content = response.json()["choices"][0]["message"]["content"].strip()
            # Extract JSON from response (in case there's extra text)
            json_start = content.find('{')
            json_end = content.rfind('}') + 1
            if json_start >= 0 and json_end > json_start:
                json_str = content[json_start:json_end]
                return json.loads(json_str)
        
        return None

    def _anthropic_extract(self, prompt: str) -> Optional[Dict[str, Any]]:
        """Extract using Anthropic Claude API."""
        # Placeholder for Claude implementation
        return None

    def _ollama_extract(self, prompt: str) -> Optional[Dict[str, Any]]:
        """Extract structured data using Ollama."""
        if not OLLAMA_AVAILABLE:
            print("Ollama not available, using basic extraction")
            return None
            
        try:
            response = ollama.chat(
                model=self.llm_model,
                messages=[
                    {"role": "system", "content": "You are an expert at analyzing podcast content. Return valid JSON only."},
                    {"role": "user", "content": prompt}
                ]
            )
            
            content = response['message']['content'].strip()
            # Extract JSON from response (in case there's extra text)
            json_start = content.find('{')
            json_end = content.rfind('}') + 1
            if json_start >= 0 and json_end > json_start:
                json_str = content[json_start:json_end]
                return json.loads(json_str)
            
            return None
        except Exception as e:
            print(f"Ollama extraction failed: {e}")
            return None

    def _basic_extraction(self, text: str) -> Dict[str, Any]:
        """Basic keyword extraction as fallback."""
        words = text.lower().split()
        word_freq = {}
        for word in words:
            if len(word) > 4 and word.isalpha():
                word_freq[word] = word_freq.get(word, 0) + 1
        
        # Get most frequent words as topics
        key_topics = [word for word, freq in 
                     sorted(word_freq.items(), key=lambda x: x[1], reverse=True)[:5]]
        
        # Simple company extraction (words ending in common suffixes)
        potential_companies = []
        for word in text.split():
            if any(word.lower().endswith(suffix) for suffix in ['inc', 'corp', 'llc', 'labs']):
                potential_companies.append(word)
        
        return {
            "key_topics": key_topics,
            "themes": ["general discussion"],
            "quotes": [],
            "startups": list(set(potential_companies)),
            "summary": "Podcast episode discussion covering various topics."
        }

    def process_all_transcribed(self) -> int:
        """Process all episodes with 'transcribed' status."""
        episodes = self.db.get_episodes_by_status('transcribed')
        processed_count = 0
        
        for episode in episodes:
            print(f"Processing summary for: {episode['title']}")
            if self.generate_summary(episode['id']):
                processed_count += 1
                print(f"✓ Processed: {episode['title']}")
            else:
                print(f"✗ Failed to process: {episode['title']}")
        
        return processed_count
