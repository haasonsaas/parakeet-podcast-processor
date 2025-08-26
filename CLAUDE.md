# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Parakeet Podcast Processor (P³) is an automated podcast processing pipeline that transforms podcasts into structured summaries using Apple Silicon-optimized ML acceleration. The system downloads podcast episodes from RSS feeds, transcribes audio using Parakeet MLX (30x faster than Whisper on Apple Silicon), generates structured summaries with local LLMs via Ollama, and exports to various formats.

Inspired by Tomasz Tunguz's innovative podcast processing system, P³ includes advanced features like AP English teacher-graded blog generation and social media post creation.

## Architecture

The system follows a modular pipeline architecture:

```
RSS → ffmpeg → Parakeet MLX → Ollama → DuckDB → Export
```

### Core Components

- **p3/cli.py**: Main command-line interface with Click framework
- **p3/database.py**: DuckDB-based storage layer with podcast/episode/transcript/summary tables
- **p3/downloader.py**: RSS feed parsing and audio download with ffmpeg normalization
- **p3/transcriber.py**: Audio transcription using Parakeet MLX (Apple Silicon optimized) with Whisper fallback
- **p3/cleaner.py**: LLM-based transcript analysis and structured summary generation via Ollama
- **p3/exporter.py**: Markdown and JSON export functionality
- **p3/writer.py**: Tunguz-inspired blog post generation with AP English grading system

### Database Schema

DuckDB tables with auto-incrementing sequences:
- **podcasts**: Feed metadata (title, rss_url, category)
- **episodes**: Individual episodes with processing status tracking
- **transcripts**: Time-segmented transcript data with speaker identification
- **summaries**: Structured analysis results (topics, themes, quotes, companies, full summary)

## Development Commands

### Setup and Installation
```bash
# Create virtual environment and install
python3 -m venv venv && source venv/bin/activate
pip install -e .

# Initialize P³ (creates directories, database, config)
p3 init
```

### Core Pipeline Commands
```bash
# Download episodes from configured RSS feeds
p3 fetch [--max-episodes N]

# Transcribe audio files (uses Parakeet MLX on Apple Silicon)
p3 transcribe [--model MODEL] [--episode-id ID]

# Generate structured summaries with Ollama
p3 digest [--provider ollama] [--model llama3.2] [--episode-id ID]

# Export daily digests to markdown/JSON
p3 export [--date YYYY-MM-DD] [--format markdown,json]

# Check processing status
p3 status

# Generate Tunguz-style blog posts with AP English grading
p3 write --topic "Your Topic" [--target-grade 91.0] [--date YYYY-MM-DD]
```

### Development Workflow
```bash
# Run the complete demo pipeline
python demo.py

# Run a specific episode through pipeline
p3 transcribe --episode-id 123
p3 digest --episode-id 123

# Export with specific date
p3 export --date 2025-08-26 --format markdown
```

### Code Quality
```bash
# Format code (configured in pyproject.toml)
black .
isort .

# Type checking
mypy p3/

# Run tests (if available)
pytest
```

## Configuration

### Primary Config: config/feeds.yaml
Copy `config/feeds.yaml.example` to `config/feeds.yaml` and configure:
- **feeds**: List of RSS feeds with names, URLs, and categories
- **settings**: Global settings for audio format, transcription models, LLM provider/model, export formats

### Key Settings
- **parakeet_enabled**: Use Parakeet MLX for Apple Silicon optimization (recommended)
- **llm_provider**: "ollama" for local processing (recommended)
- **llm_model**: "llama3.2:latest" or other Ollama models
- **max_episodes_per_feed**: Limit downloads per feed (default: 10)

## Project Structure Insights

### Data Flow
1. **Ingestion**: RSS feeds → episode metadata → audio download
2. **Processing**: ffmpeg normalization → Parakeet transcription → Ollama analysis
3. **Storage**: DuckDB with structured schema and status tracking
4. **Output**: Markdown digests, JSON exports, blog posts, social media content

### Key Architectural Decisions
- **DuckDB**: Fast analytical queries for podcast data analysis
- **Parakeet MLX**: Apple Silicon optimization for 30x transcription speedup
- **Ollama**: 100% local LLM processing for privacy and no API costs
- **Status Tracking**: Episodes progress through downloaded → transcribed → processed states
- **Modular Design**: Each component can run independently with clear interfaces

### File Organization
- **data/**: Audio files, DuckDB database, processing artifacts
- **config/**: YAML configuration files
- **exports/**: Generated digest outputs
- **blog_posts/**: Generated blog content with grading metadata
- **logs/**: Processing logs and error tracking

## Tunguz-Inspired Features

The blog generation system implements Tomasz Tunguz's innovative approaches:
- **AP English Teacher Grading**: Iterative improvement with 91/100 target grade
- **Social Media Generation**: Automatic Twitter and LinkedIn post creation
- **Company Extraction**: CRM-ready startup and company mentions
- **Investment Thesis**: Business intelligence from podcast insights

## Prerequisites

- **macOS with Apple Silicon** (for Parakeet MLX optimization)
- **ffmpeg**: Audio processing (`brew install ffmpeg`)
- **Ollama**: Local LLM server (`https://ollama.com`, then `ollama pull llama3.2`)
- **Python 3.9+**: With virtual environment support

## Performance Expectations

- **Audio Download**: ~30 seconds per episode
- **Parakeet Transcription**: 60 minutes audio → 1 second processing
- **Ollama Analysis**: Full transcript → structured summary in ~10 seconds
- **Total Pipeline**: ~1 minute for complete podcast processing

## Testing and Validation

The project includes a demo script (`demo.py`) that showcases the complete pipeline with performance timing. No formal test suite is currently present - validation is primarily through the demo workflow and manual testing of individual pipeline stages.